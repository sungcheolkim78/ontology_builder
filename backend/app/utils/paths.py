import os
from pathlib import Path


def data_dir() -> Path:
    """Base directory for parsed documents, schemas, and the graph DB.
    Defaults to backend/data, overridable via ONTOLOGY_DATA_DIR so the test
    suite can point at a throwaway directory instead of the real one (see
    tests/conftest.py) -- test fixtures delete and recreate this tree, and
    running them against the real path corrupts whatever's actually been
    extracted so far."""
    override = os.environ.get("ONTOLOGY_DATA_DIR")
    return Path(override) if override else Path(__file__).parent.parent.parent / "data"


def documents_dir() -> Path:
    """Parent of every per-document folder (see document_dir_for). Sibling
    of graph/ (the shared graph DB) and domain_schemas/ (cross-document
    schemas) -- documents/ holds only per-document artifacts."""
    return data_dir() / "documents"


def document_dir_for(stem: str) -> Path:
    """The single folder holding everything about one document: raw.md,
    manifest.json, summary.json, discovery.json, chunks.json, versions.json,
    schema_v{N}.json, and (PDF uploads only) source.pdf -- the original bytes,
    kept alongside the lossy raw.md conversion so the frontend can offer a
    page/line-accurate PDF viewer. Centralizing this here (rather than each module
    computing its own path) is what lets a new per-document artifact kind
    be added as just another file under this folder, with no new top-level
    data/ directory and no new helper elsewhere."""
    return documents_dir() / stem


def stem_for(filename: str) -> str:
    """The synthetic, stable document id `{stem}` used to key every
    per-document artifact -- derived from a `{stem}.md`/`{stem}_raw.md`-shaped
    filename by stripping any path and the last suffix."""
    return Path(os.path.basename(filename)).stem


def raw_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "raw.md"


def raw0_path_for(stem: str) -> Path:
    """The plain per-page-text conversion kept beside raw.md for comparison
    (see app.preprocess.parser); not a document in its own right."""
    return document_dir_for(stem) / "raw0.md"


def document_path_for(filename: str) -> Path:
    """`raw.md` path for a `{stem}.md`-shaped filename, as every route
    that reads a document's own content wants it."""
    return raw_path_for(stem_for(filename))


def chunk_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "chunks.json"


def pdf_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "source.pdf"


def versions_path(stem: str) -> Path:
    return document_dir_for(stem) / "versions.json"


def schema_path_for_version(stem: str, version: int) -> Path:
    return document_dir_for(stem) / f"schema_v{version}.json"


def document_manifest_path(stem: str) -> Path:
    return document_dir_for(stem) / "manifest.json"


def discovery_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "discovery.json"


def summary_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "summary.json"


def goldenset_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "goldenset.json"


def goldenset_answers_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "goldenset_answers.json"


def progress_path_for(stem: str, operation: str) -> Path:
    return document_dir_for(stem) / "progress" / f"{operation}.json"


def resume_cache_path_for(stem: str, stage: str, index: int) -> Path:
    return document_dir_for(stem) / "resume_cache" / f"{stage}_{index}.json"


def domain_schemas_dir() -> Path:
    """Parent of every domain's folder: schemas that belong to a domain (e.g.
    "insurance_policy"), not to any one document."""
    return data_dir() / "domain_schemas"


def domain_dir_for(domain: str) -> Path:
    return domain_schemas_dir() / domain


def domain_schema_path(domain: str) -> Path:
    return domain_dir_for(domain) / "schema.json"


def domain_manifest_path(domain: str) -> Path:
    return domain_dir_for(domain) / "manifest.json"


def domain_pending_review_path(domain: str) -> Path:
    return domain_dir_for(domain) / "pending_review.json"


def document_raw_files() -> list[tuple[str, Path]]:
    """(stem, raw.md path) for every registered document, newest first --
    the single place that knows a document is "a folder under documents_dir()
    with a raw.md in it", so /api/files and /api/documents can't drift apart
    on what counts as a document."""
    if not documents_dir().is_dir():
        return []
    entries = [
        (d.name, raw_path_for(d.name))
        for d in documents_dir().iterdir()
        if d.is_dir() and not d.name.startswith(".") and raw_path_for(d.name).is_file()
    ]
    return sorted(entries, key=lambda entry: entry[1].stat().st_mtime, reverse=True)


def pdf_only_document_dirs() -> list[tuple[str, Path]]:
    """(stem, source.pdf path) for documents that have a PDF but no raw.md
    yet -- a document whose Markdown conversion was deliberately deferred to
    a separate on-demand step (POST /api/documents/{filename}/generate-md)
    instead of happening inline with the PDF download, so a slow table-aware
    conversion doesn't block the request that merely fetches the PDF (see
    e.g. app.preprocess.samsunglife_utils's download route). Kept as a
    sibling of document_raw_files() rather than folded into it, since that
    function's own contract ("has raw.md") is still exactly what /api/files
    needs -- /api/files only ever serves raw.md content, so a PDF-only
    document has nothing for it to list yet."""
    if not documents_dir().is_dir():
        return []
    entries = [
        (d.name, pdf_path_for(d.name))
        for d in documents_dir().iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and not raw_path_for(d.name).is_file()
        and pdf_path_for(d.name).is_file()
    ]
    return sorted(entries, key=lambda entry: entry[1].stat().st_mtime, reverse=True)
