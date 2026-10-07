import os
import shutil
import tempfile

# Must run before any `app.*` module is imported (conftest.py is loaded by
# pytest ahead of test module collection) -- app.utils.paths.data_dir() reads this
# env var once, at each module's import time, to compute DATA_DIR/GRAPH_DIR/
# DB_PATH. Without this, the test suite reads/writes/deletes the real
# backend/data tree, which is exactly the accidental-data-loss risk this
# isolates against (see CLAUDE.md's "Do not run the backend test suite
# while podman-compose is up").
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
