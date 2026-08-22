import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parser import DATA_DIR


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def test_get_file_returns_saved_markdown_content():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "report_raw.md").write_text("# hello")
    client = TestClient(app)

    response = client.get("/api/files/report_raw.md")

    assert response.status_code == 200
    assert response.text == "# hello"


def test_get_file_returns_404_for_missing_file():
    client = TestClient(app)

    response = client.get("/api/files/does_not_exist.md")

    assert response.status_code == 404


def test_get_file_blocks_path_traversal():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    outside = DATA_DIR.parent / "secret.txt"
    outside.write_text("top secret")
    client = TestClient(app)

    try:
        response = client.get("/api/files/..%2Fsecret.txt")
        assert response.status_code == 404
    finally:
        outside.unlink()
