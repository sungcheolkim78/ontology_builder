import pytest

from app import graphdb


@pytest.fixture(autouse=True)
def clean_graphdb():
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        if graphdb.DB_PATH.is_dir():
            shutil.rmtree(graphdb.DB_PATH)
        else:
            graphdb.DB_PATH.unlink()
    yield
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        if graphdb.DB_PATH.is_dir():
            shutil.rmtree(graphdb.DB_PATH)
        else:
            graphdb.DB_PATH.unlink()


def test_has_graph_is_false_for_unknown_stem():
    assert graphdb.has_graph("nonexistent_stem") is False


def test_validate_identifier_accepts_safe_names():
    assert graphdb._validate_identifier("Person") == "Person"
    assert graphdb._validate_identifier("WORKED_ON") == "WORKED_ON"
    assert graphdb._validate_identifier("_leading_underscore") == "_leading_underscore"


def test_validate_identifier_rejects_unsafe_names():
    for bad in ["Person; DROP TABLE Person", "has space", "has-dash", "1StartsWithDigit", "", "has`tick", "Person\n"]:
        with pytest.raises(ValueError):
            graphdb._validate_identifier(bad)
