import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.preprocess.parser import (
    DATA_DIR,
    convert_general_pdf_to_markdown,
    convert_pdf_to_markdown_file,
    general_page_to_markdown,
    markdown_text,
    normalize_policy_headings,
    normalize_table,
    table_to_markdown,
)
from app.utils.paths import document_dir_for


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
        "app.preprocess.parser.anydoc.to_markdown_bytes",
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


def test_parse_saves_original_pdf_bytes_as_source_pdf(monkeypatch):
    monkeypatch.setattr(
        "app.main.convert_pdf_to_markdown_file",
        lambda filename, data: {"filename": "report_raw.md", "path": "data/report_raw.md"},
    )
    client = TestClient(app)

    response = client.post(
        "/api/parse",
        files={"file": ("report.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")},
    )

    assert response.status_code == 200
    saved = document_dir_for("report_raw") / "source.pdf"
    assert saved.read_bytes() == b"%PDF-1.4 fake pdf bytes"


def test_parse_does_not_save_source_pdf_for_non_pdf_upload(monkeypatch):
    monkeypatch.setattr(
        "app.preprocess.parser.anydoc.to_markdown_bytes", lambda data, fmt=None: "# hello"
    )
    client = TestClient(app)

    client.post(
        "/api/parse",
        files={"file": ("report.docx", b"fake docx bytes", "application/octet-stream")},
    )

    assert not (document_dir_for("report_raw") / "source.pdf").exists()


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


def test_markdown_text_structures_policy_section_headings():
    result = markdown_text(
        "LY0849001(260504)\n삼성 인터넷 급여 실손의료비보장보험(2605)\n제1관 일반사항 및 용어의 정의"
    )
    assert "제1관 일반사항 및 용어의 정의" in result
    assert "## 제1관 일반사항 및 용어의 정의" in result


def test_markdown_text_removes_standalone_page_number():
    assert markdown_text("내용\n- 12 -\n다음") == "내용\n\n다음"


def test_markdown_text_inserts_blank_line_before_circled_number_paragraph():
    text = (
        "초과금액을 보상합니다.\n"
        "② 제1항의 상해에는 유독가스 또는 유독물질을 우연히 일시에 흡입, 흡수 또는 섭취한 결과로 생긴 중독증상이 포함됩\n"
        "니다."
    )
    result = markdown_text(text)
    assert result == (
        "초과금액을 보상합니다.\n"
        "\n"
        "② 제1항의 상해에는 유독가스 또는 유독물질을 우연히 일시에 흡입, 흡수 또는 섭취한 결과로 생긴 중독증상이 포함됩\n"
        "니다."
    )


def test_markdown_text_does_not_duplicate_blank_line_before_circled_number():
    result = markdown_text("첫 문단입니다.\n\n③ 두 번째 항입니다.")
    assert result == "첫 문단입니다.\n\n③ 두 번째 항입니다."


def test_markdown_text_first_circled_number_in_band_needs_no_leading_blank():
    result = markdown_text("① 첫 번째 항입니다.")
    assert result == "① 첫 번째 항입니다."


def test_markdown_text_does_not_heading_a_sentence_that_merely_references_articles():
    line = "제3조(보상내용) 및 제4조(보상하지 않는 사항)은 각 보장종목에 해당하는 약관을 참조하시기 바랍니다."
    result = markdown_text(line)
    assert result == line
    assert "###" not in result


def test_markdown_text_does_not_heading_a_bare_article_reference_mid_sentence():
    line = "제2조에 따른 한의사를 제외한 '의사'의 의료행위에 의해서 발생한 의료비는 보상합니다"
    result = markdown_text(line)
    assert result == line
    assert "###" not in result


def test_markdown_text_still_headings_bracketed_article_title():
    result = markdown_text("제4조 [보상하지 않는 사항]")
    assert result == "### 제4조 [보상하지 않는 사항]"


def test_markdown_text_still_headings_article_with_sub_number():
    result = markdown_text("제15조의2 [보장내용 변경주기]")
    assert result == "### 제15조의2 [보장내용 변경주기]"


def test_markdown_text_bullets_numbered_sub_items_instead_of_heading():
    result = markdown_text("2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.")
    assert result == "- 2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다."


def test_markdown_text_bullets_numbered_item_prefixed_with_note_marker():
    result = markdown_text("㈜ 1. 「국민건강보험법」에서 정한 요양급여")
    assert result == "- ㈜ 1. 「국민건강보험법」에서 정한 요양급여"


def test_markdown_text_bullets_numbered_item_with_note_marker_no_space():
    result = markdown_text("㈜1. 「국민건강보험법」에서 정한 요양급여")
    assert result == "- ㈜1. 「국민건강보험법」에서 정한 요양급여"


def test_markdown_text_leaves_bare_note_marker_without_number_untouched():
    line = "㈜ 질병/상해 보장으로 구분이 되어있지 않은 요양병원실손의료비 및 상급병실료차액의 경우 계약자가"
    assert markdown_text(line) == line


def test_markdown_text_does_not_insert_blank_line_after_numbered_bullet():
    text = (
        "㈜ 1. 「국민건강보험법」에서 정한 요양급여 또는 「의료급여법」에서 정한 의료급여 절차를 거쳤지만 급여항목이\n"
        "발생하지 않은 경우로 「국민건강보험법」 또는 「의료급여법」에 따른 비급여항목 포함\n"
        "2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.\n"
        "\n"
        "다만, 입원의 경우 피보험자가 부담하는 금액을 합한 금액이 500만원을 초과하는 경우 보상합니다."
    )
    result = markdown_text(text)
    assert result == (
        "- ㈜ 1. 「국민건강보험법」에서 정한 요양급여 또는 「의료급여법」에서 정한 의료급여 절차를 거쳤지만 급여항목이\n"
        "발생하지 않은 경우로 「국민건강보험법」 또는 「의료급여법」에 따른 비급여항목 포함\n"
        "- 2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.\n"
        "\n"
        "다만, 입원의 경우 피보험자가 부담하는 금액을 합한 금액이 500만원을 초과하는 경우 보상합니다."
    )


def test_markdown_text_headings_gwan_section_as_level_two():
    result = markdown_text("제1관 일반사항 및 용어의 정의")
    assert result == "## 제1관 일반사항 및 용어의 정의"


COVER_PAGE_TEXT = (
    "LY0816002(260626)\n"
    "삼성 노후실손의료비보장보험(2601)\n"
    "(갱신형,무배당) 약관\n"
    "※ 갱신주기는 1년으로 합니다.\n"
    "노후실손의료보험은 보험회사가 피보험자의 질병 또는 상해로 인한 손해(의료비에 한정합니다)를 보상하는\n"
    "상품입니다.\n"
    "제1관 일반사항 및 용어의 정의"
)


def test_markdown_text_styles_cover_page_wherever_it_occurs():
    # This mini-cover (code + name + description + first 제N관) recurs at the
    # start of the main contract and every rider (특약) throughout a policy
    # document, not just on the PDF's literal first page.
    result = markdown_text(COVER_PAGE_TEXT)
    assert result == (
        "**보험코드:** LY0816002(260626)\n"
        "\n"
        "# 삼성 노후실손의료비보장보험(2601)\n"
        "(갱신형,무배당) 약관\n"
        "\n"
        "> ※ 갱신주기는 1년으로 합니다.\n"
        "> 노후실손의료보험은 보험회사가 피보험자의 질병 또는 상해로 인한 손해(의료비에 한정합니다)를 보상하는\n"
        "> 상품입니다.\n"
        "\n"
        "## 제1관 일반사항 및 용어의 정의"
    )


def test_markdown_text_styles_single_line_rider_cover():
    result = markdown_text(
        "JJ0000001(221209)\n지정대리청구서비스특약 약관\n※ 이 특약은 대리청구를 위한 것입니다.\n제1관 보험계약의 성립과 유지"
    )
    assert result == (
        "**보험코드:** JJ0000001(221209)\n"
        "\n"
        "# 지정대리청구서비스특약 약관\n"
        "\n"
        "> ※ 이 특약은 대리청구를 위한 것입니다.\n"
        "\n"
        "## 제1관 보험계약의 성립과 유지"
    )


def test_markdown_text_leaves_non_cover_text_untouched():
    result = markdown_text("이 약관은 다음과 같습니다.\n제1관 총칙")
    assert "**보험코드:**" not in result
    assert "## 제1관 총칙" in result
    assert result.startswith("이 약관은 다음과 같습니다.")


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
