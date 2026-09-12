"""Document -> Markdown conversion: the first stage of the pdf -> markdown ->
chunked-json pipeline (see app.chunking for the second stage).

Two independent conversion paths write the same `documents/{stem}_raw/raw.md`
output shape: `parse_to_markdown_file` (generic, via the `anydoc` library) and
`convert_pdf_to_markdown_file` (PDF-only, table-aware). The latter's
page/table heuristics are ported from
scripts/data_prep/convert_pdfs_to_markdown.py, built and tuned against
Korean insurance-policy PDFs (see that directory's README for the
heading/section heuristics and their known limitations); the per-page logic
there is preserved as-is, only the file-path-based I/O is replaced with
bytes/DATA_DIR-based I/O so it fits this app's upload flow and `data_dir()`
override (see app.paths).
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any

import anydoc
import pdfplumber

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


# --- PDF -> Markdown (table-aware) -----------------------------------------

HEADING_PATTERNS = (
    (re.compile(r"^제\s*\d+\s*[장편]\b"), "##"),
    (re.compile(r"^제\s*\d+\s*조(?:\s*\([^)]*\))?"), "###"),
    (re.compile(r"^\d+\.\s+\S"), "###"),
)
BULLET_PATTERN = re.compile(r"^[●■◆▶▣□◦ㆍ∙]\s*")
PAGE_NUMBER_PATTERN = re.compile(r"^[-–—]?\s*\d+\s*[-–—]?$|^\d+\s*/\s*\d+$")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace(" ", " ").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def markdown_text(text: str | None) -> str:
    """Lightly structure Korean headings and bullets without rewriting content."""
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    output: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or PAGE_NUMBER_PATTERN.fullmatch(line):
            if output and output[-1] != "":
                output.append("")
            continue
        heading = next(
            (prefix for pattern, prefix in HEADING_PATTERNS if pattern.match(line)), None
        )
        if heading:
            output.extend([f"{heading} {line}", ""])
        elif BULLET_PATTERN.match(line):
            output.append(f"- {BULLET_PATTERN.sub('', line)}")
        else:
            output.append(line)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def clean_cell(value: str | None) -> str:
    value = clean_text(value)
    value = value.replace("|", "\\|")
    value = re.sub(r"\n+", "<br>", value)
    return value


def normalize_table(rows: list[list[str | None]]) -> list[list[str]]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [[clean_cell(cell) for cell in row] + [""] * (width - len(row)) for row in rows]

    # pdfplumber sometimes emits completely empty spacer columns around borders.
    keep_columns = [
        index for index in range(width) if any(row[index].strip() for row in normalized)
    ]
    if not keep_columns:
        return []
    normalized = [[row[index] for index in keep_columns] for row in normalized]

    # Drop border-only/empty rows, but retain a row when at least one cell has content.
    return [row for row in normalized if any(cell.strip() for cell in row)]


def is_meaningful_table(rows: list[list[str]]) -> bool:
    if len(rows) < 2 or not rows:
        return False
    width = len(rows[0])
    if width < 2:
        return False
    populated_columns = sum(any(row[col] for row in rows) for col in range(width))
    populated_cells = sum(bool(cell) for row in rows for cell in row)
    return populated_columns >= 2 and populated_cells >= 3


def table_to_markdown(rows: list[list[str]]) -> str:
    width = len(rows[0])
    header = rows[0]
    if not any(header):
        header = [f"열 {index + 1}" for index in range(width)]
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def accepted_tables(page) -> list[tuple[Any, list[list[str]]]]:
    accepted = []
    for table in page.find_tables():
        rows = normalize_table(table.extract())
        if is_meaningful_table(rows):
            accepted.append((table, rows))
    return sorted(accepted, key=lambda item: (item[0].bbox[1], item[0].bbox[0]))


def extract_band(page, top: float, bottom: float) -> str:
    if bottom - top < 2:
        return ""
    cropped = page.crop((0, max(0, top), page.width, min(page.height, bottom)))
    return markdown_text(cropped.extract_text(x_tolerance=2, y_tolerance=3))


def page_to_markdown(page, page_number: int) -> tuple[str, int]:
    tables = accepted_tables(page)
    blocks = [f"<!-- page: {page_number} -->"]
    cursor = 0.0
    table_count = 0
    for table, rows in tables:
        top, bottom = float(table.bbox[1]), float(table.bbox[3])
        preceding = extract_band(page, cursor, top - 1)
        if preceding:
            blocks.append(preceding)
        blocks.append(table_to_markdown(rows))
        table_count += 1
        cursor = max(cursor, bottom + 1)
    trailing = extract_band(page, cursor, page.height)
    if trailing:
        blocks.append(trailing)
    return "\n\n".join(blocks), table_count


def convert_pdf_to_markdown(data: bytes, title: str) -> str:
    """Render PDF bytes to table-aware Markdown, headed by `title`."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages, 1):
            page_markdown, _ = page_to_markdown(page, index)
            pages.append(page_markdown)

    return f"# {title}\n\n" + "\n\n---\n\n".join(pages) + "\n"


def convert_pdf_to_markdown_file(filename: str, data: bytes) -> dict:
    """Convert an uploaded PDF to Markdown and save it as
    documents/{stem}_raw/raw.md, matching the output layout of
    `parse_to_markdown_file`."""
    safe_name = os.path.basename(filename)
    stem = Path(safe_name).stem
    markdown = convert_pdf_to_markdown(data, stem)

    out_stem = f"{stem}_raw"
    doc_dir = document_dir_for(out_stem)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "raw.md").write_text(markdown)

    return {"filename": f"{out_stem}.md", "path": f"data/documents/{out_stem}/raw.md"}
