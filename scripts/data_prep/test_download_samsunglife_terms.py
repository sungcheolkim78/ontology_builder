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


def file_record(category, name, **overrides):
    record = {
        "category": category,
        "product_name": name,
        "goods_code": "CODE",
        "action": "downloaded",
    }
    record.update(overrides)
    return record


def test_merge_file_records_appends_new_entries():
    existing = [file_record("어린이", "보험 A")]
    new = [file_record("어린이", "보험 B")]
    merged = module.merge_file_records(existing, new)
    assert [record["product_name"] for record in merged] == ["보험 A", "보험 B"]


def test_merge_file_records_updates_matching_entry_in_place():
    existing = [file_record("어린이", "보험 A", action="downloaded"), file_record("어린이", "보험 B")]
    new = [file_record("어린이", "보험 A", action="reused")]
    merged = module.merge_file_records(existing, new)
    assert [record["product_name"] for record in merged] == ["보험 A", "보험 B"]
    assert merged[0]["action"] == "reused"


def test_merge_categories_preserves_order_and_dedupes():
    assert module.merge_categories(["보장성"], ["저축성", "보장성"]) == ["보장성", "저축성"]


def test_load_existing_manifest_returns_none_when_missing(tmp_path):
    assert module.load_existing_manifest(tmp_path / "manifest.json") is None


def test_load_existing_manifest_returns_none_on_bad_json(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("not json", encoding="utf-8")
    assert module.load_existing_manifest(manifest_path) is None


def test_select_products_returns_fewer_than_count_when_universe_exhausted(monkeypatch):
    monkeypatch.setattr(
        module, "fetch_current_products", lambda category, timeout: [product("보험 A")]
    )
    monkeypatch.setattr(
        module, "fetch_all_products", lambda category, timeout: [product("보험 A"), product("보험 B")]
    )
    selected = module.select_products("어린이", 5, timeout=10)
    assert [item.name for item in selected] == ["보험 A", "보험 B"]


def test_select_products_falls_back_to_current_when_historical_fetch_fails(monkeypatch):
    monkeypatch.setattr(
        module, "fetch_current_products", lambda category, timeout: [product("보험 A")]
    )

    def raise_error(category, timeout):
        raise module.DownloadError("boom")

    monkeypatch.setattr(module, "fetch_all_products", raise_error)
    selected = module.select_products("어린이", 5, timeout=10)
    assert [item.name for item in selected] == ["보험 A"]


def test_select_products_excludes_already_downloaded_current_products(monkeypatch):
    monkeypatch.setattr(
        module,
        "fetch_current_products",
        lambda category, timeout: [product("보험 A"), product("보험 B"), product("보험 C")],
    )
    selected = module.select_products("어린이", 2, timeout=10, exclude=frozenset({"보험 a"}))
    assert [item.name for item in selected] == ["보험 B", "보험 C"]


def test_select_products_supplements_with_historical_when_current_exhausted_by_exclude(
    monkeypatch,
):
    monkeypatch.setattr(
        module, "fetch_current_products", lambda category, timeout: [product("보험 A")]
    )
    monkeypatch.setattr(
        module,
        "fetch_all_products",
        lambda category, timeout: [product("보험 A"), product("보험 B")],
    )
    selected = module.select_products("어린이", 1, timeout=10, exclude=frozenset({"보험 a"}))
    assert [item.name for item in selected] == ["보험 B"]


def test_downloaded_names_by_category_groups_and_normalizes():
    files = [
        file_record("어린이", "보험 A"),
        file_record("어린이", "보험 B"),
        file_record("보장성", "보험 C"),
    ]
    grouped = module.downloaded_names_by_category(files)
    assert grouped == {
        "어린이": {"보험 a", "보험 b"},
        "보장성": {"보험 c"},
    }


def test_missing_download_records_finds_absent_files(tmp_path):
    present = tmp_path / "present.pdf"
    present.write_bytes(b"%PDF-1.4")
    records = [
        file_record("어린이", "보험 A", local_file=str(present)),
        file_record("어린이", "보험 B", local_file=str(tmp_path / "missing.pdf")),
        file_record("어린이", "보험 C", local_file=str(tmp_path / "dry_run.pdf"), action="dry_run"),
    ]
    missing = module.missing_download_records(records)
    assert [record["product_name"] for record in missing] == ["보험 B", "보험 C"]


def test_redownload_from_record_writes_file_and_updates_metadata(tmp_path, monkeypatch):
    target = tmp_path / "missing.pdf"
    record = file_record(
        "어린이",
        "보험 B",
        local_file=str(target),
        source_url="https://example.test/b.pdf",
        action="dry_run",
    )
    monkeypatch.setattr(module, "request_bytes", lambda url, **kwargs: b"%PDF-1.4 content")
    updated = module.redownload_from_record(record, timeout=10)
    assert target.read_bytes() == b"%PDF-1.4 content"
    assert updated["action"] == "downloaded"
    assert updated["size_bytes"] == len(b"%PDF-1.4 content")
    assert updated["sha256"]


def test_redownload_from_record_rejects_non_pdf_content(tmp_path, monkeypatch):
    target = tmp_path / "missing.pdf"
    record = file_record(
        "어린이", "보험 B", local_file=str(target), source_url="https://example.test/b.pdf"
    )
    monkeypatch.setattr(module, "request_bytes", lambda url, **kwargs: b"not a pdf")
    try:
        module.redownload_from_record(record, timeout=10)
        assert False, "expected DownloadError"
    except module.DownloadError:
        pass
    assert not target.exists()


def test_redownload_from_record_requires_source_url():
    record = file_record("어린이", "보험 B", local_file="/tmp/x.pdf")
    record.pop("source_url", None)
    try:
        module.redownload_from_record(record, timeout=10)
        assert False, "expected DownloadError"
    except module.DownloadError:
        pass
