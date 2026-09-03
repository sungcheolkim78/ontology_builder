import os
from pathlib import Path

import anydoc

from app.paths import data_dir, document_dir_for

DATA_DIR = data_dir()


def parse_to_markdown_file(filename: str, data: bytes) -> dict:
    safe_name = os.path.basename(filename)
    stem = Path(safe_name).stem
    ext = Path(safe_name).suffix.lstrip(".")

    if ext.lower() == "md":
        # Already markdown -- anydoc doesn't accept "md" as a format (it only
        # converts *into* markdown from doc/pdf/etc.), and running it through
        # would be a pointless round-trip. Register the upload as-is instead.
        try:
            markdown = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"invalid utf-8 in markdown file: {e}") from e
    else:
        markdown = anydoc.to_markdown_bytes(data, ext or None)

    # "filename" stays {stem}_raw.md -- a synthetic, stable identifier the
    # rest of the app (and the frontend) treats as opaque, decoupled from
    # where the file actually lives on disk (see app.paths.document_dir_for).
    # The document folder is keyed by *this* stem (including "_raw"), since
    # that's what _stem() later derives back from the returned filename.
    out_stem = f"{stem}_raw"
    doc_dir = document_dir_for(out_stem)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "raw.md").write_text(markdown)

    return {"filename": f"{out_stem}.md", "path": f"data/documents/{out_stem}/raw.md"}
