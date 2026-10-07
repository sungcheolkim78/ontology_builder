"""The data directory is read when it is used, not when a module is imported:
changing ONTOLOGY_DATA_DIR after import moves every store with it."""

from app.graph import graphdb
from app.ontology import domain_schema, persistence

SCHEMA = {"node_types": [{"name": "Person", "description": "p"}], "edge_types": []}


def test_list_schema_stems_follows_the_data_dir_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_DATA_DIR", str(tmp_path))
    persistence.create_schema_version("doc_a", SCHEMA)

    assert persistence.list_schema_stems() == ["doc_a"]


def test_list_domains_follows_the_data_dir_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_DATA_DIR", str(tmp_path))
    domain_schema.save_domain_schema("insurance", SCHEMA)

    assert domain_schema.list_domains() == ["insurance"]


def test_the_graph_database_lives_under_the_current_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_DATA_DIR", str(tmp_path))

    assert graphdb.db_path() == tmp_path / "graph" / "graph.ladybugdb"
