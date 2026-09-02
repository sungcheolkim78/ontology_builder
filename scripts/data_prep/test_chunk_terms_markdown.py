import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("chunk_terms_markdown.py")
SPEC = importlib.util.spec_from_file_location("chunk_terms_markdown", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_parse_article_heading_matches_bracket_title():
    result = module.parse_article_heading("### 제1조 [목적]")
    assert result == {"article_no": "1", "sub_no": None, "title": "목적"}


def test_parse_article_heading_matches_paren_title():
    result = module.parse_article_heading("### 제3조(보험금의 지급사유)")
    assert result == {"article_no": "3", "sub_no": None, "title": "보험금의 지급사유"}


def test_parse_article_heading_matches_sub_article_number():
    result = module.parse_article_heading('### 제2조의2 ["유치"의 정의]')
    assert result == {"article_no": "2", "sub_no": "2", "title": '"유치"의 정의'}


def test_parse_article_heading_keeps_nested_parens_in_title():
    result = module.parse_article_heading(
        "### 제27조(보험료의 납입연체로 인해 해지된 계약의 부활(효력회복))"
    )
    assert result["title"] == "보험료의 납입연체로 인해 해지된 계약의 부활(효력회복)"


def test_parse_article_heading_rejects_toc_duplicate():
    # table-of-contents entries carry a trailing page number / second entry
    result = module.parse_article_heading("### 제1조 [목적] 5 제4관 보험계약의 성립과 유지")
    assert result is None


def test_parse_article_heading_rejects_midsentence_corruption():
    # PDF line-wrap artifact: a cross-reference mid-paragraph gets treated as a heading
    result = module.parse_article_heading(
        "### 제3조(보험금의 지급사유)에 해당하는 피보험자의 위험을 보장하기 위하여 체결됩니다."
    )
    assert result is None


def test_parse_article_heading_rejects_numbered_sublist():
    result = module.parse_article_heading("### 1. 계약관계 관련 용어")
    assert result is None


def test_parse_article_heading_rejects_non_heading_text():
    assert module.parse_article_heading("이 계약에서 사용되는 용어의 정의는 다음과 같습니다.") is None


def test_guess_section_label_finds_last_rider_mention_in_block():
    lines = [
        "---",
        "재식립임플란트치료보장특약(갱신형,무배당)",
        "약관",
        "※ 이 특약의 갱신주기는 10년으로 합니다.",
        "제1관 목적 및 용어의 정의",
        "### 제1조 [목적]",
    ]
    assert module.guess_section_label(lines, 0, 5) == "재식립임플란트치료보장특약(갱신형,무배당)"


def test_guess_section_label_ignores_table_rows_and_comments():
    lines = [
        "| 특약명 | 보장내용 |",
        "<!-- page: 5 -->",
        "지정대리청구서비스특약(무배당)",
        "약관",
    ]
    assert module.guess_section_label(lines, 0, 4) == "지정대리청구서비스특약(무배당)"


def test_guess_section_label_skips_disclaimer_notes_mentioning_a_rider():
    # "※ 이 특약의 갱신주기는..." is boilerplate on every rider, not its name
    lines = [
        "재식립임플란트치료보장특약(갱신형,무배당)",
        "약관",
        "※ 이 특약의 갱신주기는 10년으로 합니다.",
        "제1관 목적 및 용어의 정의",
        "### 제1조 [목적]",
    ]
    assert module.guess_section_label(lines, 0, 4) == "재식립임플란트치료보장특약(갱신형,무배당)"


def test_guess_section_label_returns_none_without_a_rider_mention():
    lines = ["삼성 치아보험(2501) 빠짐없이 튼튼하게", "(갱신형,무배당)", "보험약관"]
    assert module.guess_section_label(lines, 0, 3) is None


SAMPLE_DOCUMENT = """지엄체크 항목
### 제1조 [목적]

이 계약은 성립됩니다.
### 제1조 [목적] 5 제4관 다음장
### 제2조(정의)

여기서 "피보험자"란 사람을 말합니다.
### 제2조(정의)에 해당하는 상세 규정은 다음과 같습니다.
계속되는 내용입니다.
치아보험특약(갱신형)
약관
제1관 목적
### 제1조 [특약목적]

이 특약은 부가됩니다.
"""


def test_chunk_markdown_captures_preamble_before_first_article():
    result = module.chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    assert result["preamble"]["text"] == "지엄체크 항목"


def test_chunk_markdown_produces_one_chunk_per_real_article():
    result = module.chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    ids = [chunk["id"] for chunk in result["chunks"]]
    assert ids == ["0::제1조", "0::제2조", "1::제1조"]


def test_chunk_markdown_folds_toc_and_corrupted_headings_into_body_text():
    result = module.chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    article_1 = result["chunks"][0]
    article_2 = result["chunks"][1]
    assert "이 계약은 성립됩니다." in article_1["text"]
    assert "### 제1조 [목적] 5 제4관 다음장" in article_1["text"]
    assert '여기서 "피보험자"란 사람을 말합니다.' in article_2["text"]
    assert "### 제2조(정의)에 해당하는 상세 규정은 다음과 같습니다." in article_2["text"]


def test_chunk_markdown_scopes_ids_and_labels_per_rider_section():
    result = module.chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    main, rider = result["chunks"][0], result["chunks"][-1]
    assert main["section_index"] == 0
    assert main["section_label"] == "주계약"
    assert rider["section_index"] == 1
    assert rider["section_label"] == "치아보험특약(갱신형)"
    assert rider["title"] == "특약목적"
    assert rider["path"] == "치아보험특약(갱신형) > 제1조(특약목적)"


def test_chunk_markdown_starts_section_zero_even_if_article_1_heading_is_missing():
    # observed in 삼성인터넷급여실손의료비보장보험_2605_계약전환용_약관.md: the main
    # contract's own "제1조" heading was itself corrupted by the PDF conversion,
    # so the first surviving heading in the whole document is "제5조의2"
    document = """전문
### 제5조의2 [설명 의무]

본문 내용입니다.
"""
    result = module.chunk_markdown(document, "sample.md")
    assert result["chunks"][0]["section_index"] == 0
    assert result["chunks"][0]["section_label"] == "주계약"


def test_discover_markdown_files_finds_nested_md_files(tmp_path):
    (tmp_path / "보장성").mkdir()
    (tmp_path / "보장성" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("skip me", encoding="utf-8")

    found = module.discover_markdown_files(tmp_path)

    assert found == [tmp_path / "b.md", tmp_path / "보장성" / "a.md"]


def test_main_writes_one_json_chunk_file_per_markdown_file(tmp_path):
    input_dir = tmp_path / "md"
    output_dir = tmp_path / "chunks"
    input_dir.mkdir()
    (input_dir / "sample.md").write_text(SAMPLE_DOCUMENT, encoding="utf-8")

    exit_code = module.main(["--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert exit_code == 0
    written = json.loads((output_dir / "sample.json").read_text(encoding="utf-8"))
    assert [chunk["id"] for chunk in written["chunks"]] == ["0::제1조", "0::제2조", "1::제1조"]
