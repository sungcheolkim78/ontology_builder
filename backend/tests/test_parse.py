import shutil

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


def test_parse_saves_markdown_and_returns_path(monkeypatch):
    monkeypatch.setattr(
        "app.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
    )
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.docx", b"fake docx bytes", "application/octet-stream")},
    )

    assert response.status_code == 200
    assert response.json() == {"filename": "report_raw.md", "path": "data/report_raw.md"}
    saved = DATA_DIR / "report_raw.md"
    assert saved.read_text() == "# hello"


def test_parse_registers_markdown_upload_without_anydoc_conversion(monkeypatch):
    def fail_if_called(data, fmt=None):
        raise AssertionError("anydoc.to_markdown_bytes should not be called for .md uploads")

    monkeypatch.setattr("app.parser.anydoc.to_markdown_bytes", fail_if_called)
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("notes.md", "# already markdown".encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {"filename": "notes_raw.md", "path": "data/notes_raw.md"}
    saved = DATA_DIR / "notes_raw.md"
    assert saved.read_text() == "# already markdown"


def test_parse_returns_400_for_non_utf8_markdown_upload():
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("notes.md", b"\xff\xfe not utf-8", "text/markdown")},
    )

    assert response.status_code == 400


def test_parse_saves_original_filename_to_document_manifest(monkeypatch):
    from app.ontology import load_document_manifest

    monkeypatch.setattr(
        "app.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
    )
    client = TestClient(app)

    client.post(
        "/api/parse",
        files={"file": ("report.docx", b"fake docx bytes", "application/octet-stream")},
    )

    assert load_document_manifest("report_raw") == {"original_filename": "report.docx"}


def test_parse_returns_400_on_unsupported_format(monkeypatch):
    from app.parser import anydoc as anydoc_module

    def raise_unsupported(data, fmt=None):
        raise anydoc_module.UnsupportedError("nope")

    monkeypatch.setattr("app.parser.anydoc.to_markdown_bytes", raise_unsupported)
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("weird.xyz", b"???", "application/octet-stream")},
    )

    assert response.status_code == 400


def test_parse_returns_400_for_unrecognized_extension():
    """Regression test: anydoc raises plain ValueError (not ConvertError) for
    an extension it doesn't know, e.g. report.xyz. Uses the real anydoc call."""
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("weird.xyz", b"not a real document", "application/octet-stream")},
    )

    assert response.status_code == 400
