#!/usr/bin/env python3
"""Generate an auditable golden QA set from Markdown files."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prompts import ANSWER_PROMPT, QUESTION_PROMPT


DEFAULT_MODEL = "openai/gpt-4o-mini"
QUESTION_TYPES = {
    "entity", "attribute", "relation", "multi_hop", "list", "boolean", "unanswerable"
}
LOGGER = logging.getLogger("prepare_goldenset")


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


def discover_markdown_files(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.md" if recursive else "*.md"
    return sorted(path for path in input_dir.glob(pattern) if path.is_file())


def limit_markdown_files(files: list[Path], max_files: int | None) -> list[Path]:
    """Return the sorted file prefix selected for this run."""
    return files if max_files is None else files[:max_files]


def numbered_lines(text: str) -> list[str]:
    return text.splitlines()


def compact_document_for_questions(document: str, max_chars: int) -> str:
    """Keep every Markdown section while fitting the question prompt budget."""
    if max_chars < 1:
        raise ValueError("question context budget must be at least 1 character")
    if len(document) <= max_chars:
        return document

    lines = document.splitlines()
    section_starts = [
        index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)
    ]
    if not section_starts:
        section_starts = [0]
    elif section_starts[0] > 0:
        section_starts.insert(0, 0)

    sections = []
    for index, start in enumerate(section_starts):
        end = section_starts[index + 1] if index + 1 < len(section_starts) else len(lines)
        sections.append(lines[start:end])

    marker = "\n[section content truncated]"
    separator = "\n\n"
    headings = [
        section[0]
        for section in sections
        if section and re.match(r"^#{1,6}\s+", section[0])
    ]
    reserved = sum(len(heading) for heading in headings) + len(separator) * (len(sections) - 1)
    if reserved >= max_chars:
        return separator.join(headings)[:max_chars]

    content_budget = max_chars - reserved
    base_budget, remainder = divmod(content_budget, len(sections))
    rendered = []
    for index, section in enumerate(sections):
        heading = section[0] if re.match(r"^#{1,6}\s+", section[0]) else ""
        body = "\n".join(section[1:] if heading else section)
        budget = base_budget + (1 if index < remainder else 0)
        if len(body) > budget:
            if budget <= len(marker):
                body = body[:budget]
            else:
                body = body[: budget - len(marker)] + marker
        rendered.append("\n".join(part for part in (heading, body) if part))
    return separator.join(rendered)[:max_chars]


def configure_logging(log_path: Path) -> logging.Logger:
    """Log progress to the terminal and one UTF-8 file for the whole run."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = LOGGER
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.info("run started; log_file=%s", log_path)
    return logger


def locate_quote(lines: list[str], quote: str) -> tuple[int, int] | None:
    """Return the exact quote's 1-based line span, ignoring LLM-supplied lines."""
    if not quote:
        return None
    document = "\n".join(lines)
    start = document.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    line_start = document.count("\n", 0, start) + 1
    line_end = document.count("\n", 0, max(start, end - 1)) + 1
    return line_start, line_end


def validate_questions(payload: dict[str, Any], expected_count: int) -> list[dict[str, Any]]:
    questions = payload.get("questions")
    if not isinstance(questions, list) or len(questions) != expected_count:
        raise ValueError(f"expected exactly {expected_count} questions")
    seen: set[str] = set()
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict) or not question.get("question"):
            raise ValueError(f"question {index} is missing text")
        question["id"] = f"q{index:03d}"
        qtype = question.get("question_type")
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"question {index} has invalid question_type: {qtype!r}")
        normalized = " ".join(question["question"].lower().split())
        if normalized in seen:
            raise ValueError(f"duplicate question at position {index}")
        seen.add(normalized)
    return questions


def merge_and_validate_answers(
    questions: list[dict[str, Any]], payload: dict[str, Any], document: str
) -> tuple[list[dict[str, Any]], list[str]]:
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise ValueError("answer response is missing an answers array")
    answer_by_id = {item.get("id"): item for item in answers if isinstance(item, dict)}
    if set(answer_by_id) != {q["id"] for q in questions}:
        raise ValueError("answer ids do not exactly match question ids")

    lines = numbered_lines(document)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for question in questions:
        answer = answer_by_id[question["id"]]
        originally_answerable = answer.get("answerable") is True
        answerable = originally_answerable
        evidence = answer.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        verified_evidence = []
        for item in evidence:
            quote = item.get("quote", "") if isinstance(item, dict) else ""
            span = locate_quote(lines, quote)
            if span is None:
                warnings.append(f"{question['id']}: evidence quote was not found exactly")
                continue
            verified_evidence.append(
                {"quote": quote, "line_start": span[0], "line_end": span[1]}
            )
        if answerable and not verified_evidence:
            warnings.append(f"{question['id']}: marked unanswerable because no evidence was verified")
            answerable = False

        records.append(
            {
                **question,
                "answerable": answerable,
                "answer": answer.get("answer") if answerable else None,
                "answer_facts": answer.get("answer_facts", []) if answerable else [],
                "evidence": verified_evidence if answerable else [],
                "notes": answer.get("notes", ""),
                "validation": {
                    "evidence_quotes_verified": bool(verified_evidence) if answerable else True,
                    "status": (
                        "downgraded_to_unanswerable"
                        if originally_answerable and not answerable
                        else "valid"
                    ),
                },
            }
        )
    return records, warnings


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_model(model_name: str):
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "langchain-openai is required; run this with the backend virtual environment"
        ) from exc
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model=model_name,
        temperature=0,
    )


def invoke_json(model, prompt: str, attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return parse_json_response(model.invoke(prompt).content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise ValueError(f"model did not return valid JSON after {attempts} attempts: {last_error}")


def generate_for_document(
    model,
    source_path: Path,
    relative_path: Path,
    question_count: int,
    question_context_chars: int = 24000,
    answer_context_chars: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    LOGGER.info("reading document: %s", relative_path)
    document = source_path.read_text(encoding="utf-8")
    if not document.strip():
        raise ValueError("document is empty")
    question_context = compact_document_for_questions(document, question_context_chars)
    LOGGER.info(
        "question context prepared: %s (%d -> %d chars)",
        relative_path,
        len(document),
        len(question_context),
    )
    answer_budget = question_context_chars if answer_context_chars is None else answer_context_chars
    answer_context = compact_document_for_questions(document, answer_budget)
    LOGGER.info(
        "answer context prepared: %s (%d -> %d chars)",
        relative_path,
        len(document),
        len(answer_context),
    )
    LOGGER.info("generating %d questions: %s", question_count, relative_path)
    question_payload = invoke_json(
        model,
        QUESTION_PROMPT.format(
            question_count=question_count,
            source_file=relative_path.as_posix(),
            document=question_context,
        ),
    )
    questions = validate_questions(question_payload, question_count)
    LOGGER.info("generating answers with source evidence: %s", relative_path)
    answer_payload = invoke_json(
        model,
        ANSWER_PROMPT.format(
            questions=json.dumps(questions, ensure_ascii=False, indent=2),
            source_file=relative_path.as_posix(),
            document=answer_context,
        ),
    )
    records, warnings = merge_and_validate_answers(questions, answer_payload, document)
    return {
        "source_file": relative_path.as_posix(),
        "source_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "questions": records,
    }, warnings


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate source-grounded golden QA data from Markdown files."
    )
    parser.add_argument("input_dir", type=Path, help="directory containing Markdown files")
    parser.add_argument("--output-dir", type=Path, default=Path("goldenset"))
    parser.add_argument("--questions-per-document", type=int, default=10)
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--max-process-files",
        type=int,
        help="maximum number of Markdown files to process; defaults to all files",
    )
    parser.add_argument(
        "--question-context-chars",
        type=int,
        default=24000,
        help="maximum Markdown characters sent to the question-generation prompt",
    )
    parser.add_argument(
        "--answer-context-chars",
        type=int,
        default=None,
        help=(
            "maximum Markdown characters sent to the answer-generation prompt; "
            "defaults to the question context size"
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="single file receiving all run logs (default: <output-dir>/prepare_goldenset.log)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path(__file__).resolve().parents[2] / "backend" / ".env")
    args = build_parser().parse_args(argv)
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    log_path = (args.log_file or output_dir / "prepare_goldenset.log").resolve()
    configure_logging(log_path)
    LOGGER.info("input_dir=%s output_dir=%s model=%s", input_dir, output_dir, args.model)
    if not input_dir.is_dir():
        LOGGER.error("input directory does not exist: %s", input_dir)
        return 2
    if args.questions_per_document < 1:
        LOGGER.error("--questions-per-document must be at least 1")
        return 2
    if args.question_context_chars < 1:
        LOGGER.error("--question-context-chars must be at least 1")
        return 2
    if args.answer_context_chars is not None and args.answer_context_chars < 1:
        LOGGER.error("--answer-context-chars must be at least 1")
        return 2
    if args.max_process_files is not None and args.max_process_files < 1:
        LOGGER.error("--max-process-files must be at least 1")
        return 2
    files = discover_markdown_files(input_dir, not args.no_recursive)
    if not files:
        LOGGER.error("no Markdown files found in %s", input_dir)
        return 2

    discovered_count = len(files)
    files = limit_markdown_files(files, args.max_process_files)
    LOGGER.info(
        "discovered %d Markdown document(s); selected %d for processing",
        discovered_count,
        len(files),
    )
    LOGGER.info("initializing model")
    model = get_model(args.model)
    results = []
    generated_count = 0
    reused_count = 0
    all_warnings = []
    failed = []
    for source_path in files:
        relative_path = source_path.relative_to(input_dir)
        output_path = output_dir / relative_path.with_suffix(".golden.json")
        if output_path.exists() and not args.overwrite:
            LOGGER.info("skip: %s (output exists; use --overwrite)", relative_path)
            try:
                results.append(json.loads(output_path.read_text(encoding="utf-8")))
                reused_count += 1
            except (OSError, json.JSONDecodeError) as exc:
                failed.append(
                    {"source_file": relative_path.as_posix(), "error": f"invalid existing output: {exc}"}
                )
            continue
        LOGGER.info("start document: %s", relative_path)
        try:
            result, warnings = generate_for_document(
                model,
                source_path,
                relative_path,
                args.questions_per_document,
                args.question_context_chars,
                args.answer_context_chars,
            )
            result["model"] = args.model
            write_json(output_path, result)
            results.append(result)
            generated_count += 1
            all_warnings.extend(f"{relative_path}: {warning}" for warning in warnings)
            LOGGER.info("completed document: %s", relative_path)
        except Exception as exc:  # continue other documents and report all failures
            failed.append({"source_file": relative_path.as_posix(), "error": str(exc)})
            LOGGER.exception("failed document: %s", relative_path)

    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "model": args.model,
        "questions_per_document": args.questions_per_document,
        "max_process_files": args.max_process_files,
        "documents_discovered": discovered_count,
        "documents_selected": len(files),
        "documents_total": len(results),
        "documents_generated": generated_count,
        "documents_reused": reused_count,
        "documents_failed": failed,
        "warnings": all_warnings,
    }
    write_json(output_dir / "manifest.json", manifest)
    with (output_dir / "goldenset.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            for question in result["questions"]:
                handle.write(
                    json.dumps(
                        {
                            "source_file": result["source_file"],
                            "source_sha256": result["source_sha256"],
                            **question,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    LOGGER.info(
        f"done: {generated_count} generated, {reused_count} reused, "
        f"{len(failed)} failure(s) -> {output_dir}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
