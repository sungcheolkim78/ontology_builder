import json

import networkx as nx

from app.chat import get_chat_model
from app.ontology import parse_json_response

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
    response = model.invoke(KEYWORD_PROMPT.format(question=question))
    keywords = parse_json_response(response.content)
    if not isinstance(keywords, list):
        raise ValueError("keyword extraction did not return a JSON list")
    return keywords


def determine_relevant_types(question: str, schema: dict) -> dict:
    model = get_chat_model()
    response = model.invoke(
        TYPE_ANALYSIS_PROMPT.format(schema=json.dumps(schema), question=question)
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


def format_type_preview(node_types: list, edge_types: list) -> str:
    node_part = ", ".join(node_types) if node_types else "없음"
    edge_part = ", ".join(edge_types) if edge_types else "없음"
    return f"[관련 타입 분석] 노드: {node_part} / 엣지: {edge_part}"


def find_relevant_nodes(nodes: list, keywords: list, allowed_types: list | None = None) -> list:
    lowered_keywords = [k.lower() for k in keywords]
    allowed = set(allowed_types) if allowed_types is not None else None
    matched = []
    for node in nodes:
        if allowed is not None and node["type"] not in allowed:
            continue
        label = node["label"].lower()
        if any(kw in label or label in kw for kw in lowered_keywords):
            matched.append(node["id"])
    return matched


def find_matching_edges(edges: list, allowed_types: list, matched_node_ids: set) -> list:
    allowed = set(allowed_types)
    matched = []
    for edge in edges:
        if edge["type"] not in allowed:
            continue
        if edge["source"] in matched_node_ids or edge["target"] in matched_node_ids:
            matched.append(edge)
    return matched


def all_nodes_of_types(nodes: list, allowed_types: list) -> list:
    allowed = set(allowed_types)
    return [n["id"] for n in nodes if n["type"] in allowed]


def all_edges_of_types(edges: list, allowed_types: list) -> list:
    allowed = set(allowed_types)
    return [e for e in edges if e["type"] in allowed]


def _build_graph(graph_data: dict) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in graph_data["nodes"]:
        g.add_node(node["id"])
    for edge in graph_data["edges"]:
        g.add_edge(edge["source"], edge["target"], type=edge["type"])
    return g


def _build_context_text(graph_data: dict, seed_ids: set, hops: int) -> str | None:
    if not seed_ids:
        return None

    g = _build_graph(graph_data)
    collected = set()
    for seed in seed_ids:
        if seed not in g:
            continue
        ego = nx.ego_graph(g, seed, radius=max(hops, 0), undirected=True)
        collected.update(ego.nodes())

    if not collected:
        return None

    nodes_by_id = {n["id"]: n for n in graph_data["nodes"]}
    subgraph = g.subgraph(collected)

    node_lines = [
        f"- {nodes_by_id[nid]['label']} ({nodes_by_id[nid]['type']})"
        for nid in collected
        if nid in nodes_by_id
    ]
    edge_lines = [
        f"- {nodes_by_id[u]['label']} --{data['type']}--> {nodes_by_id[v]['label']}"
        for u, v, data in subgraph.edges(data=True)
        if u in nodes_by_id and v in nodes_by_id
    ]

    parts = ["Entities:", *node_lines]
    if edge_lines:
        parts += ["", "Relations:", *edge_lines]
    return "\n".join(parts)


def search_graph(question: str, schema: dict, graph_data: dict, hops: int = 1) -> dict:
    """Schema-aware graph search: determine which node/edge types (from the
    document's own schema) are relevant to the question, then search actual
    node/edge instances of those types, then expand `hops` from whatever
    matched. Returns the determined types (for a "here's what I looked for"
    preview) alongside the resulting context text, or None if nothing was
    found at any stage."""
    types = determine_relevant_types(question, schema)
    node_types = types["node_types"]
    edge_types = types["edge_types"]

    if not node_types and not edge_types:
        return {"node_types": [], "edge_types": [], "context": None}

    keywords = extract_keywords(question)
    matched_node_ids = set(
        find_relevant_nodes(graph_data["nodes"], keywords, allowed_types=node_types)
    )

    if edge_types:
        matched_edges = find_matching_edges(graph_data["edges"], edge_types, matched_node_ids)
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
        matched_node_ids = set(all_nodes_of_types(graph_data["nodes"], node_types))
        if edge_types:
            for edge in all_edges_of_types(graph_data["edges"], edge_types):
                matched_node_ids.add(edge["source"])
                matched_node_ids.add(edge["target"])

    context = _build_context_text(graph_data, matched_node_ids, hops)
    return {"node_types": node_types, "edge_types": edge_types, "context": context}
