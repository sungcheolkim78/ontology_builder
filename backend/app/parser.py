import os
from pathlib import Path

import anydoc

from app.paths import data_dir

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

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{stem}_raw.md"
    (DATA_DIR / out_name).write_text(markdown)

    return {"filename": out_name, "path": f"data/{out_name}"}
