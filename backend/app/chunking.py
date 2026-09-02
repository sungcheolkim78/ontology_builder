"""Table-aware PDF-to-Markdown conversion and article-level Markdown chunking.

Ported from scripts/data_prep/convert_pdfs_to_markdown.py and
scripts/data_prep/chunk_terms_markdown.py, which were built and tuned
against Korean insurance-policy PDFs (see that directory's README for the
heading/section heuristics and their known limitations). The per-page/
per-file logic there is preserved as-is; only the file-path-based I/O is
replaced with bytes/DATA_DIR-based I/O so it fits this app's upload flow
and `data_dir()` override (see app.paths).
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any

import pdfplumber

from app.paths import data_dir

DATA_DIR = data_dir()
CHUNK_DIR = data_dir() / "chunks"


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
    text = text.replace(" ", " ").replace("\x00", "")
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
    """Convert an uploaded PDF to Markdown and save it as `{stem}_raw.md`,
    matching the output layout of `app.parser.parse_to_markdown_file`."""
    safe_name = os.path.basename(filename)
    stem = Path(safe_name).stem
    markdown = convert_pdf_to_markdown(data, stem)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{stem}_raw.md"
    (DATA_DIR / out_name).write_text(markdown)

    return {"filename": out_name, "path": f"data/{out_name}"}


# --- Markdown -> per-article JSON chunks ------------------------------------

# Anchored to the full line: a TOC duplicate or a mid-sentence PDF line-wrap
# artifact always has extra text trailing the closing bracket/paren, so
# requiring the title bracket to end the line rejects both at once.
ARTICLE_HEADING_PATTERN = re.compile(
    r"^###\s*제(?P<no>\d+)조(?:의(?P<sub>\d+))?\s*(?:\[(?P<title_b>.+)\]|\((?P<title_p>.+)\))\s*$"
)


def parse_article_heading(line: str) -> dict[str, str | None] | None:
    match = ARTICLE_HEADING_PATTERN.match(line)
    if not match:
        return None
    title = match.group("title_b")
    if title is None:
        title = match.group("title_p")
    return {"article_no": match.group("no"), "sub_no": match.group("sub"), "title": title}


def guess_section_label(lines: list[str], start: int, end: int) -> str | None:
    """Best-effort rider name for the section beginning at `end`.

    A rider's name always appears somewhere in the block of text preceding
    its first article, but its exact position drifts with PDF line-wrapping
    (see scripts/data_prep/README.md), so this takes the last short,
    non-tabular mention of "특약" in the block rather than assuming adjacency
    to any one marker line.
    """
    for index in range(end - 1, start - 1, -1):
        candidate = lines[index].strip()
        if not candidate or candidate.startswith(("<!--", "※")) or "|" in candidate:
            continue
        if "특약" in candidate and len(candidate) < 60:
            return candidate
    return None


def _body_text(lines: list[str], start: int, end: int) -> str:
    """Join lines[start:end], dropping page-break artifacts, not headings."""
    kept = [
        line
        for line in lines[start:end]
        if line.strip() != "---" and not line.strip().startswith("<!-- page:")
    ]
    return "\n".join(kept).strip()


def chunk_markdown(text: str, source_name: str) -> dict:
    lines = text.splitlines()
    headings = [
        (index, parsed)
        for index, line in enumerate(lines)
        if (parsed := parse_article_heading(line)) is not None
    ]

    preamble_end = headings[0][0] if headings else len(lines)
    preamble = {
        "line_start": 1,
        "line_end": preamble_end,
        "text": _body_text(lines, 0, preamble_end),
    }

    chunks = []
    section_index = -1
    section_label = None
    section_start = 0
    for position, (index, parsed) in enumerate(headings):
        is_section_start = position == 0 or (
            parsed["article_no"] == "1" and parsed["sub_no"] is None
        )
        if is_section_start:
            section_index += 1
            section_label = (
                "주계약"
                if section_index == 0
                else guess_section_label(lines, section_start, index) or f"특약_{section_index}"
            )
            section_start = index

        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        article_ref = f"제{parsed['article_no']}조" + (
            f"의{parsed['sub_no']}" if parsed["sub_no"] else ""
        )
        chunks.append(
            {
                "id": f"{section_index}::{article_ref}",
                "section_index": section_index,
                "section_label": section_label,
                "article_no": parsed["article_no"],
                "sub_no": parsed["sub_no"],
                "title": parsed["title"],
                "path": f"{section_label} > {article_ref}({parsed['title']})",
                "line_start": index + 1,
                "line_end": end,
                "text": _body_text(lines, index + 1, end),
            }
        )

    return {"source": source_name, "preamble": preamble, "chunks": chunks}


def chunk_markdown_file(filename: str) -> dict:
    """Chunk a Markdown file already registered under DATA_DIR (e.g. the
    `{stem}_raw.md` written by `parse_to_markdown_file`/
    `convert_pdf_to_markdown_file`) and save the result as
    `{stem}.json` under CHUNK_DIR."""
    safe_name = os.path.basename(filename)
    source_path = DATA_DIR / safe_name
    if not source_path.is_file():
        raise FileNotFoundError(f"markdown file not found: {safe_name}")

    stem = Path(safe_name).stem
    result = chunk_markdown(source_path.read_text(encoding="utf-8"), safe_name)

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{stem}.json"
    (CHUNK_DIR / out_name).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {"filename": out_name, "path": f"data/chunks/{out_name}", **result}
