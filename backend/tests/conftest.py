import os
import shutil
import tempfile

# Must run before any `app.*` module is imported (conftest.py is loaded by
# pytest ahead of test module collection) -- app.paths.data_dir() reads this
# env var once, at each module's import time, to compute DATA_DIR/GRAPH_DIR/
# DB_PATH. Without this, the test suite reads/writes/deletes the real
# backend/data tree, which is exactly the accidental-data-loss risk this
# isolates against (see CLAUDE.md's "Do not run the backend test suite
# while podman-compose is up").
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="ontology_builder_test_data_")
os.environ["ONTOLOGY_DATA_DIR"] = _TEST_DATA_DIR


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
