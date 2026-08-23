import json
import re
from pathlib import Path

from app.chat import get_chat_model
from app.telemetry import invoke_with_telemetry
from app import graphdb

GRAPH_DIR = Path(__file__).parent.parent / "data" / "graph"

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

SCHEMA_PROMPT = """Given the following document, propose an ontology schema for \
extracting entities and relationships from it.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": [{{"name": "...", "description": "..."}}], \
"edge_types": [{{"name": "...", "description": "...", "source": "<node type name>", \
"target": "<node type name>"}}]}}

Document:
{document}
"""

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


def generate_schema(document_text: str) -> dict:
    model = get_chat_model()
    response = invoke_with_telemetry(
        "ontology.generate_schema", model, SCHEMA_PROMPT.format(document=document_text)
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


def save_graph(stem: str, graph: dict) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"])


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
