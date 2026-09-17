"""Document -> Markdown conversion: the first stage of the pdf -> markdown ->
chunked-json pipeline (see app.preprocess.chunking for the second stage).

Two independent conversion paths write the same `documents/{stem}_raw/raw.md`
output shape: `parse_to_markdown_file` (generic, via the `anydoc` library) and
`convert_pdf_to_markdown_file` (PDF-only). The latter calls both PDF
converters on the same bytes: `convert_insurance_policy_to_markdown` --
table/heading heuristics ported from
scripts/data_prep/convert_pdfs_to_markdown.py, built and tuned against
Korean insurance-policy PDFs (see that directory's README for the
heading/section heuristics and their known limitations); the per-page logic
there is preserved as-is, only the file-path-based I/O is replaced with
bytes/DATA_DIR-based I/O so it fits this app's upload flow and `data_dir()`
override (see app.paths) -- saved as the document's actual `raw.md`; and
`convert_general_pdf_to_markdown` -- plain per-page text, no table detection
or heading/bullet restructuring, for a PDF with no such structure to
exploit -- saved alongside as `raw0.md`, a reference copy for comparing the
two conversions rather than a document of its own (no `document_dir_for`
entry, not picked up by `/api/documents`, which only looks for `raw.md`).
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


POLICY_SECTION_LINE = re.compile(r"^제\s*\d+\s*(?:편|장|절|관)\b.*$")
POLICY_ARTICLE_LINE = re.compile(
    r"^제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\[.*\]|\(.*\))\s*$"
)


def normalize_policy_headings(markdown: str) -> str:
    """Add stable Markdown levels to standalone policy hierarchy lines.

    anydoc and already-Markdown uploads can contain policy headings as plain
    text. Restricting this to complete, line-anchored forms avoids turning
    legal references inside ordinary paragraphs into headings.
    """
    normalized: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            content = line.lstrip("#").strip()
        else:
            content = line
        if POLICY_SECTION_LINE.fullmatch(content):
            normalized.append(f"## {content}")
        elif POLICY_ARTICLE_LINE.fullmatch(content):
            normalized.append(f"### {content}")
        else:
            normalized.append(raw_line)
    return "\n".join(normalized)


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

    markdown = normalize_policy_headings(markdown)

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


# --- PDF -> Markdown --------------------------------------------------------

HEADING_PATTERNS = (
    (re.compile(r"^제\s*\d+\s*[장편관]\b"), "##"),
    # A real 제N조 heading is the article number plus its bracketed/parenthesized
    # title and nothing else on the line -- the trailing `$` is load-bearing:
    # a sentence that merely *references* an article ("제3조(보상내용) 및
    # 제4조...은 각 보장종목에 해당하는 약관을 참조하시기 바랍니다.") starts the
    # same way but keeps going past the title, so it fails this full-line match
    # and falls through to plain body text instead of becoming a false heading.
    (re.compile(r"^제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*[\(\[][^)\]]*[)\]])?\s*$"), "###"),
)
BULLET_PATTERN = re.compile(r"^[●■◆▶▣□◦ㆍ∙]\s*")
# A numbered sub-item within an article (e.g. "2. 외래제비용...") is a list
# item, not its own heading level. It's sometimes prefixed with the "㈜" note
# marker (e.g. "㈜ 1. 「국민건강보험법」..." or "㈜1. ...") when it follows a
# table footnote -- the optional prefix keeps that variant recognized too,
# instead of falling through as an unstyled, undifferentiated line.
NUMBERED_ITEM_PATTERN = re.compile(r"^(?:㈜\s*)?\d+\.\s+\S")
# A circled number (①②③...) marks a 항 (paragraph) within a 조 (article). The
# PDF text layer doesn't put a blank line before these, so they otherwise run
# on straight from the end of the previous 항's text -- force a new paragraph.
CIRCLED_NUMBER_PATTERN = re.compile(r"^[①-⑳]")
PAGE_NUMBER_PATTERN = re.compile(r"^[-–—]?\s*\d+\s*[-–—]?$|^\d+\s*/\s*\d+$")

# The cover page opens with a policy code line (e.g. "LY0816002(260626)"),
# then the policy name, then a short description, before the first 제N관
# heading. FRONT_MATTER_CODE_PATTERN identifies the code line; the
# description's start is marked by a line beginning with "※".
FRONT_MATTER_CODE_PATTERN = re.compile(r"^[A-Z]{1,6}\d{3,}(?:\([0-9]{2,10}\))?$")
GROUP_HEADING_START_PATTERN = re.compile(r"^제\s*\d+\s*관\b")


def style_cover_page(lines: list[str]) -> tuple[list[str], list[str]] | None:
    """Split a mini-cover opening into a styled block plus the remaining lines.

    The main contract and every rider (특약) in a Samsung Life policy PDF each
    restart with their own mini cover: a policy code, the policy/rider name, a
    short description, then the first 제N관 heading -- this recurs at the start
    of every such section throughout the document, not just on page 1. Returns
    (styled_lines, rest) when this shape is found at the start of `lines`, else
    None so the caller falls back to normal handling.
    """
    content = [(index, line) for index, line in enumerate(lines) if line]
    if not content:
        return None
    first_index, code = content[0]
    if not FRONT_MATTER_CODE_PATTERN.match(code):
        return None
    group_pos = next(
        (index for index, line in enumerate(lines) if GROUP_HEADING_START_PATTERN.match(line)),
        None,
    )
    if group_pos is None or group_pos <= first_index:
        return None

    body = [line for line in lines[first_index + 1 : group_pos] if line]
    note_index = next((i for i, line in enumerate(body) if line.startswith("※")), len(body))
    name_lines, desc_lines = body[:note_index], body[note_index:]
    if not name_lines:
        return None

    styled = [f"**보험코드:** {code}", "", f"# {name_lines[0]}", *name_lines[1:]]
    if desc_lines:
        styled.append("")
        styled.extend(f"> {line}" for line in desc_lines)
    styled.append("")
    return styled, lines[group_pos:]


def markdown_text(text: str | None) -> str:
    """Lightly structure Korean headings and bullets without rewriting content."""
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    lines = [raw_line.strip() for raw_line in cleaned.splitlines()]
    output: list[str] = []
    cover = style_cover_page(lines)
    if cover is not None:
        styled, lines = cover
        output.extend(styled)
    for line in lines:
        if not line or PAGE_NUMBER_PATTERN.fullmatch(line):
            if output and output[-1] != "":
                output.append("")
            continue
        heading = next(
            (prefix for pattern, prefix in HEADING_PATTERNS if pattern.match(line)), None
        )
        if heading:
            output.extend([f"{heading} {line}", ""])
        elif CIRCLED_NUMBER_PATTERN.match(line):
            if output and output[-1] != "":
                output.append("")
            output.append(line)
        elif NUMBERED_ITEM_PATTERN.match(line):
            output.append(f"- {line}")
        elif BULLET_PATTERN.match(line):
            output.append(f"- {BULLET_PATTERN.sub('', line)}")
        else:
            output.append(line)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def clean_cell(value: str | None) -> str:
    value = clean_text(value)
    value = value.replace("|", "\\|")
    value = re.sub(r"\n+", "<br>", value)
    return value


def normalize_table(rows: list[list[str | None]]) -> list[list[str]]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [
        [clean_cell(cell) for cell in row] + [""] * (width - len(row)) for row in rows
    ]

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


def convert_insurance_policy_to_markdown(data: bytes, title: str) -> str:
    """Render PDF bytes to table-aware Markdown, headed by `title`.

    Specialized for Korean insurance-policy PDFs -- see this module's
    docstring and page_to_markdown/markdown_text for the table-detection
    and 제N조-heading heuristics this relies on. For a PDF with no such
    structure, use convert_general_pdf_to_markdown instead."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages, 1):
            page_markdown, _ = page_to_markdown(page, index)
            pages.append(page_markdown)

    return f"# {title}\n\n" + "\n\n---\n\n".join(pages) + "\n"


def general_page_to_markdown(page) -> str:
    """Plain per-page text extraction for a PDF with no exploitable
    structure -- unlike page_to_markdown, this does no table detection and
    applies no heading/bullet conventions, since those are specific to
    Korean insurance policies (see markdown_text)."""
    return clean_text(page.extract_text(x_tolerance=2, y_tolerance=3))


def convert_general_pdf_to_markdown(data: bytes, title: str) -> str:
    """Render PDF bytes to plain Markdown, headed by `title`, for a PDF with
    no tables or reliable heading structure to exploit -- just cleaned
    per-page text. Use convert_insurance_policy_to_markdown instead for a
    Korean insurance-policy PDF."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            pages.append(general_page_to_markdown(page))

    return f"# {title}\n\n" + "\n\n---\n\n".join(pages) + "\n"


def convert_pdf_to_markdown_file(filename: str, data: bytes) -> dict:
    """Convert an uploaded PDF to Markdown via
    convert_insurance_policy_to_markdown and save it as
    documents/{stem}_raw/raw.md, matching the output layout of
    `parse_to_markdown_file`. Also runs convert_general_pdf_to_markdown on
    the same bytes and saves that alongside as raw0.md -- a reference copy
    for comparing the two conversions, not a document of its own (the
    returned/registered document is still just raw.md)."""
    safe_name = os.path.basename(filename)
    stem = Path(safe_name).stem
    markdown = convert_insurance_policy_to_markdown(data, stem)
    general_markdown = convert_general_pdf_to_markdown(data, stem)

    out_stem = f"{stem}_raw"
    doc_dir = document_dir_for(out_stem)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "raw.md").write_text(markdown)
    (doc_dir / "raw0.md").write_text(general_markdown)

    return {"filename": f"{out_stem}.md", "path": f"data/documents/{out_stem}/raw.md"}
