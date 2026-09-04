import json
import os

from app import graphdb
from app.chat import get_chat_model, to_langchain_messages
from app.embeddings import get_embedding_model
from app.ontology import parse_json_response
from app.schema_validation import normalize_schema
from app.telemetry import invoke_with_telemetry, embed_with_telemetry

# How many of a type's own nodes to keep when keyword matching finds none
# and search falls back to embedding similarity -- a ranked cutoff instead
# of dumping every instance of the type into the context.
EMBEDDING_FALLBACK_TOP_K = 5

# Comparison operators a property_filter's "operator" field may use --
# see graphdb.find_nodes_by_property, which this maps 1:1 onto.
PROPERTY_FILTER_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}

_CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

# Config-driven and permissive by default (unset = no filtering at all) --
# design spec section 7.3: a node/edge written before this feature existed
# has no `confidence` field at all and must always be included regardless of
# this threshold, since a stricter-than-before default would silently
# regress retrieval for every document extracted before this design.
MIN_CONFIDENCE = os.environ.get("GRAPHRAG_MIN_CONFIDENCE")


def _passes_confidence(item: dict) -> bool:
    if not MIN_CONFIDENCE:
        return True
    confidence = item.get("confidence")
    if confidence is None:
        return True
    return _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(MIN_CONFIDENCE, 0)

ANALYSIS_PROMPT = """Given this ontology schema and a user's question, do three things:

1. Decide which node types and edge types (using their exact names from the schema) are \
relevant to answering the question. Only use type names that appear in the schema below. \
If nothing in the schema seems relevant, return empty lists.

2. For each relevant node type, extract the key entities, names, or specific terms \
mentioned in the question that might refer to an instance of that node type. Only include \
a type in "keywords" if the question actually names a specific instance of it -- omit \
types with no matching terms.

3. For each relevant node type whose schema entry below declares typed "properties" \
(an object mapping property names to datatype declarations), decide whether the question \
implies a specific comparison against one of those declared properties -- e.g. "50% \
이상인 보장" implies a Coverage property named "amount" compared >= 50. If so, include a \
"property_filters" entry for that type: {{"property": "<declared property name>", \
"operator": "eq|ne|gt|gte|lt|lte", "value": "<comparison value as a string>"}}. Only use \
property names actually declared for that exact type in the schema below, never one you \
invent. Omit a type from "property_filters" entirely if the question doesn't imply a \
specific property comparison for it, or if that type declares no properties.

Schema:
{schema}

Question:
{question}

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": ["..."], "edge_types": ["..."], "keywords": {{"TypeName": ["term1", "term2"]}}, \
"property_filters": {{"TypeName": {{"property": "...", "operator": "eq|ne|gt|gte|lt|lte", "value": "..."}}}}}}
"""


def embed_query(question: str) -> list:
    model = get_embedding_model()
    vectors = embed_with_telemetry("graphrag.embed_query", model, [question])
    return vectors[0]


def analyze_question(question: str, schema: dict) -> dict:
    """Single combined LLM call replacing what used to be two sequential
    calls (type analysis, then keyword extraction) -- both only need the
    schema + question as input, so one round-trip determines relevant
    node/edge types and, for each relevant node type, any keywords naming a
    specific instance of it (e.g. {"Person": ["Ada Lovelace"]}), so
    find_relevant_nodes can match each term only against its own type."""
    model = get_chat_model()
    response = invoke_with_telemetry(
        "graphrag.analyze_question",
        model,
        ANALYSIS_PROMPT.format(schema=json.dumps(schema), question=question),
    )
    result = parse_json_response(response.content)
    if not isinstance(result.get("node_types"), list) or not isinstance(
        result.get("edge_types"), list
    ):
        raise ValueError("question analysis did not return node_types/edge_types lists")

    valid_node_types = {nt["name"] for nt in schema.get("node_types", [])}
    valid_edge_types = {et["name"] for et in schema.get("edge_types", [])}
    node_types = [t for t in result["node_types"] if t in valid_node_types]
    edge_types = [t for t in result["edge_types"] if t in valid_edge_types]

    keywords_raw = result.get("keywords")
    keywords = {}
    if isinstance(keywords_raw, dict):
        keywords = {
            t: kws
            for t, kws in keywords_raw.items()
            if t in node_types and isinstance(kws, list)
        }

    # Declared properties per node type, from the normalized schema -- used
    # only to validate property_filters below (analyze_question's own
    # node_types/edge_types validation above stays against the raw `schema`
    # dict, unchanged, so this doesn't alter existing behavior for a schema
    # with no typed properties at all).
    declared_properties_by_type = {
        t["name"]: t.get("properties", {})
        for t in normalize_schema(schema).get("node_types", [])
    }
    property_filters_raw = result.get("property_filters")
    property_filters = {}
    if isinstance(property_filters_raw, dict):
        for type_name, filt in property_filters_raw.items():
            if type_name not in node_types or not isinstance(filt, dict):
                continue
            prop_name = filt.get("property")
            operator = filt.get("operator")
            value = filt.get("value")
            if (
                prop_name in declared_properties_by_type.get(type_name, {})
                and operator in PROPERTY_FILTER_OPERATORS
                and value is not None
            ):
                property_filters[type_name] = {
                    "property": prop_name,
                    "operator": operator,
                    "value": str(value),
                }

    return {
        "node_types": node_types,
        "edge_types": edge_types,
        "keywords": keywords,
        "property_filters": property_filters,
    }


def _format_evidence_suffix(item: dict) -> str:
    # `detail` is an LLM-written paraphrase kept for readability;
    # evidence_text is the exact source wording a user can verify against
    # raw.md -- shown alongside detail, never instead of it (design spec
    # section 7.2). source_section can be present without evidence_text
    # (they're set independently -- see app.ontology's extraction
    # normalization), so each is checked on its own.
    bits = []
    if item.get("evidence_text"):
        bits.append(f"근거: {item['evidence_text']}")
    if item.get("source_section"):
        bits.append(f"출처: {item['source_section']}")
    return f" [{'; '.join(bits)}]" if bits else ""


def _format_node_line(node: dict) -> str:
    line = f"- {node['label']} ({node['type']})"
    if node.get("detail"):
        line += f": {node['detail']}"
    line += _format_evidence_suffix(node)
    return line


def _format_edge_line(nodes_by_id: dict, edge: dict) -> str:
    line = f"- {nodes_by_id[edge['source']]['label']} --{edge['type']}--> {nodes_by_id[edge['target']]['label']}"
    if edge.get("detail"):
        line += f": {edge['detail']}"
    line += _format_evidence_suffix(edge)
    return line


def _build_context_text(nodes: list, edges: list) -> str | None:
    if not nodes:
        return None

    # nodes_by_id stays unfiltered so an edge's source/target label lookup
    # never fails even when that endpoint's own node line was dropped by the
    # confidence filter below.
    nodes_by_id = {n["id"]: n for n in nodes}
    node_lines = [_format_node_line(n) for n in nodes if _passes_confidence(n)]
    if not node_lines:
        return None
    edge_lines = [_format_edge_line(nodes_by_id, e) for e in edges if _passes_confidence(e)]

    parts = ["Entities:", *node_lines]
    if edge_lines:
        parts += ["", "Relations:", *edge_lines]
    return "\n".join(parts)


def search_graph(question: str, schema: dict, stem: str, version: int = 1, hops: int = 1) -> dict:
    """Schema-aware graph search: determine which node/edge types (from the
    document's own schema) are relevant to the question, then search actual
    node/edge instances of those types via LadybugDB, then expand `hops`
    from whatever matched. Returns the determined types (for a "here's what
    I looked for" preview) and the matched nodes/edges themselves (so the
    frontend can link the answer back to specific graph entities) alongside
    the resulting context text, or None/empty if nothing was found at any
    stage."""
    analysis = analyze_question(question, schema)
    node_types = analysis["node_types"]
    edge_types = analysis["edge_types"]
    keywords = analysis["keywords"]
    property_filters = analysis["property_filters"]

    if not node_types and not edge_types:
        return {
            "node_types": [],
            "edge_types": [],
            "context": None,
            "related_nodes": [],
            "related_edges": [],
        }

    # Ordered (not just a set) so the relevance signal each match tier
    # already carries -- keyword hits, then embedding-similarity rank for
    # the fallback case -- survives into related_nodes' order below, instead
    # of being discarded by a plain set's undefined iteration order.
    matched_node_ids = []
    matched_node_id_set = set()

    def _add_matched(node_id):
        if node_id not in matched_node_id_set:
            matched_node_id_set.add(node_id)
            matched_node_ids.append(node_id)

    if node_types:
        query_embedding = None
        for node_type in node_types:
            type_ids = graphdb.find_relevant_nodes(
                stem, {node_type: keywords.get(node_type, [])}, [node_type], version=version
            )
            if not type_ids and node_type in property_filters:
                # Keyword matching found nothing, but the question implies a
                # comparison against one of this type's own declared typed
                # properties (e.g. "50% 이상인 보장") -- neither a label
                # substring match nor embedding similarity can answer a
                # threshold/exact-value question like this, so it's tried
                # before falling further to the embedding fallback below
                # (design spec section 7.1).
                filt = property_filters[node_type]
                type_ids = graphdb.find_nodes_by_property(
                    stem, node_type, filt["property"], filt["operator"], filt["value"],
                    version=version,
                )
            if not type_ids:
                # No keyword was extracted for this type, or the extracted
                # keyword didn't match any instance -- either the question
                # names nothing concrete (a category question like "what are
                # the responsibilities?") or the question/document languages
                # don't literally overlap. The type analysis step already
                # established this type is relevant, so rank that type's
                # own nodes by embedding similarity to the question instead
                # of dumping every instance of it into the context.
                if query_embedding is None:
                    query_embedding = embed_query(question)
                type_ids = graphdb.find_similar_nodes(
                    stem, node_type, query_embedding, top_k=EMBEDDING_FALLBACK_TOP_K,
                    version=version,
                )
            if not type_ids:
                # No embedding was stored for this type either -- most
                # likely it was extracted before embeddings existed, so
                # there's nothing to rank. Last resort: every instance of
                # just this type, same fallback embeddings were meant to
                # narrow (see EMBEDDING_FALLBACK_TOP_K above).
                type_ids = graphdb.all_nodes_of_types(stem, [node_type], version=version)
            for node_id in type_ids:
                _add_matched(node_id)

    if edge_types:
        matched_edges = graphdb.find_matching_edges(
            stem, edge_types, matched_node_id_set, version=version
        )
        for edge in matched_edges:
            _add_matched(edge["source"])
            _add_matched(edge["target"])

        if not matched_node_ids:
            # No node was matched at all (node_types was empty, or every
            # determined node type turned out to have zero real instances) --
            # fall back to every edge of the determined type rather than
            # reporting "not found" when the graph actually has data.
            for edge in graphdb.all_edges_of_types(stem, edge_types, version=version):
                _add_matched(edge["source"])
                _add_matched(edge["target"])

    related_nodes, related_edges = graphdb.expand_hops(
        stem, matched_node_id_set, hops, version=version
    )
    # Put directly-matched nodes first, in the relevance order they were
    # matched in above; nodes only pulled in by hop expansion (never
    # directly matched) sort after, in whatever order the DB returned them.
    match_rank = {node_id: rank for rank, node_id in enumerate(matched_node_ids)}
    related_nodes.sort(key=lambda n: match_rank.get(n["id"], len(match_rank)))
    context = _build_context_text(related_nodes, related_edges)
    return {
        "node_types": node_types,
        "edge_types": edge_types,
        "context": context,
        "related_nodes": related_nodes,
        "related_edges": related_edges,
    }


def answer_question(
    messages: list[dict], schema: dict, stem: str, version: int = 1, hops: int = 1
) -> dict:
    """Runs search_graph() against `messages[-1]`'s content, then answers
    with the full `messages` history augmented by whatever context was
    found (or a fixed "no context found" reply if nothing was). Shared by
    `/api/chat`'s schema+graph-available path and the goldenset per-question
    answer endpoint, so the two ways of asking this app a question against
    a document's graph can never silently drift apart."""
    result = search_graph(messages[-1]["content"], schema, stem, version=version, hops=hops)
    if result["context"]:
        augmented = [
            {
                "role": "system",
                "content": f"다음은 문서에서 추출된 관련 정보입니다:\n{result['context']}",
            }
        ] + messages
        model = get_chat_model()
        response = invoke_with_telemetry("chat.answer", model, to_langchain_messages(augmented))
        content = response.content
    else:
        content = "관련된 내용을 찾을 수 없습니다."
    return {
        "content": content,
        "node_types": result["node_types"],
        "edge_types": result["edge_types"],
        "related_nodes": result["related_nodes"],
        "related_edges": result["related_edges"],
    }
