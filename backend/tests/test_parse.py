import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
<<<<<<< HEAD
from app.parser import DATA_DIR, normalize_policy_headings
=======
from app.preprocess.parser import (
    DATA_DIR,
    convert_general_pdf_to_markdown,
    convert_pdf_to_markdown_file,
    general_page_to_markdown,
    markdown_text,
    normalize_table,
    table_to_markdown,
)
>>>>>>> d45d004182ba4ebae79f8cc382b56b91fdeef35a
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
        "app.preprocess.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
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

    monkeypatch.setattr("app.preprocess.parser.anydoc.to_markdown_bytes", fail_if_called)
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
        "app.preprocess.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
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
    from app.preprocess.parser import anydoc as anydoc_module

    def raise_unsupported(data, fmt=None):
        raise anydoc_module.UnsupportedError("nope")

    monkeypatch.setattr("app.preprocess.parser.anydoc.to_markdown_bytes", raise_unsupported)
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

    monkeypatch.setattr("app.preprocess.parser.anydoc.to_markdown_bytes", fail_if_called)
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
        "app.preprocess.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
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


def test_normalize_table_removes_empty_border_columns():
    rows = [["", "항목", "내용", ""], [None, "보험료", "10만원", None]]
    assert normalize_table(rows) == [["항목", "내용"], ["보험료", "10만원"]]


def test_table_to_markdown_preserves_cell_line_breaks():
    rows = normalize_table([["항목", "내용"], ["조건", "첫째\n둘째"]])
    result = table_to_markdown(rows)
    assert "| 항목 | 내용 |" in result
    assert "| 조건 | 첫째<br>둘째 |" in result


def test_markdown_text_structures_korean_articles_and_bullets():
    result = markdown_text("제1조(목적)\n● 보험금을 지급합니다.")
    assert "### 제1조(목적)" in result
    assert "- 보험금을 지급합니다." in result


def test_markdown_text_removes_standalone_page_number():
    assert markdown_text("내용\n- 12 -\n다음") == "내용\n\n다음"


def test_markdown_text_links_midsentence_article_reference_instead_of_heading():
    result = markdown_text(
        "제3조(보험금의 지급사유)에 해당하는 피보험자의 위험을 보장하기 위하여 체결됩니다."
    )
    assert result == (
        "[제3조(보험금의 지급사유)]에 해당하는 피보험자의 위험을 보장하기 위하여 체결됩니다."
    )
    assert "###" not in result


def test_markdown_text_links_midsentence_article_reference_without_title():
    assert markdown_text("제1조에 따라 지급합니다.") == "[제1조]에 따라 지급합니다."


def test_markdown_text_links_midsentence_chapter_reference_instead_of_heading():
    result = markdown_text("제2장(보장내용)에 따라 다음과 같이 정합니다.")
    assert result == "[제2장(보장내용)]에 따라 다음과 같이 정합니다."
    assert "##" not in result


def test_markdown_text_links_reference_alone_on_a_line_without_blank_line_before():
    """A citation can land alone on its own line purely from PDF line-wrap
    (e.g. "...제3조(보험금의 지급사유)를 준용한다" wraps so the reference is
    the whole line and the continuation is the next), even though it isn't
    starting a new article -- unlike a real heading, there's no blank line
    separating it from the preceding sentence."""
    result = markdown_text("이 계약은 별도의 규정이 없는 한\n제3조(보험금의 지급사유)")
    assert result == "이 계약은 별도의 규정이 없는 한\n[제3조(보험금의 지급사유)]"
    assert "###" not in result


def test_markdown_text_still_structures_heading_preceded_by_blank_line():
    result = markdown_text("본문 내용\n\n제3조(목적)")
    assert "### 제3조(목적)" in result


def test_markdown_text_still_structures_standalone_chapter_heading():
    assert markdown_text("제2장(보장내용)") == "## 제2장(보장내용)"


def test_markdown_text_numbers_list_one_level_below_chapter():
    result = markdown_text("제2장(보장내용)\n1. 첫번째 항목")
    assert "## 제2장(보장내용)" in result
    assert "### 1. 첫번째 항목" in result


def test_markdown_text_numbers_list_one_level_below_article():
    result = markdown_text("제1조(목적)\n1. 첫번째 항목")
    assert "### 제1조(목적)" in result
    assert "#### 1. 첫번째 항목" in result


def test_markdown_text_structures_standalone_section_heading_without_title():
    """제N관 (subsection) titles are conventionally bare, unparenthesized
    text after the marker (unlike articles, which always parenthesize their
    title) -- still a genuine heading when it's the whole line and preceded
    by a blank line (or nothing, i.e. first line)."""
    assert (
        markdown_text("제1관 목적 및 용어의 정의")
        == "## 제1관 목적 및 용어의 정의"
    )


def test_markdown_text_numbers_list_one_level_below_section():
    result = markdown_text("제1관 목적 및 용어의 정의\n1. 첫번째 항목")
    assert "## 제1관 목적 및 용어의 정의" in result
    assert "### 1. 첫번째 항목" in result


def test_markdown_text_links_midsentence_section_reference_without_blank_line_before():
    result = markdown_text("본문이 이어지는 문장\n제1관에서 정한 사항에 따릅니다.")
    assert result == "본문이 이어지는 문장\n[제1관]에서 정한 사항에 따릅니다."
    assert "##" not in result


def test_markdown_text_numbers_list_defaults_to_h3_without_prior_heading():
    assert markdown_text("1. 첫번째 항목") == "### 1. 첫번째 항목"


def test_convert_pdf_to_markdown_file_saves_markdown_and_returns_path(monkeypatch):
    monkeypatch.setattr(
        "app.preprocess.parser.convert_insurance_policy_to_markdown",
        lambda data, title: f"# {title}\n\nbody",
    )
    monkeypatch.setattr(
        "app.preprocess.parser.convert_general_pdf_to_markdown",
        lambda data, title: f"# {title}\n\ngeneral body",
    )

    result = convert_pdf_to_markdown_file("report.pdf", b"fake pdf bytes")

    assert result == {
        "filename": "report_raw.md",
        "path": "data/documents/report_raw/raw.md",
    }
    assert (document_dir_for("report_raw") / "raw.md").read_text() == "# report\n\nbody"


def test_convert_pdf_to_markdown_file_also_saves_general_conversion_as_raw0(monkeypatch):
    monkeypatch.setattr(
        "app.preprocess.parser.convert_insurance_policy_to_markdown",
        lambda data, title: f"# {title}\n\nbody",
    )
    monkeypatch.setattr(
        "app.preprocess.parser.convert_general_pdf_to_markdown",
        lambda data, title: f"# {title}\n\ngeneral body",
    )

    convert_pdf_to_markdown_file("report.pdf", b"fake pdf bytes")

    assert (
        document_dir_for("report_raw") / "raw0.md"
    ).read_text() == "# report\n\ngeneral body"


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, **kwargs):
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_general_page_to_markdown_does_not_apply_heading_or_bullet_structure():
    page = FakePage("제1조(목적)\n● 보험금을 지급합니다.  ")
    result = general_page_to_markdown(page)
    assert result == "제1조(목적)\n● 보험금을 지급합니다."
    assert "###" not in result


def test_general_page_to_markdown_keeps_standalone_page_numbers():
    page = FakePage("본문\n- 12 -\n다음")
    assert general_page_to_markdown(page) == "본문\n- 12 -\n다음"


def test_convert_general_pdf_to_markdown_joins_pages_with_divider(monkeypatch):
    monkeypatch.setattr(
        "app.preprocess.parser.pdfplumber.open",
        lambda _: FakePdf([FakePage("첫 페이지"), FakePage("둘째 페이지")]),
    )

    result = convert_general_pdf_to_markdown(b"fake pdf bytes", "제목")

    assert result == "# 제목\n\n첫 페이지\n\n---\n\n둘째 페이지\n"
