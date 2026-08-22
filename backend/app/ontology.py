import json
import re
from pathlib import Path

from app.chat import get_chat_model

GRAPH_DIR = Path(__file__).parent.parent / "data" / "graph"

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

Respond with ONLY valid JSON in this exact shape, no other text:
{{"nodes": [{{"id": "...", "label": "...", "type": "<a node type name from the schema>"}}], \
"edges": [{{"source": "<node id>", "target": "<node id>", \
"type": "<an edge type name from the schema>"}}]}}

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
    response = model.invoke(SCHEMA_PROMPT.format(document=document_text))
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
    response = model.invoke(prompt)
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
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "nodes.json").write_text(json.dumps(graph["nodes"]))
    (d / "edges.json").write_text(json.dumps(graph["edges"]))


def load_graph(stem: str) -> dict | None:
    d = graph_dir_for(stem)
    nodes_path = d / "nodes.json"
    edges_path = d / "edges.json"
    if not nodes_path.is_file() or not edges_path.is_file():
        return None
    return {
        "nodes": json.loads(nodes_path.read_text()),
        "edges": json.loads(edges_path.read_text()),
    }
