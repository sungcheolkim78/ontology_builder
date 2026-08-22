import os
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


def test_list_files_returns_saved_filenames_newest_first():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "older_raw.md").write_text("old")
    (DATA_DIR / "older_raw.md").touch()
    os.utime(DATA_DIR / "older_raw.md", (1000, 1000))
    (DATA_DIR / "newer_raw.md").write_text("new")
    os.utime(DATA_DIR / "newer_raw.md", (2000, 2000))
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {
        "files": [{"filename": "newer_raw.md"}, {"filename": "older_raw.md"}]
    }


def test_list_files_excludes_hidden_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ".gitkeep").write_text("")
    (DATA_DIR / "report_raw.md").write_text("content")
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": [{"filename": "report_raw.md"}]}


def test_list_files_returns_empty_list_when_no_data_dir():
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": []}


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
