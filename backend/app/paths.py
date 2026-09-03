import os
from pathlib import Path


def data_dir() -> Path:
    """Base directory for parsed documents, schemas, and the graph DB.
    Defaults to backend/data, overridable via ONTOLOGY_DATA_DIR so the test
    suite can point at a throwaway directory instead of the real one (see
    tests/conftest.py) -- test fixtures delete and recreate this tree, and
    running them against the real path corrupts whatever's actually been
    extracted so far."""
    override = os.environ.get("ONTOLOGY_DATA_DIR")
    return Path(override) if override else Path(__file__).parent.parent / "data"


def documents_dir() -> Path:
    """Parent of every per-document folder (see document_dir_for). Sibling
    of graph/ (the shared graph DB) and domain_schemas/ (cross-document
    schemas) -- documents/ holds only per-document artifacts."""
    return data_dir() / "documents"


def document_dir_for(stem: str) -> Path:
    """The single folder holding everything about one document: raw.md,
    manifest.json, summary.json, discovery.json, chunks.json, versions.json,
    schema_v{N}.json. Centralizing this here (rather than each module
    computing its own path) is what lets a new per-document artifact kind
    be added as just another file under this folder, with no new top-level
    data/ directory and no new helper elsewhere."""
    return documents_dir() / stem
