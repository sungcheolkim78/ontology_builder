"""Markdown -> per-article JSON chunking, the second stage of the
pdf -> markdown -> chunked-json pipeline (see app.parser for the first
stage, which produces the raw.md this module reads).

Ported from scripts/data_prep/chunk_terms_markdown.py, built and tuned
against Korean insurance-policy PDFs (see that directory's README for the
heading/section heuristics and their known limitations).
"""

from __future__ import annotations

import json
import re

from app.paths import data_dir, document_dir_for

DATA_DIR = data_dir()

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


def chunk_markdown_file(stem: str) -> dict:
    """Chunk the document's own raw.md (written by parse_to_markdown_file/
    convert_pdf_to_markdown_file, both in app.parser) and save the result as
    chunks.json in that same document folder."""
    doc_dir = document_dir_for(stem)
    source_path = doc_dir / "raw.md"
    if not source_path.is_file():
        raise FileNotFoundError(f"markdown file not found for document: {stem}")

    result = chunk_markdown(source_path.read_text(encoding="utf-8"), stem)

    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "chunks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {"filename": "chunks.json", "path": f"data/documents/{stem}/chunks.json", **result}
