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
