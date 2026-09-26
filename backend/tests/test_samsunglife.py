import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ontology import load_document_manifest
from app.preprocess.parser import DATA_DIR
from app.preprocess.samsunglife_utils import DEFAULT_CATEGORIES, SamsungLifeTerm
from app.utils.paths import data_dir, document_dir_for


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


FAKE_TERM = SamsungLifeTerm(
    category="보장성",
    name="삼성 건강보험",
    goods_code="G123",
    sale_date="20240101",
    document_id="D1",
    status="판매중",
    classification="일반",
    currently_listed=True,
)


def test_search_samsunglife_terms_returns_matches(monkeypatch):
    monkeypatch.setattr(
        "app.main.find_terms_by_name", lambda query, categories: [FAKE_TERM]
    )
    client = TestClient(app)

    response = client.get("/api/samsunglife/terms", params={"q": "건강"})

    assert response.status_code == 200
    assert response.json() == {
        "terms": [
            {
                "category": "보장성",
                "name": "삼성 건강보험",
                "goods_code": "G123",
                "sale_date": "20240101",
                "document_id": "D1",
                "status": "판매중",
                "classification": "일반",
                "currently_listed": True,
            }
        ]
    }


def test_search_samsunglife_terms_forwards_default_categories(monkeypatch):
    captured = {}

    def fake_find(query, categories):
        captured["query"] = query
        captured["categories"] = categories
        return []

    monkeypatch.setattr("app.main.find_terms_by_name", fake_find)
    client = TestClient(app)

    client.get("/api/samsunglife/terms", params={"q": "건강"})

    assert captured == {"query": "건강", "categories": DEFAULT_CATEGORIES}


def test_search_samsunglife_terms_returns_502_on_upstream_failure(monkeypatch):
    from app.preprocess.samsunglife_utils import SamsungLifeDownloadError

    def raise_error(query, categories):
        raise SamsungLifeDownloadError("request failed after 3 attempts: boom")

    monkeypatch.setattr("app.main.find_terms_by_name", raise_error)
    client = TestClient(app)

    response = client.get("/api/samsunglife/terms", params={"q": "건강"})

    assert response.status_code == 502


def _fake_download_term_by_name(name, output_dir, categories=DEFAULT_CATEGORIES):
    target = Path(output_dir) / "보장성" / f"{name}_약관.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 fake pdf bytes")
    return {
        "category": "보장성",
        "name": name,
        "goods_code": "G123",
        "sale_date": "20240101",
        "document_id": "D1",
        "status": "판매중",
        "classification": "일반",
        "currently_listed": True,
        "source_url": "https://pcms.samsunglife.com/fake.pdf",
        "local_file": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": "deadbeef",
    }


def test_download_samsunglife_term_only_saves_pdf_without_converting(monkeypatch):
    # Regression test: markdown conversion (slow, table-aware pdfplumber) is
    # deliberately deferred to a separate POST .../generate-md call -- the
    # download route itself must never touch convert_pdf_to_markdown_file.
    def fail_if_called(filename, data):
        raise AssertionError("download route must not run markdown conversion")

    monkeypatch.setattr("app.main.download_term_by_name", _fake_download_term_by_name)
    monkeypatch.setattr("app.main.convert_pdf_to_markdown_file", fail_if_called)
    client = TestClient(app)

    response = client.post("/api/samsunglife/terms/download", json={"name": "테스트보험"})

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "테스트보험_약관_raw.md"
    assert body["samsunglife_term"]["name"] == "테스트보험"
    assert body["samsunglife_term"]["source_url"] == "https://pcms.samsunglife.com/fake.pdf"

    stem = "테스트보험_약관_raw"
    assert load_document_manifest(stem) == {
        "original_filename": "테스트보험_약관.pdf",
        "converter": "table_aware",
    }
    saved_pdf = document_dir_for(stem) / "source.pdf"
    assert saved_pdf.read_bytes() == b"%PDF-1.4 fake pdf bytes"
    assert not (document_dir_for(stem) / "raw.md").exists()


def test_download_samsunglife_term_forwards_requested_categories(monkeypatch):
    captured = {}

    def fake_download(name, output_dir, categories=DEFAULT_CATEGORIES):
        captured["categories"] = categories
        return _fake_download_term_by_name(name, output_dir, categories)

    monkeypatch.setattr("app.main.download_term_by_name", fake_download)
    client = TestClient(app)

    client.post(
        "/api/samsunglife/terms/download",
        json={"name": "테스트보험", "categories": ["어린이"]},
    )

    assert captured["categories"] == ("어린이",)


def test_download_samsunglife_term_returns_404_when_not_found(monkeypatch):
    from app.preprocess.samsunglife_utils import SamsungLifeDownloadError

    def raise_not_found(name, output_dir, categories=DEFAULT_CATEGORIES):
        raise SamsungLifeDownloadError(f"no term found named {name!r}")

    monkeypatch.setattr("app.main.download_term_by_name", raise_not_found)
    client = TestClient(app)

    response = client.post("/api/samsunglife/terms/download", json={"name": "없는상품"})

    assert response.status_code == 404


def test_download_samsunglife_term_returns_409_when_ambiguous(monkeypatch):
    from app.preprocess.samsunglife_utils import SamsungLifeDownloadError

    def raise_ambiguous(name, output_dir, categories=DEFAULT_CATEGORIES):
        raise SamsungLifeDownloadError(
            f"2 terms match {name!r} across categories (보장성, 저축성); narrow `categories` to disambiguate"
        )

    monkeypatch.setattr("app.main.download_term_by_name", raise_ambiguous)
    client = TestClient(app)

    response = client.post("/api/samsunglife/terms/download", json={"name": "중복상품"})

    assert response.status_code == 409


def test_download_samsunglife_term_returns_502_on_upstream_failure(monkeypatch):
    from app.preprocess.samsunglife_utils import SamsungLifeDownloadError

    def raise_network_error(name, output_dir, categories=DEFAULT_CATEGORIES):
        raise SamsungLifeDownloadError("request failed after 3 attempts: boom")

    monkeypatch.setattr("app.main.download_term_by_name", raise_network_error)
    client = TestClient(app)

    response = client.post("/api/samsunglife/terms/download", json={"name": "테스트보험"})

    assert response.status_code == 502


def _download_pdf_only(client, name="테스트보험"):
    response = client.post("/api/samsunglife/terms/download", json={"name": name})
    assert response.status_code == 200
    return response.json()


def _poll_generate_md(client, filename, timeout=15.0):
    """generate-md now runs the real converter in a subprocess (see
    app.preprocess.md_generation), so tests poll the GET status route
    instead of reading a synchronous response body. The timeout is generous
    since spawning a fresh worker process (re-importing this whole app) is
    slower than an in-process call."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/documents/{filename}/generate-md").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"generate-md for {filename} did not finish within {timeout}s")


# Module-level (and therefore picklable) fakes for convert_pdf_to_markdown_file.
# generate-md submits it to a ProcessPoolExecutor, so a replacement must be
# picklable by reference -- a closure defined inside a test function is not,
# and even if it were, a child process's mutations to a parent-process local
# (e.g. a captured `calls` list) never propagate back. Verification instead
# rides through the returned result dict (which really does cross the
# process boundary, via the Future) or through real disk I/O under
# data_dir() (shared with the parent test process).


def _fake_convert_writes_raw_md(filename, data):
    stem = "테스트보험_약관_raw"
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text("# 변환됨")
    return {
        "filename": f"{stem}.md",
        "path": f"data/documents/{stem}/raw.md",
        "received_filename": filename,
        "received_size": len(data),
    }


def _fake_convert_raises_value_error(filename, data):
    raise ValueError("not a valid pdf")


def _fake_convert_counts_calls_slowly(filename, data):
    marker = data_dir() / "convert_calls.txt"
    with marker.open("a") as f:
        f.write(filename + "\n")
    time.sleep(1.0)
    return {"filename": "x.md", "path": "data/documents/x/raw.md"}


def test_generate_md_converts_saved_pdf(monkeypatch):
    monkeypatch.setattr("app.main.download_term_by_name", _fake_download_term_by_name)
    client = TestClient(app)
    body = _download_pdf_only(client)
    stem = "테스트보험_약관_raw"

    monkeypatch.setattr(
        "app.preprocess.md_generation.convert_pdf_to_markdown_file",
        _fake_convert_writes_raw_md,
    )

    start_response = client.post(f"/api/documents/{body['filename']}/generate-md")
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "running"

    status = _poll_generate_md(client, body["filename"])

    assert status["status"] == "done"
    assert status["result"]["filename"] == f"{stem}.md"
    assert status["result"]["path"] == f"data/documents/{stem}/raw.md"
    # The original_filename recorded at download time (not some re-derived
    # name) is what generate-md must hand to the converter.
    assert status["result"]["received_filename"] == "테스트보험_약관.pdf"
    assert status["result"]["received_size"] == len(b"%PDF-1.4 fake pdf bytes")
    assert (document_dir_for(stem) / "raw.md").read_text() == "# 변환됨"


def test_generate_md_returns_404_when_no_pdf_saved():
    client = TestClient(app)

    response = client.post("/api/documents/does_not_exist_raw.md/generate-md")

    assert response.status_code == 404


def test_generate_md_returns_400_when_markdown_already_generated(monkeypatch):
    from app.preprocess.parser import raw_stem_for

    monkeypatch.setattr("app.main.download_term_by_name", _fake_download_term_by_name)
    client = TestClient(app)
    body = _download_pdf_only(client)
    stem = raw_stem_for("테스트보험_약관.pdf")
    (document_dir_for(stem) / "raw.md").write_text("already here")

    response = client.post(f"/api/documents/{body['filename']}/generate-md")

    assert response.status_code == 400


def test_generate_md_reports_error_status_when_conversion_fails(monkeypatch):
    monkeypatch.setattr("app.main.download_term_by_name", _fake_download_term_by_name)
    client = TestClient(app)
    body = _download_pdf_only(client)

    monkeypatch.setattr(
        "app.preprocess.md_generation.convert_pdf_to_markdown_file",
        _fake_convert_raises_value_error,
    )

    start_response = client.post(f"/api/documents/{body['filename']}/generate-md")
    assert start_response.status_code == 200

    status = _poll_generate_md(client, body["filename"])

    assert status["status"] == "error"
    assert "not a valid pdf" in status["detail"]


def test_generate_md_get_status_is_idle_when_never_started():
    client = TestClient(app)

    response = client.get("/api/documents/does_not_exist_raw.md/generate-md")

    assert response.status_code == 200
    assert response.json() == {"status": "idle", "detail": None, "result": None}


def test_generate_md_second_post_does_not_start_a_duplicate_conversion(monkeypatch):
    monkeypatch.setattr("app.main.download_term_by_name", _fake_download_term_by_name)
    monkeypatch.setattr(
        "app.preprocess.md_generation.convert_pdf_to_markdown_file",
        _fake_convert_counts_calls_slowly,
    )
    client = TestClient(app)
    body = _download_pdf_only(client)

    first = client.post(f"/api/documents/{body['filename']}/generate-md")
    second = client.post(f"/api/documents/{body['filename']}/generate-md")
    assert first.json()["status"] == "running"
    assert second.json()["status"] == "running"

    _poll_generate_md(client, body["filename"])

    marker = data_dir() / "convert_calls.txt"
    assert marker.read_text().splitlines() == ["테스트보험_약관.pdf"]
