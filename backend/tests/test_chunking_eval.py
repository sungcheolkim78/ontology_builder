from app.preprocess.chunking_eval import (
    normalize_rider_name,
    parse_product_toc,
    score_article_coverage,
    score_article_sequence,
)

TOC_TEXT = """앞부분
상품 목차
주계약
삼성가나다보험(2601)(무배당) 1
제도성특약
건강우대특약(무배당) 52
특정신체부위·질병보장제한부인수특약 73
부록
[부록]약관에서인용된법령내용 81

---

<!-- page: 32 -->
주계약 약관
"""


def test_parse_product_toc_reads_main_riders_and_appendix():
    toc = parse_product_toc(TOC_TEXT)
    assert toc["main"] == "삼성가나다보험(2601)(무배당)"
    assert toc["riders"] == ["건강우대특약(무배당)", "특정신체부위·질병보장제한부인수특약"]
    assert toc["appendix"] == ["[부록]약관에서인용된법령내용"]


def test_parse_product_toc_is_none_without_a_product_toc_block():
    assert parse_product_toc("# 제목\n\n본문\n") is None


def test_normalize_rider_name_unifies_marker_spaces_dots_and_suffix():
    assert normalize_rider_name("# 특정신체부위∙질병보장제한부인수특약 약관") == "특정신체부위질병보장제한부인수특약"
    assert normalize_rider_name("운전·탑승부담보특약") == normalize_rider_name("운전탑승부담보특약")
    assert normalize_rider_name("건강우대특약(무배당)") == "건강우대특약(무배당)"


def test_article_sequence_counts_duplicates_gaps_and_regressions():
    def chunk(no, section=0):
        return {"section_index": section, "section_label": "x", "article_no": str(no), "sub_no": None}

    result = score_article_sequence([chunk(1), chunk(2), chunk(4), chunk(2), chunk(2)])
    assert result["gaps"] == 1
    assert result["regressions"] == 1
    assert result["duplicate_articles"] == 2


def test_article_coverage_counts_heading_lines_without_a_chunk():
    raw = "### 제1조 [목적]\n본문\n제2-1조 [용어]\n본문\n제3조 [목적] 5\n"
    covered = {"chunks": [{"line_start": 1}]}
    result = score_article_coverage(covered, raw)
    assert (result["heading_lines"], result["chunked"], result["missed"]) == (2, 1, 1)


def test_parse_product_toc_follows_the_list_across_pages_and_joins_wrapped_names():
    text = """상품 목차
주계약
삼성가나다보험(2601)(무배당) 1
특약 ※특약은 해당 보험료를 납입한 경우에만 적용합니다.
가나특약C(무배당) 10

---

<!-- page: 37 -->

초기집중보장형프리미엄클래스Ⅱ암주요치료보장(상급종합병원플러스)특약C(무
배당,저해약환급금형) 462
다라특약(무배당) 500
부록
[부록]약관에서인용된법령내용 600

---

<!-- page: 38 -->

주계약 약관
"""
    toc = parse_product_toc(text)
    assert toc["riders"] == [
        "가나특약C(무배당)",
        "초기집중보장형프리미엄클래스Ⅱ암주요치료보장(상급종합병원플러스)특약C(무배당,저해약환급금형)",
        "다라특약(무배당)",
    ]
    assert toc["appendix"] == ["[부록]약관에서인용된법령내용"]


def test_parse_product_toc_keeps_a_wrapped_entry_whose_page_number_was_lost():
    text = """상품 목차
주계약
삼성가나다보험(2601)(무배당) 1
특약
초기집중보장형암주요치료보장특약C(무

배당,저해약환급금형)
다라특약(무배당) 500
"""
    assert parse_product_toc(text)["riders"] == [
        "초기집중보장형암주요치료보장특약C(무배당,저해약환급금형)",
        "다라특약(무배당)",
    ]
