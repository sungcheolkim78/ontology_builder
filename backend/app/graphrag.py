import json

from app import graphdb
from app.chat import get_chat_model
from app.ontology import parse_json_response
from app.telemetry import invoke_with_telemetry

KEYWORD_PROMPT = """Extract the key entities, names, or specific terms mentioned in this \
question that might refer to nodes in a knowledge graph.

Respond with ONLY a JSON array of strings, no other text. If there are no specific \
entities, respond with [].

Question:
{question}
"""

TYPE_ANALYSIS_PROMPT = """Given this ontology schema and a user's question, decide which \
node types and edge types (using their exact names from the schema) are relevant to \
answering the question. Only use type names that appear in the schema below. If nothing \
in the schema seems relevant, return empty lists.

Schema:
{schema}

Question:
{question}

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": ["..."], "edge_types": ["..."]}}
"""


def extract_keywords(question: str) -> list:
    model = get_chat_model()
    response = invoke_with_telemetry(
        "graphrag.extract_keywords", model, KEYWORD_PROMPT.format(question=question)
    )
    keywords = parse_json_response(response.content)
    if not isinstance(keywords, list):
        raise ValueError("keyword extraction did not return a JSON list")
    return keywords


def determine_relevant_types(question: str, schema: dict) -> dict:
    model = get_chat_model()
    response = invoke_with_telemetry(
        "graphrag.determine_types",
        model,
        TYPE_ANALYSIS_PROMPT.format(schema=json.dumps(schema), question=question),
    )
    result = parse_json_response(response.content)
    if not isinstance(result.get("node_types"), list) or not isinstance(
        result.get("edge_types"), list
    ):
        raise ValueError("type analysis did not return node_types/edge_types lists")

    valid_node_types = {nt["name"] for nt in schema.get("node_types", [])}
    valid_edge_types = {et["name"] for et in schema.get("edge_types", [])}
    return {
        "node_types": [t for t in result["node_types"] if t in valid_node_types],
        "edge_types": [t for t in result["edge_types"] if t in valid_edge_types],
    }


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
    types = determine_relevant_types(question, schema)
    node_types = types["node_types"]
    edge_types = types["edge_types"]

    if not node_types and not edge_types:
        return {
            "node_types": [],
            "edge_types": [],
            "context": None,
            "related_nodes": [],
            "related_edges": [],
        }

    keywords = extract_keywords(question)
    matched_node_ids = set(graphdb.find_relevant_nodes(stem, keywords, node_types))

    if edge_types:
        matched_edges = graphdb.find_matching_edges(stem, edge_types, matched_node_ids)
        for edge in matched_edges:
            matched_node_ids.add(edge["source"])
            matched_node_ids.add(edge["target"])

    if not matched_node_ids:
        # Keyword matching found no specific instance -- either the question
        # names nothing concrete (a category question like "what are the
        # responsibilities?") or the question/document languages don't
        # literally overlap. The type analysis step already established
        # these types are relevant, so fall back to every instance of them
        # rather than reporting "not found" when the graph actually has data.
        matched_node_ids = set(graphdb.all_nodes_of_types(stem, node_types))
        if edge_types:
            for edge in graphdb.all_edges_of_types(stem, edge_types):
                matched_node_ids.add(edge["source"])
                matched_node_ids.add(edge["target"])

    related_nodes, related_edges = graphdb.expand_hops(stem, matched_node_ids, hops)
    context = _build_context_text(related_nodes, related_edges)
    return {
        "node_types": node_types,
        "edge_types": edge_types,
        "context": context,
        "related_nodes": related_nodes,
        "related_edges": related_edges,
    }
