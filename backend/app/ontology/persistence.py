import shutil
from datetime import datetime

from app.graph import graphdb
from app.llm.calls import embed
from app.preprocess.embeddings import node_embedding_text
from app.utils.paths import (  # noqa: F401 -- re-exported: callers import these from here
    discovery_path_for,
    document_dir_for,
    document_manifest_path,
    documents_dir,
    schema_path_for_version,
    summary_path_for,
    versions_path,
)
from app.utils.store import locked, read_json, write_json

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


def _load_versions_manifest(stem: str) -> dict:
    # A fresh default on every call: callers append to its "versions" list.
    return read_json(versions_path(stem), {"active_version": None, "versions": []})


def _save_versions_manifest(stem: str, manifest: dict) -> None:
    write_json(versions_path(stem), manifest)


def list_versions(stem: str) -> list[dict]:
    return _load_versions_manifest(stem)["versions"]


def get_active_version(stem: str) -> int | None:
    return _load_versions_manifest(stem)["active_version"]


def save_schema(stem: str, version: int, schema: dict) -> None:
    write_json(schema_path_for_version(stem, version), schema)


def load_schema(stem: str, version: int) -> dict | None:
    return read_json(schema_path_for_version(stem, version))


def create_schema_version(stem: str, schema: dict, document_type: str = "general") -> int:
    with locked(stem):
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
    with locked(stem):
        manifest = _load_versions_manifest(stem)
        if not any(v["version"] == version for v in manifest["versions"]):
            raise ValueError(f"version {version} not found for {stem!r}")
        manifest["active_version"] = version
        _save_versions_manifest(stem, manifest)


def delete_version(stem: str, version: int) -> None:
    with locked(stem):
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


class SchemaNotFound(LookupError):
    """The document has no usable active schema: none was ever generated, or
    the active version's file is gone."""


class OntologyNotExtracted(LookupError):
    """The document's active schema has no extracted graph yet."""


def active_schema(stem: str) -> tuple[int, dict]:
    """(version, schema) for the document's active schema version. Raises
    SchemaNotFound if there is no active version or its schema file is
    missing -- the one place that rule lives, so no caller can pass a
    None schema on to a model call."""
    version = get_active_version(stem)
    schema = load_schema(stem, version) if version is not None else None
    if schema is None:
        raise SchemaNotFound(stem)
    return version, schema


def active_ontology(stem: str, load_graph: bool = True) -> tuple[int, dict, dict | None]:
    """(version, schema, graph) for the document's active schema version and
    the graph extracted against it. Raises SchemaNotFound (see active_schema)
    or OntologyNotExtracted. With load_graph=False the graph's existence is
    checked but it is not read and None is returned in its place -- for a
    caller on a hot path (every chat message) that only needs to know whether
    there is one."""
    version, schema = active_schema(stem)
    if load_graph:
        graph = graphdb.load_graph(stem, version=version)
        if graph is None:
            raise OntologyNotExtracted(stem)
        return version, schema, graph
    if not graphdb.has_graph(stem, version=version):
        raise OntologyNotExtracted(stem)
    return version, schema, None


def save_document_manifest(stem: str, original_filename: str, converter: str = "anydoc") -> None:
    """Records the per-document info the rest of this module's stem-based
    file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md", and
    which PDF-to-Markdown converter produced that Markdown ("anydoc" or
    "table_aware" -- see app.preprocess.parser). Schema and graph presence are
    deliberately NOT duplicated here -- load_schema and graphdb.has_graph
    already answer those live, so there's nothing to keep in sync."""
    with locked(stem):
        write_json(
            document_manifest_path(stem),
            {"original_filename": original_filename, "converter": converter},
        )


def load_document_manifest(stem: str) -> dict | None:
    return read_json(document_manifest_path(stem))


def update_document_manifest(stem: str, **updates) -> dict:
    """Partial update over save_document_manifest's always-overwrite shape --
    loads whatever's already there (or {} for a document with no manifest
    yet), applies only the fields the caller actually passed (None values are
    treated as "not provided", not "clear this field"), and writes the merged
    result back. Backs the manifest-edit route, which lets a user correct a
    field (e.g. original_filename) without resupplying the whole manifest."""
    with locked(stem):
        manifest = load_document_manifest(stem) or {}
        manifest.update({k: v for k, v in updates.items() if v is not None})
        write_json(document_manifest_path(stem), manifest)
        return manifest


def delete_document(stem: str) -> None:
    """Removes a document entirely: its graph rows for every schema version
    it ever had (graph data lives in the shared LadybugDB, keyed by
    (source_document, version), not inside the document's own folder --
    see delete_version, which does the same per-version cleanup), then the
    whole documents/{stem}/ folder (raw.md, manifest.json, chunks.json,
    schema_v{N}.json, ...). Unlike delete_version there's no remaining
    version to fall back to -- the document is gone."""
    for v in list_versions(stem):
        graphdb.delete_version_data(stem, v["version"])
    shutil.rmtree(document_dir_for(stem), ignore_errors=True)


def save_discovery(stem: str, report: dict) -> None:
    """One discovery report per document, not per schema version -- discovery
    is an exploratory, re-runnable read of the document itself, not tied to
    any particular schema/extraction attempt, so overwriting on every run
    (rather than versioning it like schema_v{N}.json) is intentional."""
    write_json(discovery_path_for(stem), report)


def load_discovery(stem: str) -> dict | None:
    return read_json(discovery_path_for(stem))


def save_document_summary(stem: str, summary: str) -> None:
    """One summary per document, overwritten on regeneration -- same
    exploratory-artifact model as discover_ontology/save_discovery above."""
    write_json(summary_path_for(stem), {"summary": summary})


def load_document_summary(stem: str) -> str | None:
    stored = read_json(summary_path_for(stem))
    return None if stored is None else stored["summary"]


def embed_nodes(nodes: list) -> list:
    """Attaches an "embedding" vector to each node (label + detail text),
    so graphdb.find_similar_nodes has something to rank against later when
    a question's keywords don't literally match any node's label. Returns
    new dicts rather than mutating the input."""
    if not nodes:
        return []
    texts = [node_embedding_text(n) for n in nodes]
    vectors = embed("embed-nodes", texts)
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
    if not documents_dir().is_dir():
        return []
    return [
        d.name
        for d in documents_dir().iterdir()
        if d.is_dir() and (d / "versions.json").is_file()
    ]


def load_graph(stem: str, version: int = 1) -> dict | None:
    return graphdb.load_graph(stem, version=version)
