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


# --- PDF -> Markdown --------------------------------------------------------

# 제N장/제N편/제N관 (chapter/part/subsection) and 제N조 (article) references
# all optionally carry a parenthesized title (e.g. "제2장(보장내용)",
# "제3조(목적)") -- but chapter/part/subsection titles are just as often bare,
# unparenthesized text instead (e.g. "제1관 목적 및 용어의 정의", "제1편
# 총칙"), unlike articles, which in this pipeline are never genuine without
# one (see chunking.chunk_markdown's ARTICLE_HEADING_PATTERN, which requires
# the same). So the genuine-heading rule differs by kind:
#   - article: genuine only if it has a parenthesized title AND that title
#     runs to the end of the line AND a blank line (or nothing, i.e. it's
#     the first line seen) precedes it.
#   - chapter (including 관): genuine if -- when it does have a
#     parenthesized title -- that title runs to the end of the line, AND
#     either way a blank line (or nothing) precedes it.
# Any of those conditions failing means it's a citation, not a heading:
# trailing content after a *closed* title (as in "제3조(보험금의
# 지급사유)에 해당하는 ...") is the classic mid-sentence case; no blank line
# before it (as in "...별도의 규정이 없는 한\n제3조(보험금의 지급사유)",
# where a citation lands alone on its own line purely from PDF line-wrap,
# with the sentence it belongs to continuing on the *next* line) is the
# other -- a real heading is set off from surrounding prose, a citation
# isn't. Either way the reference gets linked as `[...]` in place instead of
# restructured into a heading line, which would otherwise misrepresent
# running prose as a new section.
CHAPTER_PATTERN = re.compile(r"^제\s*\d+\s*[장편관](?P<title>\s*\([^)]*\))?")
ARTICLE_PATTERN = re.compile(r"^제\s*\d+\s*조(?P<title>\s*\([^)]*\))?")
NUMBERED_PATTERN = re.compile(r"^\d+\.\s+\S")

REFERENCE_HEADING_PATTERNS = (
    (CHAPTER_PATTERN, "##", "chapter"),
    (ARTICLE_PATTERN, "###", "article"),
)
BULLET_PATTERN = re.compile(r"^[●■◆▶▣□◦ㆍ∙]\s*")
PAGE_NUMBER_PATTERN = re.compile(r"^[-–—]?\s*\d+\s*[-–—]?$|^\d+\s*/\s*\d+$")

# A numbered list item (e.g. "1. 보장내용") nests one level below whatever
# chapter/article heading last preceded it -- "###" directly under a chapter
# (##), "####" directly under an article (###). Without one yet, default to
# the historical flat "###" (no chapter/article context to nest under).
NUMBERED_HEADING_LEVEL = {"chapter": "###", "article": "####"}
DEFAULT_NUMBERED_HEADING_LEVEL = "###"


def markdown_text(text: str | None) -> str:
    """Lightly structure Korean headings and bullets without rewriting content."""
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    output: list[str] = []
    last_heading_kind: str | None = None
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or PAGE_NUMBER_PATTERN.fullmatch(line):
            if output and output[-1] != "":
                output.append("")
            continue

        reference = next(
            (
                (prefix, kind, match)
                for pattern, prefix, kind in REFERENCE_HEADING_PATTERNS
                if (match := pattern.match(line))
            ),
            None,
        )
        if reference:
            prefix, kind, match = reference
            has_title = match.group("title") is not None
            trailing = line[match.end():]
            preceded_by_blank = not output or output[-1] == ""
            if kind == "article":
                genuine = has_title and not trailing and preceded_by_blank
            else:
                genuine = preceded_by_blank and (not has_title or not trailing)
            if genuine:
                output.extend([f"{prefix} {line}", ""])
                last_heading_kind = kind
                continue
            line = f"[{line[:match.end()]}]{trailing}"
        elif NUMBERED_PATTERN.match(line):
            numbered_prefix = NUMBERED_HEADING_LEVEL.get(
                last_heading_kind, DEFAULT_NUMBERED_HEADING_LEVEL
            )
            output.extend([f"{numbered_prefix} {line}", ""])
            continue

        if BULLET_PATTERN.match(line):
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
