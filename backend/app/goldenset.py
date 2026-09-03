"""Document-grounded golden QA generation, adapted from
scripts/prepare_goldenset/prepare_goldenset.py so a single already-uploaded
document can get a golden set from a UI button click instead of only via that
script's offline CLI pass over a folder of Markdown files.

Deliberately reads the whole document, never chunks.chunk_markdown_file's
per-article chunks -- see the module-level rationale in app.ontology for why
discover_ontology_from_chunks/generate_schema_from_chunks/
extract_graph_from_chunks all chunk: those pipelines exist to make LLM calls
that would otherwise blow the context window. A golden set is the opposite
kind of artifact -- it's the ground truth *used to validate* those pipelines'
output, generated once and curated, not a hot path -- so building it by
chunking would risk baking the same chunking pipeline's blind spots (a
locally-scoped view, consolidation merge mistakes) into the ground truth
meant to catch them. compact_document_for_questions below keeps every
section but truncates within a character budget instead, so a large
document still gets a whole-document-shaped view (every section
represented) rather than an independent per-chunk one.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from app.chat import get_chat_model
from app.ontology import parse_json_response
from app.paths import document_dir_for
from app.prompts import ANSWER_PROMPT, QUESTION_PROMPT
from app.telemetry import invoke_with_telemetry

logger = logging.getLogger(__name__)

QUESTION_TYPES = {
    "entity", "attribute", "relation", "multi_hop", "list", "boolean", "unanswerable"
}

DEFAULT_QUESTION_COUNT = 10
DEFAULT_CONTEXT_CHARS = 24_000


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


def _locate_quote(lines: list[str], quote: str) -> tuple[int, int] | None:
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


def _validate_questions(payload: dict, expected_count: int) -> list[dict]:
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


def _merge_and_validate_answers(
    questions: list[dict], payload: dict, document: str
) -> tuple[list[dict], list[str]]:
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise ValueError("answer response is missing an answers array")
    answer_by_id = {item.get("id"): item for item in answers if isinstance(item, dict)}
    if set(answer_by_id) != {q["id"] for q in questions}:
        raise ValueError("answer ids do not exactly match question ids")

    lines = document.splitlines()
    records: list[dict] = []
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
            span = _locate_quote(lines, quote)
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


def generate_goldenset(
    document_text: str,
    source_name: str,
    question_count: int = DEFAULT_QUESTION_COUNT,
    question_context_chars: int = DEFAULT_CONTEXT_CHARS,
    answer_context_chars: int | None = None,
) -> dict:
    """Generates a golden QA set for one whole document: a question-generation
    LLM call over a section-preserving, budget-compacted view of the
    document, then a separate answer-generation call whose evidence quotes
    are re-verified in code against the *full* original document text (not
    the compacted view) before being accepted -- see
    scripts/prepare_goldenset/README.md for the same two-call, verify-in-code
    design this mirrors."""
    if not document_text.strip():
        raise ValueError("document is empty")
    if question_count < 1:
        raise ValueError("question_count must be at least 1")

    model = get_chat_model()
    question_context = compact_document_for_questions(document_text, question_context_chars)
    answer_budget = question_context_chars if answer_context_chars is None else answer_context_chars
    answer_context = compact_document_for_questions(document_text, answer_budget)

    question_response = invoke_with_telemetry(
        "goldenset.generate_questions",
        model,
        QUESTION_PROMPT.format(
            question_count=question_count, source_file=source_name, document=question_context
        ),
    )
    questions = _validate_questions(parse_json_response(question_response.content), question_count)

    answer_response = invoke_with_telemetry(
        "goldenset.generate_answers",
        model,
        ANSWER_PROMPT.format(
            questions=json.dumps(questions, ensure_ascii=False, indent=2),
            source_file=source_name,
            document=answer_context,
        ),
    )
    records, warnings = _merge_and_validate_answers(
        questions, parse_json_response(answer_response.content), document_text
    )

    return {
        "source_file": source_name,
        "source_sha256": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
        "questions": records,
        "warnings": warnings,
    }


def goldenset_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "goldenset.json"


def save_goldenset(stem: str, report: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    goldenset_path_for(stem).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_goldenset(stem: str) -> dict | None:
    path = goldenset_path_for(stem)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# Generated-answer persistence -----------------------------------------------
#
# A golden set's own questions/golden-answers (above) are generated once and
# curated; this section is the opposite -- a running log of what this app's
# *own* GraphRAG pipeline actually answered for each question, recorded every
# time someone clicks "답변 생성" in the UI, so that answer (and the schema
# version/related nodes/etc. it came from) doesn't just flash on screen and
# disappear. Kept as a per-question *list* (append-only, never overwritten)
# rather than one record per question, since the schema evolves over time --
# an answer generated against schema version 3 says nothing reliable about
# version 4, but throwing it away would lose a real data point about how
# version 3 performed. latest_goldenset_answers() is the read side: for the
# document's *current* active schema version specifically, since an answer
# generated against a since-changed schema is no longer trustworthy as "the"
# current answer for that question.
def goldenset_answers_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "goldenset_answers.json"


def _load_answers(stem: str) -> dict:
    path = goldenset_answers_path_for(stem)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_answers(stem: str, answers: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    goldenset_answers_path_for(stem).write_text(
        json.dumps(answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def record_goldenset_answer(
    stem: str,
    question_id: str,
    *,
    schema_version: int,
    hops: int,
    content: str,
    node_types: list,
    edge_types: list,
    related_nodes: list,
    related_edges: list,
) -> dict:
    """Appends one generated-answer record for `question_id` and returns it.
    Never mutates or removes any prior record -- see the module-level note
    above for why history is kept even for schema versions no longer
    active."""
    answers = _load_answers(stem)
    record = {
        "schema_version": schema_version,
        "hops": hops,
        "generated_at": datetime.now().isoformat(),
        "content": content,
        "node_types": node_types,
        "edge_types": edge_types,
        "related_nodes": related_nodes,
        "related_edges": related_edges,
    }
    answers.setdefault(question_id, []).append(record)
    _save_answers(stem, answers)
    return record


def latest_goldenset_answers(stem: str, active_schema_version: int | None) -> dict:
    """For every question id with at least one saved answer, the most
    recently recorded answer whose schema_version equals
    `active_schema_version` -- empty for a question with no answer recorded
    against that exact version, and `{}` entirely when the document has no
    active schema version at all."""
    if active_schema_version is None:
        return {}
    answers = _load_answers(stem)
    latest = {}
    for question_id, records in answers.items():
        matching = [r for r in records if r["schema_version"] == active_schema_version]
        if matching:
            latest[question_id] = matching[-1]
    return latest
