import os
from pathlib import Path

import anydoc

from app.paths import data_dir

DATA_DIR = data_dir()


def parse_to_markdown_file(filename: str, data: bytes) -> dict:
    safe_name = os.path.basename(filename)
    stem = Path(safe_name).stem
    ext = Path(safe_name).suffix.lstrip(".")

    markdown = anydoc.to_markdown_bytes(data, ext or None)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{stem}_raw.md"
    (DATA_DIR / out_name).write_text(markdown)

    return {"filename": out_name, "path": f"data/{out_name}"}
