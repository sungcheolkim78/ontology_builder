from app.utils.paths import (
    chunk_path_for,
    data_dir,
    document_dir_for,
    document_path_for,
    document_raw_files,
    documents_dir,
    stem_for,
)


def test_documents_dir_is_a_subdirectory_of_data_dir():
    assert documents_dir() == data_dir() / "documents"


def test_document_dir_for_nests_under_documents_dir():
    assert document_dir_for("report_raw") == documents_dir() / "report_raw"


def test_stem_for_strips_directory_and_suffix():
    assert stem_for("report_raw.md") == "report_raw"


def test_stem_for_strips_a_path_not_just_a_bare_filename():
    assert stem_for("some/dir/report_raw.md") == "report_raw"


def test_document_path_for_is_raw_md_under_the_stems_document_dir():
    assert document_path_for("report_raw.md") == document_dir_for("report_raw") / "raw.md"


def test_chunk_path_for_is_chunks_json_under_the_document_dir():
    assert chunk_path_for("report_raw") == document_dir_for("report_raw") / "chunks.json"


def test_document_raw_files_is_empty_when_documents_dir_is_absent():
    assert document_raw_files() == []


def test_document_raw_files_lists_only_dirs_with_a_raw_md(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.documents_dir", lambda: tmp_path)

    with_raw = tmp_path / "with_raw"
    with_raw.mkdir()
    (with_raw / "raw.md").write_text("content")

    without_raw = tmp_path / "without_raw"
    without_raw.mkdir()

    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "raw.md").write_text("content")

    entries = document_raw_files()
    assert entries == [("with_raw", with_raw / "raw.md")]
