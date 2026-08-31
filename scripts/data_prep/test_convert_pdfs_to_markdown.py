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


def test_markdown_text_removes_standalone_page_number():
    assert module.markdown_text("내용\n- 12 -\n다음") == "내용\n\n다음"
