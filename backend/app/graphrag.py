import json

from app import graphdb
from app.chat import get_chat_model
from app.embeddings import get_embedding_model
from app.ontology import parse_json_response
from app.telemetry import invoke_with_telemetry, embed_with_telemetry

# How many of a type's own nodes to keep when keyword matching finds none
# and search falls back to embedding similarity -- a ranked cutoff instead
# of dumping every instance of the type into the context.
EMBEDDING_FALLBACK_TOP_K = 5

ANALYSIS_PROMPT = """Given this ontology schema and a user's question, do two things:

1. Decide which node types and edge types (using their exact names from the schema) are \
relevant to answering the question. Only use type names that appear in the schema below. \
If nothing in the schema seems relevant, return empty lists.

2. For each relevant node type, extract the key entities, names, or specific terms \
mentioned in the question that might refer to an instance of that node type. Only include \
a type in "keywords" if the question actually names a specific instance of it -- omit \
types with no matching terms.

Schema:
{schema}

Question:
{question}

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": ["..."], "edge_types": ["..."], "keywords": {{"TypeName": ["term1", "term2"]}}}}
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

    return {"node_types": node_types, "edge_types": edge_types, "keywords": keywords}


def _format_node_line(node: dict) -> str:
    line = f"- {node['label']} ({node['type']})"
    if node.get("detail"):
        line += f": {node['detail']}"
    return line


def _format_edge_line(nodes_by_id: dict, edge: dict) -> str:
    line = f"- {nodes_by_id[edge['source']]['label']} --{edge['type']}--> {nodes_by_id[edge['target']]['label']}"
    if edge.get("detail"):
        line += f": {edge['detail']}"
    return line


def _build_context_text(nodes: list, edges: list) -> str | None:
    if not nodes:
        return None

    nodes_by_id = {n["id"]: n for n in nodes}
    node_lines = [_format_node_line(n) for n in nodes]
    edge_lines = [_format_edge_line(nodes_by_id, e) for e in edges]

    parts = ["Entities:", *node_lines]
    if edge_lines:
        parts += ["", "Relations:", *edge_lines]
    return "\n".join(parts)


def search_graph(question: str, schema: dict, stem: str, hops: int = 1) -> dict:
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
                stem, {node_type: keywords.get(node_type, [])}, [node_type]
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
                    stem, node_type, query_embedding, top_k=EMBEDDING_FALLBACK_TOP_K
                )
            if not type_ids:
                # No embedding was stored for this type either -- most
                # likely it was extracted before embeddings existed, so
                # there's nothing to rank. Last resort: every instance of
                # just this type, same fallback embeddings were meant to
                # narrow (see EMBEDDING_FALLBACK_TOP_K above).
                type_ids = graphdb.all_nodes_of_types(stem, [node_type])
            for node_id in type_ids:
                _add_matched(node_id)

    if edge_types:
        matched_edges = graphdb.find_matching_edges(stem, edge_types, matched_node_id_set)
        for edge in matched_edges:
            _add_matched(edge["source"])
            _add_matched(edge["target"])

        if not matched_node_ids:
            # No node was matched at all (node_types was empty, or every
            # determined node type turned out to have zero real instances) --
            # fall back to every edge of the determined type rather than
            # reporting "not found" when the graph actually has data.
            for edge in graphdb.all_edges_of_types(stem, edge_types):
                _add_matched(edge["source"])
                _add_matched(edge["target"])

    related_nodes, related_edges = graphdb.expand_hops(stem, matched_node_id_set, hops)
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
