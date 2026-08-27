import json
import logging
import os
import re
from pathlib import Path

from app.chat import get_chat_model
from app.embeddings import get_embedding_model, node_embedding_text
from app.telemetry import invoke_with_telemetry, embed_with_telemetry
from app import graphdb
from app.paths import data_dir

GRAPH_DIR = data_dir() / "graph"

logger = logging.getLogger(__name__)

# ~4 chars/token is a conservative rule of thumb. 200_000 was originally sized
# for the default model (gpt-4o-mini, 128k-token context); real OPENROUTER_MODEL
# choices in practice (e.g. Gemini models) commonly have ~1M-token context, and
# real legal/insurance documents routinely exceed 200k chars, so the limit is
# raised 1.5x rather than tuned per-model. Configurable since OPENROUTER_MODEL
# can point at a model with a different context window. Guards against silently
# blowing the context window or getting back truncated/malformed JSON (e.g. an
# edge referencing a node that got cut off mid-response) instead of a clear,
# immediate error.
MAX_DOCUMENT_CHARS = int(os.environ.get("MAX_DOCUMENT_CHARS", 300_000))


def _check_document_length(document_text: str) -> None:
    if len(document_text) > MAX_DOCUMENT_CHARS:
        raise ValueError(
            f"document is too long ({len(document_text)} chars, "
            f"max {MAX_DOCUMENT_CHARS}) to send to the LLM in one call"
        )

DEFAULT_SCHEMA = {
    "node_types": [
        {"name": "Entity", "description": "A generic named entity mentioned in the document."}
    ],
    "edge_types": [
        {
            "name": "RELATED_TO",
            "description": "A generic relationship between two entities.",
            "source": "Entity",
            "target": "Entity",
        }
    ],
}

# Shared by every schema-generation prompt variant below -- the identifier rule
# and output shape are policy, not something that should vary per document type.
_SCHEMA_OUTPUT_INSTRUCTIONS = """Every "name" value (for both node_types and edge_types) MUST be a valid \
identifier: letters, digits, and underscores only, no spaces or other \
characters, and it must start with a letter or underscore (e.g. "JobTitle" \
or "Job_Title", not "Job Title"). This applies even if the document is not \
in English -- transliterate or translate the name into an ASCII identifier.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": [{{"name": "...", "description": "..."}}], \
"edge_types": [{{"name": "...", "description": "...", "source": "<node type name>", \
"target": "<node type name>"}}]}}

Document:
{document}
"""

SCHEMA_PROMPT = """Given the following document, propose an ontology schema for \
extracting entities and relationships from it.

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

# For statutes, contracts, and insurance policies/terms: documents built from a
# formal internal structure (chapters/articles/paragraphs/items, e.g. 장/절/조/항/호)
# and explicitly defined terms reused throughout. The generic SCHEMA_PROMPT above
# tends to propose only surface-level named entities and misses definitions,
# obligations/conditions/exclusions, and cross-references between provisions --
# this variant asks the LLM to look for those specifically, without forcing any of
# them into a schema that doesn't actually have them.
LEGAL_SCHEMA_PROMPT = """Given the following document, propose an ontology schema for \
extracting entities and relationships from it.

This document is a legal or insurance-style document (e.g. statute, contract, \
insurance policy/terms) with a formal internal structure (chapters/articles/\
paragraphs/items, e.g. 장/절/조/항/호) and defined terms reused throughout. When \
proposing node_types and edge_types, look specifically for: defined terms from a \
definitions clause (kept separate from the entities that later use them); parties \
and roles (e.g. insurer/policyholder/insured/beneficiary); obligations, rights, \
and benefits each party has; conditions and exclusions that trigger or bar them \
(e.g. waiting periods, 면책사유); cross-references between the document's own \
provisions (e.g. "제15조에 따라", "전항에도 불구하고"); and, only if worth \
navigating on its own, the document's structural units themselves (e.g. \
"Article"). Only propose types the document actually supports, using its own \
terminology rather than generic labels wholesale.

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

SCHEMA_PROMPTS = {
    "general": SCHEMA_PROMPT,
    "legal": LEGAL_SCHEMA_PROMPT,
}

EXTRACT_PROMPT = """Using this ontology schema:
{schema}

Extract entities and relationships from the following document that conform to \
this schema.

For each node and edge, also include a "detail" field: one or two sentences of \
specific supporting information from the document -- exact conditions, exceptions, \
figures, dates, or phrasing -- that isn't captured by the label/type alone. Omit \
"detail" (or leave it an empty string) if the document has nothing beyond the label \
worth adding.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"nodes": [{{"id": "...", "label": "...", "type": "<a node type name from the schema>", \
"detail": "..."}}], "edges": [{{"source": "<node id>", "target": "<node id>", \
"type": "<an edge type name from the schema>", "detail": "..."}}]}}

Document:
{document}
"""


def parse_json_response(text: str) -> dict:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}")


def generate_schema(document_text: str, document_type: str = "general") -> dict:
    _check_document_length(document_text)
    prompt_template = SCHEMA_PROMPTS.get(document_type)
    if prompt_template is None:
        raise ValueError(f"unknown document_type: {document_type!r}")
    model = get_chat_model()
    response = invoke_with_telemetry(
        "ontology.generate_schema", model, prompt_template.format(document=document_text)
    )
    schema = parse_json_response(response.content)
    if not isinstance(schema.get("node_types"), list) or not isinstance(
        schema.get("edge_types"), list
    ):
        raise ValueError("schema JSON missing node_types/edge_types lists")
    return schema


def extract_graph(document_text: str, schema: dict) -> dict:
    model = get_chat_model()
    prompt = EXTRACT_PROMPT.format(
        schema=json.dumps(schema), document=document_text
    )
    response = invoke_with_telemetry("ontology.extract_graph", model, prompt)
    graph = parse_json_response(response.content)
    if not isinstance(graph.get("nodes"), list) or not isinstance(
        graph.get("edges"), list
    ):
        raise ValueError("extraction JSON missing nodes/edges lists")

    # The LLM occasionally hallucinates an edge endpoint that isn't among
    # its own extracted nodes (e.g. an implied node it never fully emitted,
    # or output truncated mid-list). graphdb.write_graph would reject the
    # whole extraction over one bad edge, so drop just the offending edges
    # here and keep the rest of the graph.
    node_ids = {n["id"] for n in graph["nodes"]}
    valid_edges = []
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            logger.warning(
                "dropping edge with unknown endpoint: %r -> %r (type=%r)",
                edge.get("source"), edge.get("target"), edge.get("type"),
            )
            continue
        valid_edges.append(edge)
    graph["edges"] = valid_edges

    return graph


def graph_dir_for(stem: str) -> Path:
    return GRAPH_DIR / stem


def save_schema(stem: str, schema: dict) -> None:
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema.json").write_text(json.dumps(schema))


def load_schema(stem: str) -> dict | None:
    path = graph_dir_for(stem) / "schema.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save_document_manifest(stem: str, original_filename: str) -> None:
    """Records the one piece of per-document info the rest of this module's
    stem-based file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md". Schema
    and graph presence are deliberately NOT duplicated here -- load_schema
    and graphdb.has_graph already answer those live, so there's nothing to
    keep in sync."""
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"original_filename": original_filename}))


def load_document_manifest(stem: str) -> dict | None:
    path = graph_dir_for(stem) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def embed_nodes(nodes: list) -> list:
    """Attaches an "embedding" vector to each node (label + detail text),
    so graphdb.find_similar_nodes has something to rank against later when
    a question's keywords don't literally match any node's label. Returns
    new dicts rather than mutating the input."""
    if not nodes:
        return []
    model = get_embedding_model()
    texts = [node_embedding_text(n) for n in nodes]
    vectors = embed_with_telemetry("ontology.embed_nodes", model, texts)
    return [{**node, "embedding": vector} for node, vector in zip(nodes, vectors)]


def save_graph(stem: str, graph: dict) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"])


def embed_graph(stem: str) -> int:
    """Embeds this document's already-extracted nodes in a separate pass
    from extraction, so a large document's LLM extraction call doesn't also
    pay for the embedding call before anything is visible. Reads the nodes
    graphdb already has (written by save_graph with no embedding), computes
    vectors, and updates them in place via graphdb.update_node_embeddings --
    rerunning this is safe and simply recomputes/overwrites every node's
    embedding."""
    graph = graphdb.load_graph(stem)
    if graph is None or not graph["nodes"]:
        return 0
    nodes = embed_nodes(graph["nodes"])
    graphdb.update_node_embeddings(stem, nodes)
    return len(nodes)


def list_schema_stems() -> list[str]:
    if not GRAPH_DIR.is_dir():
        return []
    return [
        d.name
        for d in GRAPH_DIR.iterdir()
        if d.is_dir() and (d / "schema.json").is_file()
    ]


def load_graph(stem: str) -> dict | None:
    return graphdb.load_graph(stem)
