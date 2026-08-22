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


def extract_keywords(question: str) -> list:
    model = get_chat_model()
    response = model.invoke(KEYWORD_PROMPT.format(question=question))
    keywords = parse_json_response(response.content)
    if not isinstance(keywords, list):
        raise ValueError("keyword extraction did not return a JSON list")
    return keywords


def find_relevant_nodes(nodes: list, keywords: list) -> list:
    lowered_keywords = [k.lower() for k in keywords]
    matched = []
    for node in nodes:
        label = node["label"].lower()
        if any(kw in label or label in kw for kw in lowered_keywords):
            matched.append(node["id"])
    return matched


def _build_graph(graph_data: dict) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in graph_data["nodes"]:
        g.add_node(node["id"])
    for edge in graph_data["edges"]:
        g.add_edge(edge["source"], edge["target"], type=edge["type"])
    return g


def retrieve_graph_context(graph_data: dict, keywords: list, hops: int = 1) -> str | None:
    seed_ids = find_relevant_nodes(graph_data["nodes"], keywords)
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
