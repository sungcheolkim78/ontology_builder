import json
from datetime import datetime
from pathlib import Path

from app.graph import graphdb
from app import ontology
from app.preprocess.embeddings import node_embedding_text
from app.paths import document_dir_for, documents_dir
from app.llm.telemetry import embed_with_telemetry

DOCUMENTS_DIR = documents_dir()

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


def _apply_type_change(type_list: list, element: dict, decision: str) -> None:
    idx = next((i for i, t in enumerate(type_list) if t["name"] == element["name"]), None)
    if decision == "DEPRECATE":
        if idx is not None:
            type_list[idx] = {**type_list[idx], "description": f"[DEPRECATED] {type_list[idx]['description']}"}
    elif idx is not None:
        type_list[idx] = element
    else:
        type_list.append(element)


def _apply_schema_type_changes(schema: dict, changes: list) -> dict:
    node_types = list(schema["node_types"])
    edge_types = list(schema["edge_types"])
    for change in changes:
        target = node_types if change["element_type"] == "node_type" else edge_types
        _apply_type_change(target, change["element"], change["decision"])
    return {"node_types": node_types, "edge_types": edge_types}


def versions_path(stem: str) -> Path:
    return document_dir_for(stem) / "versions.json"


def _load_versions_manifest(stem: str) -> dict:
    path = versions_path(stem)
    if not path.is_file():
        return {"active_version": None, "versions": []}
    return json.loads(path.read_text())


def _save_versions_manifest(stem: str, manifest: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    versions_path(stem).write_text(json.dumps(manifest, ensure_ascii=False))


def list_versions(stem: str) -> list[dict]:
    return _load_versions_manifest(stem)["versions"]


def get_active_version(stem: str) -> int | None:
    return _load_versions_manifest(stem)["active_version"]


def schema_path_for_version(stem: str, version: int) -> Path:
    return document_dir_for(stem) / f"schema_v{version}.json"


def save_schema(stem: str, version: int, schema: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    schema_path_for_version(stem, version).write_text(json.dumps(schema, ensure_ascii=False))


def load_schema(stem: str, version: int) -> dict | None:
    path = schema_path_for_version(stem, version)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def create_schema_version(stem: str, schema: dict, document_type: str = "general") -> int:
    manifest = _load_versions_manifest(stem)
    next_version = max((v["version"] for v in manifest["versions"]), default=0) + 1
    save_schema(stem, next_version, schema)
    manifest["versions"].append(
        {
            "version": next_version,
            "document_type": document_type,
            "created_at": datetime.now().isoformat(),
        }
    )
    manifest["active_version"] = next_version
    _save_versions_manifest(stem, manifest)
    return next_version


def activate_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    if not any(v["version"] == version for v in manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    manifest["active_version"] = version
    _save_versions_manifest(stem, manifest)


def delete_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    remaining = [v for v in manifest["versions"] if v["version"] != version]
    if len(remaining) == len(manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    schema_path_for_version(stem, version).unlink(missing_ok=True)
    graphdb.delete_version_data(stem, version)
    manifest["versions"] = remaining
    if manifest["active_version"] == version:
        manifest["active_version"] = max((v["version"] for v in remaining), default=None)
    _save_versions_manifest(stem, manifest)


def save_document_manifest(stem: str, original_filename: str, converter: str = "anydoc") -> None:
    """Records the per-document info the rest of this module's stem-based
    file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md", and
    which PDF-to-Markdown converter produced that Markdown ("anydoc" or
    "table_aware" -- see app.preprocess.parser). Schema and graph presence are
    deliberately NOT duplicated here -- load_schema and graphdb.has_graph
    already answer those live, so there's nothing to keep in sync."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"original_filename": original_filename, "converter": converter}, ensure_ascii=False)
    )


def load_document_manifest(stem: str) -> dict | None:
    path = document_dir_for(stem) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def discovery_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "discovery.json"


def save_discovery(stem: str, report: dict) -> None:
    """One discovery report per document, not per schema version -- discovery
    is an exploratory, re-runnable read of the document itself, not tied to
    any particular schema/extraction attempt, so overwriting on every run
    (rather than versioning it like schema_v{N}.json) is intentional."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    discovery_path_for(stem).write_text(json.dumps(report, ensure_ascii=False))


def load_discovery(stem: str) -> dict | None:
    path = discovery_path_for(stem)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def summary_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "summary.json"


def save_document_summary(stem: str, summary: str) -> None:
    """One summary per document, overwritten on regeneration -- same
    exploratory-artifact model as discover_ontology/save_discovery above."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    summary_path_for(stem).write_text(json.dumps({"summary": summary}, ensure_ascii=False))


def load_document_summary(stem: str) -> str | None:
    path = summary_path_for(stem)
    if not path.is_file():
        return None
    return json.loads(path.read_text())["summary"]


def embed_nodes(nodes: list) -> list:
    """Attaches an "embedding" vector to each node (label + detail text),
    so graphdb.find_similar_nodes has something to rank against later when
    a question's keywords don't literally match any node's label. Returns
    new dicts rather than mutating the input."""
    if not nodes:
        return []
    model = ontology.get_embedding_model()
    texts = [node_embedding_text(n) for n in nodes]
    vectors = embed_with_telemetry("embed-nodes", model, texts)
    return [{**node, "embedding": vector} for node, vector in zip(nodes, vectors)]


def save_graph(stem: str, graph: dict, version: int = 1) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"], version=version)


def embed_graph(stem: str, version: int = 1) -> int:
    """Embeds this document version's already-extracted nodes in a separate
    pass from extraction, so a large document's LLM extraction call doesn't
    also pay for the embedding call before anything is visible. Reads the
    nodes graphdb already has (written by save_graph with no embedding),
    computes vectors, and updates them in place via graphdb.update_node_embeddings
    -- rerunning this is safe and simply recomputes/overwrites every node's
    embedding."""
    graph = graphdb.load_graph(stem, version=version)
    if graph is None or not graph["nodes"]:
        return 0
    nodes = embed_nodes(graph["nodes"])
    graphdb.update_node_embeddings(stem, nodes, version=version)
    return len(nodes)


def list_schema_stems() -> list[str]:
    if not DOCUMENTS_DIR.is_dir():
        return []
    return [
        d.name
        for d in DOCUMENTS_DIR.iterdir()
        if d.is_dir() and (d / "versions.json").is_file()
    ]


def load_graph(stem: str, version: int = 1) -> dict | None:
    return graphdb.load_graph(stem, version=version)
