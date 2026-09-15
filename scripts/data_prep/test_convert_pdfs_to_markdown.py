import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("convert_pdfs_to_markdown.py")
SPEC = importlib.util.spec_from_file_location("convert_pdfs_to_markdown", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_normalize_table_removes_empty_border_columns():
    rows = [["", "항목", "내용", ""], [None, "보험료", "10만원", None]]
    assert module.normalize_table(rows) == [["항목", "내용"], ["보험료", "10만원"]]


def test_table_to_markdown_preserves_cell_line_breaks():
    rows = module.normalize_table([["항목", "내용"], ["조건", "첫째\n둘째"]])
    result = module.table_to_markdown(rows)
    assert "| 항목 | 내용 |" in result
    assert "| 조건 | 첫째<br>둘째 |" in result


def test_markdown_text_structures_korean_articles_and_bullets():
    result = module.markdown_text("제1조(목적)\n● 보험금을 지급합니다.")
    assert "### 제1조(목적)" in result
    assert "- 보험금을 지급합니다." in result


def test_markdown_text_structures_policy_section_headings():
    result = module.markdown_text("LY0849001(260504)\n삼성 인터넷 급여 실손의료비보장보험(2605)\n제1관 일반사항 및 용어의 정의")
    assert "제1관 일반사항 및 용어의 정의" in result
    assert "## 제1관 일반사항 및 용어의 정의" in result


def test_markdown_text_removes_standalone_page_number():
    assert module.markdown_text("내용\n- 12 -\n다음") == "내용\n\n다음"


def test_markdown_text_inserts_blank_line_before_circled_number_paragraph():
    text = (
        "초과금액을 보상합니다.\n"
        "② 제1항의 상해에는 유독가스 또는 유독물질을 우연히 일시에 흡입, 흡수 또는 섭취한 결과로 생긴 중독증상이 포함됩\n"
        "니다."
    )
    result = module.markdown_text(text)
    assert result == (
        "초과금액을 보상합니다.\n"
        "\n"
        "② 제1항의 상해에는 유독가스 또는 유독물질을 우연히 일시에 흡입, 흡수 또는 섭취한 결과로 생긴 중독증상이 포함됩\n"
        "니다."
    )


def test_markdown_text_does_not_duplicate_blank_line_before_circled_number():
    result = module.markdown_text("첫 문단입니다.\n\n③ 두 번째 항입니다.")
    assert result == "첫 문단입니다.\n\n③ 두 번째 항입니다."


def test_markdown_text_first_circled_number_in_band_needs_no_leading_blank():
    result = module.markdown_text("① 첫 번째 항입니다.")
    assert result == "① 첫 번째 항입니다."


def test_markdown_text_does_not_heading_a_sentence_that_merely_references_articles():
    line = "제3조(보상내용) 및 제4조(보상하지 않는 사항)은 각 보장종목에 해당하는 약관을 참조하시기 바랍니다."
    result = module.markdown_text(line)
    assert result == line
    assert "###" not in result


def test_markdown_text_does_not_heading_a_bare_article_reference_mid_sentence():
    line = "제2조에 따른 한의사를 제외한 ‘의사’의 의료행위에 의해서 발생한 의료비는 보상합니다"
    result = module.markdown_text(line)
    assert result == line
    assert "###" not in result


def test_markdown_text_still_headings_bracketed_article_title():
    result = module.markdown_text("제4조 [보상하지 않는 사항]")
    assert result == "### 제4조 [보상하지 않는 사항]"


def test_markdown_text_still_headings_article_with_sub_number():
    result = module.markdown_text("제15조의2 [보장내용 변경주기]")
    assert result == "### 제15조의2 [보장내용 변경주기]"


def test_markdown_text_bullets_numbered_sub_items_instead_of_heading():
    result = module.markdown_text("2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.")
    assert result == "- 2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다."


def test_markdown_text_bullets_numbered_item_prefixed_with_note_marker():
    result = module.markdown_text("㈜ 1. 「국민건강보험법」에서 정한 요양급여")
    assert result == "- ㈜ 1. 「국민건강보험법」에서 정한 요양급여"


def test_markdown_text_bullets_numbered_item_with_note_marker_no_space():
    result = module.markdown_text("㈜1. 「국민건강보험법」에서 정한 요양급여")
    assert result == "- ㈜1. 「국민건강보험법」에서 정한 요양급여"


def test_markdown_text_leaves_bare_note_marker_without_number_untouched():
    line = "㈜ 질병/상해 보장으로 구분이 되어있지 않은 요양병원실손의료비 및 상급병실료차액의 경우 계약자가"
    assert module.markdown_text(line) == line


def test_markdown_text_does_not_insert_blank_line_after_numbered_bullet():
    text = (
        "㈜ 1. 「국민건강보험법」에서 정한 요양급여 또는 「의료급여법」에서 정한 의료급여 절차를 거쳤지만 급여항목이\n"
        "발생하지 않은 경우로 「국민건강보험법」 또는 「의료급여법」에 따른 비급여항목 포함\n"
        "2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.\n"
        "\n"
        "다만, 입원의 경우 피보험자가 부담하는 금액을 합한 금액이 500만원을 초과하는 경우 보상합니다."
    )
    result = module.markdown_text(text)
    assert result == (
        "- ㈜ 1. 「국민건강보험법」에서 정한 요양급여 또는 「의료급여법」에서 정한 의료급여 절차를 거쳤지만 급여항목이\n"
        "발생하지 않은 경우로 「국민건강보험법」 또는 「의료급여법」에 따른 비급여항목 포함\n"
        "- 2. 외래제비용, 외래수술비 및 처방조제비를 합산하여 공제금액을 적용합니다.\n"
        "\n"
        "다만, 입원의 경우 피보험자가 부담하는 금액을 합한 금액이 500만원을 초과하는 경우 보상합니다."
    )


def test_markdown_text_headings_gwan_section_as_level_two():
    result = module.markdown_text("제1관 일반사항 및 용어의 정의")
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
    # document, not just on the PDF's literal first page -- see e.g. pages
    # 29, 60, 78, 110 of a real converted document.
    result = module.markdown_text(COVER_PAGE_TEXT)
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
    result = module.markdown_text(
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
    result = module.markdown_text("이 약관은 다음과 같습니다.\n제1관 총칙")
    assert "**보험코드:**" not in result
    assert "## 제1관 총칙" in result
    assert result.startswith("이 약관은 다음과 같습니다.")


def test_main_respects_limit_argument(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4")
    output_dir = tmp_path / "out"

    converted = []

    def fake_convert_pdf(source, destination):
        converted.append(source)
        return {
            "source_pdf": str(source),
            "source_sha256": "x",
            "markdown_file": str(destination),
            "pages": 1,
            "tables": 0,
            "characters": 10,
        }

    monkeypatch.setattr(module, "convert_pdf", fake_convert_pdf)
    exit_code = module.main(
        ["--input-dir", str(tmp_path), "--output-dir", str(output_dir), "--limit", "1"]
    )
    assert exit_code == 0
    assert len(converted) == 1


def test_main_rejects_non_positive_limit(tmp_path):
    exit_code = module.main(["--input-dir", str(tmp_path), "--limit", "0"])
    assert exit_code == 2
