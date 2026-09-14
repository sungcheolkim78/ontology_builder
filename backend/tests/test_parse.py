import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parser import DATA_DIR, normalize_policy_headings
from app.paths import document_dir_for


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
    assert response.json() == {
        "filename": "report_raw.md",
        "path": "data/documents/report_raw/raw.md",
    }
    saved = document_dir_for("report_raw") / "raw.md"
    assert saved.read_text() == "# hello"


def test_normalize_policy_headings_marks_plain_section_and_article_lines():
    result = normalize_policy_headings(
        "상품명\n제1관 일반사항 및 용어의 정의\n제1조 [보장종목]\n제1조에 따른다."
    )
    assert result == (
        "상품명\n## 제1관 일반사항 및 용어의 정의\n"
        "### 제1조 [보장종목]\n제1조에 따른다."
    )


def test_parse_normalizes_already_markdown_policy_upload():
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={
            "file": (
                "terms.md",
                "LY0849001(260504)\n제1관 일반사항 및 용어의 정의\n### 제1조 [보장종목]".encode(
                    "utf-8"
                ),
                "text/markdown",
            )
        },
    )

    assert response.status_code == 200
    assert "## 제1관 일반사항 및 용어의 정의" in (
        document_dir_for("terms_raw") / "raw.md"
    ).read_text()


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
    assert response.json() == {
        "filename": "notes_raw.md",
        "path": "data/documents/notes_raw/raw.md",
    }
    saved = document_dir_for("notes_raw") / "raw.md"
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

    assert load_document_manifest("report_raw") == {
        "original_filename": "report.docx",
        "converter": "anydoc",
    }


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


def test_parse_uses_table_aware_converter_for_pdf_when_requested(monkeypatch):
    def fail_if_called(data, fmt=None):
        raise AssertionError("anydoc should not be called when table_aware is requested for a pdf")

    monkeypatch.setattr("app.parser.anydoc.to_markdown_bytes", fail_if_called)
    monkeypatch.setattr(
        "app.main.convert_pdf_to_markdown_file",
        lambda filename, data: {"filename": "report_raw.md", "path": "data/report_raw.md"},
    )
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.pdf", b"fake pdf bytes", "application/pdf")},
        data={"converter": "table_aware"},
    )

    assert response.status_code == 200
    from app.ontology import load_document_manifest

    assert load_document_manifest("report_raw") == {
        "original_filename": "report.pdf",
        "converter": "table_aware",
    }


def test_parse_uses_table_aware_converter_for_pdf_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.main.convert_pdf_to_markdown_file",
        lambda filename, data: {"filename": "report_raw.md", "path": "data/report_raw.md"},
    )
    monkeypatch.setattr(
        "app.parser.anydoc.to_markdown_bytes",
        lambda data, fmt=None: (_ for _ in ()).throw(
            AssertionError("insurance PDFs should use the table-aware converter")
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.pdf", b"fake pdf bytes", "application/pdf")},
    )

    assert response.status_code == 200


def test_parse_ignores_table_aware_for_non_pdf_upload(monkeypatch):
    monkeypatch.setattr(
        "app.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
    )
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.docx", b"fake docx bytes", "application/octet-stream")},
        data={"converter": "table_aware"},
    )

    assert response.status_code == 200
    from app.ontology import load_document_manifest

    assert load_document_manifest("report_raw")["converter"] == "anydoc"


def test_parse_returns_400_when_table_aware_conversion_fails(monkeypatch):
    def raise_error(filename, data):
        raise ValueError("not a valid pdf")

    monkeypatch.setattr("app.main.convert_pdf_to_markdown_file", raise_error)
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.pdf", b"not a real pdf", "application/pdf")},
        data={"converter": "table_aware"},
    )

    assert response.status_code == 400
