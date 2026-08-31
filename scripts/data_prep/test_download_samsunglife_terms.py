import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("download_samsunglife_terms.py")
SPEC = importlib.util.spec_from_file_location("download_samsunglife_terms", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def product(name, date="20260101", listed=True):
    return module.Product(
        category="어린이",
        name=name,
        goods_code="CODE",
        sale_date=date,
        document_id="DOC",
        status="상품운용",
        classification="개인>어린이",
        currently_listed=listed,
    )


def test_unique_products_preserves_first_product():
    result = module.unique_products([product("보험 A"), product("보험 A"), product("보험 B")])
    assert [item.name for item in result] == ["보험 A", "보험 B"]


def test_safe_filename_removes_unsafe_characters():
    assert module.safe_filename('보험/A:*?"<>|[특약]') == "보험_A_특약_약관.pdf"


def test_pdf_path_pattern_matches_viewer_javascript():
    html = '''"filepath" : '../uploadDir/doc/2026/0101/CODE/301/123.pdf','''
    match = module.PDF_PATH_PATTERN.search(html)
    assert match is not None
    assert match.group(1) == "../uploadDir/doc/2026/0101/CODE/301/123.pdf"


def test_product_form_targets_individual_category():
    form = module.product_form("보장성", 2, 100)
    assert form["mCode"] == "개인"
    assert form["gCode"] == "보장성"
    assert form["pageNo"] == 2
