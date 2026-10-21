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


RIDER_DOCUMENT = """# 삼성가나다보험 약관

### 제1조 [목적]
주계약 본문입니다.
### 제2조 [정의]
주계약 정의입니다.

# 건강우대특약(무배당) 약관

### 제1조 [특약의 체결]
특약 본문입니다.
### 제2조 [정의]
특약 정의입니다.

(체증형)암진단특약C(무배당) 약관
## 제1편 일반사항
[특약 약관] 제1편 일반사항 제1-1조부터 제1-15조까지의 규정을 이 특약 제1편 일반사항으로 합니다.
## 제2편 개별사항
## 제1관 용어의 정의 등
제2-1조 [용어의 정의]
본문입니다.
제2-2조 [보험기간]
본문입니다.
"""


def test_a_rider_title_line_starts_a_rider_and_names_it():
    chunks = chunk_markdown(RIDER_DOCUMENT, "doc")["chunks"]

    assert [(c["rider_type"], c["rider_name"]) for c in chunks] == [
        ("main", "주계약"),
        ("main", "주계약"),
        ("rider", "건강우대특약(무배당)"),
        ("rider", "건강우대특약(무배당)"),
        ("rider", "(체증형)암진단특약C(무배당)"),
        ("rider", "(체증형)암진단특약C(무배당)"),
    ]
    assert {c["rider_name_source"] for c in chunks[2:]} == {"title"}
    # path keeps its old shape: rider > 편/관 > 조
    assert chunks[4]["path"].startswith("(체증형)암진단특약C(무배당) > 제2편 개별사항 > 제1관 용어의 정의 등 > 제2-1조")


def test_a_rider_name_wrapped_onto_two_lines_is_joined():
    text = (
        "### 제1조 [목적]\n본문\n\n"
        "<!-- page: 324 -->\n\n"
        "갑상선암및기타피부암주요치료보장\n"
        "(치료별연간1회,진단후10년)특약C(무배당) 약관\n"
        "제2-1조 [용어의 정의]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert chunks[1]["rider_name"] == "갑상선암및기타피부암주요치료보장(치료별연간1회,진단후10년)특약C(무배당)"


def test_a_line_that_merely_ends_in_teukyak_yakgwan_is_not_a_rider_title():
    text = (
        "### 제1조 [목적]\n본문\n"
        "① 회사는 특약의 부활(효력회복) 청약을 받은 경우에는 주계약 및 해당 특약 약관\n"
        "### 제2조 [정의]\n본문\n"
        "제도성특약 약관\n"
        "### 제3조 [보험금]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert {c["section_index"] for c in chunks} == {0}


def test_a_rider_without_a_title_line_falls_back_and_says_so():
    text = "### 제1조 [목적]\n본문\n### 제2조 [정의]\n본문\n### 제1조 [특약의 적용]\n본문\n"
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert chunks[2]["rider_name"] == "특약_1"
    assert chunks[2]["rider_name_source"] == "fallback"


def test_a_product_code_line_above_a_rider_title_is_not_part_of_its_name():
    text = (
        "### 제1조 [목적]\n본문\n\n"
        "JG0002003(251212)\n"
        "특별조건부특약 약관\n"
        "### 제1조 [특약의 적용]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert chunks[1]["rider_name"] == "특별조건부특약"


def test_a_part_numbered_rider_with_no_title_line_still_starts_where_numbering_restarts():
    text = (
        "### 제1조 [목적]\n본문\n"
        "### 제2조 [정의]\n본문\n"
        "# 가나특약 약관\n"
        "제2-1조 [용어]\n본문\n"
        "제2-2조 [기간]\n본문\n"
        "제2-1조 [용어]\n본문\n"  # next rider, its title lost in conversion
        "제2-2조 [기간]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [c["section_index"] for c in chunks] == [0, 0, 1, 1, 2, 2]
    assert chunks[2]["rider_name"] == "가나특약"
    assert chunks[4]["rider_name_source"] in {"guess", "fallback"}


def test_moving_on_to_the_next_part_inside_one_rider_is_not_a_new_rider():
    text = (
        "### 제1조 [목적]\n본문\n"
        "제1-1조 [적용]\n본문\n"
        "제1-2조 [정의]\n본문\n"
        "제2-1조 [용어]\n본문\n"
        "제2-2조 [기간]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert {c["section_index"] for c in chunks} == {0}


def test_a_rider_title_wrapped_in_the_middle_of_a_word_is_joined():
    text = (
        "### 제1조 [목적]\n본문\n\n"
        "(체증형)주요순환계질환특정치료보장(치료별연간1회)특약C(무배\n"
        "당,저해약환급금형) 약관\n"
        "※ 안내입니다.\n"
        "제2-1조 [용어]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert chunks[1]["rider_name"] == "(체증형)주요순환계질환특정치료보장(치료별연간1회)특약C(무배당,저해약환급금형)"
    assert chunks[1]["rider_name_source"] == "title"


def test_the_common_part_every_rider_refers_to_is_its_own_general_section():
    text = (
        "### 제1조 [목적]\n본문\n\n"
        "[특약 약관] 제1편 일반사항\n"
        "## 제1편 일반사항\n"
        "제1-1조 [목적]\n본문\n"
        "제1-2조 [정의]\n본문\n"
        "# 가나특약 약관\n"
        "제2-1조 [용어]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [(c["rider_type"], c["rider_name"]) for c in chunks] == [
        ("main", "주계약"),
        ("general", "[특약 약관] 제1편 일반사항"),
        ("general", "[특약 약관] 제1편 일반사항"),
        ("rider", "가나특약"),
    ]


def test_a_rider_title_whose_last_line_is_only_yakgwan_is_joined():
    text = (
        "### 제1조 [목적]\n본문\n\n"
        "갑상선암및기타피부암직접치료보장특약C(무배당,저해약환급금형)\n"
        "약관\n"
        "제2-1조 [용어]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert chunks[1]["rider_name"] == "갑상선암및기타피부암직접치료보장특약C(무배당,저해약환급금형)"


APPENDIX_DOCUMENT = """### 제1조 [목적]
주계약 본문입니다.
### 제2조 [정의]
주계약 정의입니다.

---

부록 약관에서 인용된 법령 내용 (가나다 순)
- 아래 법령은 고객의 이해를 돕기 위해 마련되었습니다.
- 감염병의 예방 및 관리에 관한 법률
### 제2조(정의)
이 법에서 사용하는 용어의 뜻은 다음과 같다.
### 제3조(적용)
적용 범위.
- 개인정보보호법
### 제2조(정의)
개인정보의 정의.
"""


def test_a_cited_law_in_the_appendix_is_its_own_section_not_part_of_the_last_rider():
    chunks = chunk_markdown(APPENDIX_DOCUMENT, "doc")["chunks"]

    assert [(c["rider_type"], c["rider_name"], c["article_no"]) for c in chunks] == [
        ("main", "주계약", "1"),
        ("main", "주계약", "2"),
        ("appendix", "감염병의 예방 및 관리에 관한 법률", "2"),
        ("appendix", "감염병의 예방 및 관리에 관한 법률", "3"),
        ("appendix", "개인정보보호법", "2"),
    ]
    assert len({c["id"] for c in chunks}) == len(chunks)  # 제2조 of two laws no longer collide
    assert chunks[2]["path"].startswith("부록 > 감염병의 예방 및 관리에 관한 법률 > 제2조")
    # the appendix's own disclaimer is not left in the last chunk of the contract
    assert "법령 내용" not in chunks[1]["text"]
    assert "가나다" not in chunks[1]["text"]


def test_the_appendix_starts_at_the_last_opening_line_not_at_a_table_of_contents_mention():
    text = (
        "[부록] 약관에서 인용된 법령 내용\n"  # front-matter list of what the policy contains
        "### 제1조 [목적]\n주계약 본문입니다.\n"
        "### 제2조 [정의]\n주계약 정의입니다.\n"
        "부록 약관에서 인용된 법령 내용 (가나다 순)\n"
        "- 개인정보보호법\n"
        "### 제2조(정의)\n개인정보의 정의.\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [c["rider_type"] for c in chunks] == ["main", "main", "appendix"]


def test_headings_before_the_first_article_are_front_matter_not_the_main_contract():
    text = (
        "### 제8조 [보험금의 지급절차]\n"  # a stray line of the table of contents
        "### 제20조 [계약내용의 변경 등]\n"
        "# 삼성가나다보험 약관\n"
        "## 제1관 목적\n"
        "### 제1조 [목적]\n본문\n"
        "### 제2조 [정의]\n본문\n"
    )
    result = chunk_markdown(text, "doc")

    assert [c["article_no"] for c in result["chunks"]] == ["1", "2"]
    assert {c["rider_name"] for c in result["chunks"]} == {"주계약"}
    assert "제8조" in result["preamble"]["text"]


def test_the_plan_a_rider_repeats_its_articles_for_is_recorded_and_keeps_ids_unique():
    text = (
        "### 제1조 [목적]\n본문\n"
        "# 실손의료비보장특약 약관\n"
        "### 제1조 [담보종목]\n본문\n"
        "《상해입원형》\n"
        "### 제3조 [보상내용]\n상해 입원 본문\n"
        "---\n\n<!-- page: 270 -->\n\n"
        "《종합통원형》\n"
        "### 제3조 [보상내용]\n통원 본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [c["variant"] for c in chunks] == [None, None, "상해입원형", "종합통원형"]
    assert len({c["id"] for c in chunks}) == len(chunks)
    assert chunks[3]["path"].endswith("《종합통원형》 > 제3조(보상내용)")
    # the plan's name line is the next article's, not the previous article's text
    assert "《" not in chunks[2]["text"]


def test_a_plan_continues_until_the_next_plan_line_or_the_next_rider():
    text = (
        "### 제1조 [목적]\n본문\n"
        "# 실손의료비보장특약 약관\n"
        "### 제1조 [담보종목]\n본문\n"
        "《상해입원형》\n"
        "### 제3조 [보상내용]\n본문\n"
        "### 제4조 [보상하지 않는 사항]\n본문\n"
        "《종합통원형》\n"
        "### 제3조 [보상내용]\n본문\n"
        "### 제4조 [보상하지 않는 사항]\n본문\n"
        "# 다라특약 약관\n"
        "### 제1조 [목적]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [c["variant"] for c in chunks] == [None, None, "상해입원형", "상해입원형", "종합통원형", "종합통원형", None]
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_a_marked_line_that_is_a_sentence_after_its_parenthesised_title_is_not_a_heading():
    line = "### 제20조(계약내용의 변경 등) 제1항의 절차에 따라 계약자 명의를 변경하여 특별부활(효력회복)"
    assert parse_article_heading(line) is None


@pytest.mark.parametrize(
    "line",
    [
        # table-of-contents lines: a page number ends them, or an article is listed on the same line
        "## 제5관 보험료의 납입 20",
        "## 제1관 목적 및 용어의 정의 5 제13조 [계약 전 알릴 의무] 15",
        "## 제1관 보험계약의 성립과 유지 제22조 [계약의 소멸] 20",
    ],
)
def test_parse_section_heading_rejects_table_of_contents_lines(line):
    assert parse_section_heading(line) is None


def test_a_table_of_contents_part_line_is_not_the_part_of_the_first_article():
    text = (
        "## 제7관 분쟁의 조정 등 26\n"  # front matter
        "### 제1조 [목적]\n본문\n"
        "### 제2조 [정의]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert [c["ancestors"] for c in chunks] == [[], []]


def test_a_second_contract_in_the_same_policy_is_a_main_contract_named_by_its_title():
    text = (
        "### 제1조 [목적]\n본문\n"
        "### 제2조 [정의]\n본문\n"
        "① 태아가 유산 또는 사산에 의해 출생하지 못한 경우에는 계약(특약 포함)을 무효로 합니다.\n"
        "---\n\n<!-- page: 52 -->\n\n"
        "삼성생명 우리아이행복보험 (무배당)\n"
        "2 종(출생아 가입형) 약관\n"
        "제 1 관 목적 및 용어의 정의\n"
        "### 제1조 [목적]\n본문\n"
    )
    chunks = chunk_markdown(text, "doc")["chunks"]

    assert (chunks[2]["rider_type"], chunks[2]["rider_name"]) == (
        "main",
        "삼성생명 우리아이행복보험 (무배당) 2 종(출생아 가입형)",
    )
    assert chunks[2]["rider_name_source"] == "title"
    assert "특약 포함" not in chunks[2]["rider_name"]
