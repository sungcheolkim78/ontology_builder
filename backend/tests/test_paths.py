from app.paths import data_dir, document_dir_for, documents_dir


def test_documents_dir_is_a_subdirectory_of_data_dir():
    assert documents_dir() == data_dir() / "documents"


def test_document_dir_for_nests_under_documents_dir():
    assert document_dir_for("report_raw") == documents_dir() / "report_raw"
