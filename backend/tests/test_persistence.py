"""Tests for app.ontology.persistence's read-modify-write operations under
concurrent callers: two requests for one document at once must not lose an
update."""

import shutil
import threading
import time

import pytest

from app.ontology import persistence
from app.preprocess.parser import DATA_DIR

STEM = "doc_raw"


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def _slow(real):
    def wrapper(*args, **kwargs):
        value = real(*args, **kwargs)
        time.sleep(0.005)
        return value

    return wrapper


@pytest.fixture
def slow_reads(monkeypatch):
    """Widens the window between a read-modify-write's read and its write, so
    an unprotected one loses an update every time instead of once in a while."""
    monkeypatch.setattr(persistence, "_load_versions_manifest", _slow(persistence._load_versions_manifest))
    monkeypatch.setattr(persistence, "load_document_manifest", _slow(persistence.load_document_manifest))


def _run_in_threads(fn, count):
    threads = [threading.Thread(target=fn, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_create_schema_version_gives_every_caller_its_own_version(slow_reads):
    schema = {"node_types": [{"name": "Person", "description": "p"}], "edge_types": []}
    created = []

    def create(i):
        created.append(persistence.create_schema_version(STEM, schema, document_type="general"))

    _run_in_threads(create, 8)

    assert sorted(created) == list(range(1, 9))
    assert [v["version"] for v in persistence.list_versions(STEM)] == sorted(created)
    assert all(persistence.load_schema(STEM, v) == schema for v in created)


def test_concurrent_manifest_updates_keep_every_field(slow_reads):
    persistence.save_document_manifest(STEM, "report.pdf")

    def update(i):
        persistence.update_document_manifest(STEM, **{f"field_{i}": i})

    _run_in_threads(update, 8)

    manifest = persistence.load_document_manifest(STEM)
    assert {k: v for k, v in manifest.items() if k.startswith("field_")} == {
        f"field_{i}": i for i in range(8)
    }
    assert manifest["original_filename"] == "report.pdf"
