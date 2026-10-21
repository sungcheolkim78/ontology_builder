import json

import pytest

from app.preprocess.chunking import (
    chunk_markdown,
    chunk_markdown_file,
    guess_section_label,
    parse_article_heading,
    parse_section_heading,
)
from app.utils.paths import document_dir_for


def test_parse_article_heading_matches_bracket_title():
    assert parse_article_heading("### 제1조 [목적]") == {
        "article_no": "1",
        "sub_no": None,
        "title": "목적",
    }


@pytest.mark.parametrize(
    ("line", "article_no", "sub_no"),
    [
        ("### 제4 조 [보험금 지급에 관한 세부규정]", "4", None),
        ("### 제2조의 2 [용어의 정의2]", "2", "2"),
    ],
)
def test_parse_article_heading_accepts_pdf_inserted_spaces(line, article_no, sub_no):
    result = parse_article_heading(line)
    assert result["article_no"] == article_no
    assert result["sub_no"] == sub_no


@pytest.mark.parametrize(
    "line",
    ["제1관 목적 및 용어의 정의", "제 1 관 목적 및 용어의 정의", "## 제1관 목적"],
)
def test_parse_section_heading_accepts_korean_policy_section_forms(line):
    assert parse_section_heading(line) == {
        "level": "관",
        "no": "1",
        "title": "목적 및 용어의 정의" if "용어" in line else "목적",
    }


@pytest.mark.parametrize(
    ("line", "article_no", "sub_no", "title"),
    [
        # the converter sometimes drops the `###`
        ("제26조 [보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지]", "26", None, "보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지"),
        # riders number articles by part: 제2-1조, 제2-1조의2
        ("제2-1조 [용어의 정의]", "2-1", None, "용어의 정의"),
        ("제2-1조의3 [“급성심근경색증”의 정의 및 진단확정]", "2-1", "3", "“급성심근경색증”의 정의 및 진단확정"),
        ("제4-36조 (통신판매시 준수사항)", "4-36", None, "통신판매시 준수사항"),
        ("### 제1-2조 [목적]", "1-2", None, "목적"),
    ],
)
def test_parse_article_heading_accepts_headings_without_markdown_marker_and_part_numbers(
    line, article_no, sub_no, title
):
    assert parse_article_heading(line) == {"article_no": article_no, "sub_no": sub_no, "title": title}


@pytest.mark.parametrize(
    "line",
    [
        # a body sentence that starts with a citation, with no `###` to vouch for it
        "제20조(계약내용의 변경 등) 제1항의 절차에 따라 계약자 명의를 보험수익자로 변경하여 계약의 특별부활(효력",
        "제1조 [목적]에 따라 보험금을 지급합니다.",
        "제3조 [목적] 5",
    ],
)
def test_parse_article_heading_rejects_unmarked_lines_that_are_not_whole_headings(line):
    assert parse_article_heading(line) is None


def test_parse_article_heading_rejects_midsentence_corruption():
    result = parse_article_heading(
        "### 제3조(보험금의 지급사유)에 해당하는 피보험자의 위험을 보장하기 위하여 체결됩니다."
    )
    assert result is None


def test_guess_section_label_finds_last_rider_mention_in_block():
    lines = [
        "---",
        "재식립임플란트치료보장특약(갱신형,무배당)",
        "약관",
        "※ 이 특약의 갱신주기는 10년으로 합니다.",
        "제1관 목적 및 용어의 정의",
        "### 제1조 [목적]",
    ]
    assert guess_section_label(lines, 0, 5) == "재식립임플란트치료보장특약(갱신형,무배당)"


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
    result = chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    assert result["preamble"]["text"] == "지엄체크 항목"


def test_chunk_markdown_produces_one_chunk_per_real_article():
    result = chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    ids = [chunk["id"] for chunk in result["chunks"]]
    assert ids == ["0::제1조", "0::제2조", "1::제1조"]


def test_chunk_markdown_scopes_ids_and_labels_per_rider_section():
    result = chunk_markdown(SAMPLE_DOCUMENT, "sample.md")
    main, rider = result["chunks"][0], result["chunks"][-1]
    assert main["section_index"] == 0
    assert main["section_label"] == "주계약"
    assert rider["section_index"] == 1
    assert rider["section_label"] == "치아보험특약(갱신형)"
    assert rider["title"] == "특약목적"
    assert rider["ancestors"] == [{"level": "관", "no": "1", "title": "목적"}]
    assert rider["path"] == "치아보험특약(갱신형) > 제1관 목적 > 제1조(특약목적)"


def test_chunk_markdown_preserves_section_heading_on_article_chunk():
    result = chunk_markdown(
        "제2관 보험금의 지급\n### 제4 조 [보험금 지급에 관한 세부규정]\n본문",
        "sample.md",
    )
    chunk = result["chunks"][0]
    assert chunk["article_no"] == "4"
    assert chunk["ancestors"] == [{"level": "관", "no": "2", "title": "보험금의 지급"}]


def test_chunk_markdown_file_reads_from_document_dir_and_writes_chunks_json():
    doc_dir = document_dir_for("sample_raw")
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "raw.md").write_text(SAMPLE_DOCUMENT, encoding="utf-8")

    result = chunk_markdown_file("sample_raw")

    assert result["filename"] == "chunks.json"
    assert result["path"] == "data/documents/sample_raw/chunks.json"
    assert [chunk["id"] for chunk in result["chunks"]] == ["0::제1조", "0::제2조", "1::제1조"]

    saved = json.loads((doc_dir / "chunks.json").read_text(encoding="utf-8"))
    assert saved["source"] == "sample_raw"
    assert len(saved["chunks"]) == 3


def test_chunk_markdown_file_raises_for_missing_document():
    with pytest.raises(FileNotFoundError):
        chunk_markdown_file("does_not_exist")
