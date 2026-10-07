import os
import shutil
import tempfile

# A session-wide throwaway data directory, set before anything imports an app
# module: a safety net so that nothing running outside a test (collection,
# import time) can touch the real backend/data tree. Every test then gets a
# data directory of its own, see isolated_data_dir below.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="ontology_builder_test_data_")
os.environ["ONTOLOGY_DATA_DIR"] = _TEST_DATA_DIR


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    """No test makes a real OpenRouter embeddings call: every test starts with
    a fake embedding model installed at the one patch point,
    app.llm.calls.get_embedding_model. A test that cares about the vectors
    patches that same name itself, after this runs."""
    from fakes import FakeEmbeddingModel

    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: FakeEmbeddingModel())


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Points the app at an empty data directory of its own for each test, so no
    test deletes or recreates anything: the documents, schemas and graph
    database one test writes are simply gone for the next. The graph
    database's connection is cached, so it is dropped on the way in and out."""
    from app.graph import graphdb

    # A subfolder, so a test can keep its own files in tmp_path without them
    # being part of (or wiped with) the data directory.
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ONTOLOGY_DATA_DIR", str(data))
    graphdb.reset_connection()
    yield data
    graphdb.reset_connection()
