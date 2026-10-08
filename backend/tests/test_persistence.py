"""Tests for app.ontology.persistence's read-modify-write operations under
concurrent callers: two requests for one document at once must not lose an
update."""

import threading
import time

import pytest

from app.ontology import domain_schema, persistence
from app.preprocess import goldenset

STEM = "doc_raw"


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
    monkeypatch.setattr(goldenset, "_load_answers", _slow(goldenset._load_answers))
    monkeypatch.setattr(domain_schema, "load_domain_pending_review", _slow(domain_schema.load_domain_pending_review))


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


def test_concurrent_goldenset_answers_for_one_question_are_all_kept(slow_reads):
    def record(i):
        goldenset.record_goldenset_answer(
            STEM, "q1", schema_version=1, hops=1, content=f"answer {i}",
            node_types=[], edge_types=[], related_nodes=[], related_edges=[],
        )

    _run_in_threads(record, 8)

    kept = goldenset._load_answers(STEM)["q1"]
    assert sorted(r["content"] for r in kept) == sorted(f"answer {i}" for i in range(8))


def test_concurrent_pending_review_resolutions_each_remove_their_own_changes(slow_reads):
    schema = {"node_types": [{"name": "Person", "description": "p"}], "edge_types": []}
    domain_schema.save_domain_schema("insurance", schema)
    domain_schema._save_domain_pending_review(
        "insurance", [{"change_id": f"c{i}"} for i in range(8)]
    )

    def resolve(i):
        domain_schema.apply_domain_schema_changes("insurance", [{
            "change_id": f"c{i}", "element_type": "node_type", "decision": "ADD",
            "element": {"name": f"Type{i}", "description": "d"},
        }])

    _run_in_threads(resolve, 8)

    assert domain_schema.load_domain_pending_review("insurance") == []
    names = {t["name"] for t in domain_schema.load_domain_schema("insurance")["node_types"]}
    assert names == {"Person"} | {f"Type{i}" for i in range(8)}


# --- resolving a document's active schema / ontology -------------------------

SCHEMA = {"node_types": [{"name": "Person", "description": "p"}], "edge_types": []}
NODES = [{"id": "n1", "label": "Alice", "type": "Person"}]


def test_active_schema_returns_the_active_version_and_its_schema():
    persistence.create_schema_version(STEM, {"node_types": [], "edge_types": []})
    version = persistence.create_schema_version(STEM, SCHEMA)

    assert persistence.active_schema(STEM) == (version, SCHEMA)


def test_active_schema_raises_when_the_document_has_no_schema():
    with pytest.raises(persistence.SchemaNotFound):
        persistence.active_schema(STEM)


def test_active_schema_raises_when_the_active_versions_file_is_gone():
    version = persistence.create_schema_version(STEM, SCHEMA)
    persistence.schema_path_for_version(STEM, version).unlink()

    with pytest.raises(persistence.SchemaNotFound):
        persistence.active_schema(STEM)


def test_active_ontology_returns_the_version_schema_and_extracted_graph():
    version = persistence.create_schema_version(STEM, SCHEMA)
    persistence.graphdb.write_graph(STEM, NODES, [], version=version)

    got_version, schema, graph = persistence.active_ontology(STEM)

    assert (got_version, schema) == (version, SCHEMA)
    assert [n["label"] for n in graph["nodes"]] == ["Alice"]


def test_active_ontology_without_loading_the_graph_still_checks_that_there_is_one():
    version = persistence.create_schema_version(STEM, SCHEMA)
    with pytest.raises(persistence.OntologyNotExtracted):
        persistence.active_ontology(STEM, load_graph=False)

    persistence.graphdb.write_graph(STEM, NODES, [], version=version)

    assert persistence.active_ontology(STEM, load_graph=False) == (version, SCHEMA, None)


def test_active_ontology_raises_schema_not_found_before_looking_for_a_graph():
    with pytest.raises(persistence.SchemaNotFound):
        persistence.active_ontology(STEM)


def test_both_not_found_errors_are_lookup_errors():
    assert issubclass(persistence.SchemaNotFound, LookupError)
    assert issubclass(persistence.OntologyNotExtracted, LookupError)
