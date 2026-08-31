#!/usr/bin/env python3
"""Convert Korean insurance-policy PDFs to table-aware Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover - exercised by user environments
    raise SystemExit(
        "pdfplumber is required. Install scripts/data_prep/requirements.txt first."
    ) from exc


HEADING_PATTERNS = (
    (re.compile(r"^제\s*\d+\s*[장편]\b"), "##"),
    (re.compile(r"^제\s*\d+\s*조(?:\s*\([^)]*\))?"), "###"),
    (re.compile(r"^\d+\.\s+\S"), "###"),
)
BULLET_PATTERN = re.compile(r"^[●■◆▶▣□◦ㆍ∙]\s*")
PAGE_NUMBER_PATTERN = re.compile(r"^[-–—]?\s*\d+\s*[-–—]?$|^\d+\s*/\s*\d+$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ").replace("\x00", "")
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


def convert_pdf(source: Path, destination: Path) -> dict[str, Any]:
    pages: list[str] = []
    table_count = 0
    with pdfplumber.open(source) as pdf:
        total_pages = len(pdf.pages)
        for index, page in enumerate(pdf.pages, 1):
            page_markdown, page_tables = page_to_markdown(page, index)
            pages.append(page_markdown)
            table_count += page_tables
            if index % 100 == 0 or index == total_pages:
                print(f"  page {index}/{total_pages}", flush=True)

    document = (
        f"# {source.stem}\n\n"
        f"<!-- source_pdf: {source.as_posix()} -->\n\n"
        + "\n\n---\n\n".join(pages)
        + "\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(destination)
    return {
        "source_pdf": str(source),
        "source_sha256": sha256_file(source),
        "markdown_file": str(destination),
        "pages": len(pages),
        "tables": table_count,
        "characters": len(document),
    }


def discover_pdfs(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.rglob("*.pdf") if path.is_file())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Korean PDFs to table-aware Markdown."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw/pdf"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/md"))
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not input_dir.is_dir():
        print(f"error: input directory does not exist: {input_dir}", file=sys.stderr)
        return 2
    pdfs = discover_pdfs(input_dir)
    if not pdfs:
        print(f"error: no PDF files found under {input_dir}", file=sys.stderr)
        return 2

    converted: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    reused = 0
    for index, source in enumerate(pdfs, 1):
        relative = source.relative_to(input_dir)
        destination = output_dir / relative.with_suffix(".md")
        print(f"[{index}/{len(pdfs)}] {relative}", flush=True)
        if destination.exists() and not args.overwrite:
            print("  reused (use --overwrite to regenerate)", flush=True)
            reused += 1
            continue
        try:
            converted.append(convert_pdf(source, destination))
        except Exception as exc:
            failures.append({"source_pdf": str(source), "error": str(exc)})
            print(f"  failed: {exc}", file=sys.stderr, flush=True)

    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "converted": converted,
        "reused_count": reused,
        "failures": failures,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"done: {len(converted)} converted, {reused} reused, {len(failures)} failed",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

