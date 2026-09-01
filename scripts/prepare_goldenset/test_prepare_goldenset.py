import importlib.util
import logging
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("prepare_goldenset.py")
SPEC = importlib.util.spec_from_file_location("prepare_goldenset", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_locate_quote_recalculates_multiline_span():
    lines = ["first", "second line", "third line"]
    assert module.locate_quote(lines, "second line\nthird") == (2, 3)


def test_merge_rejects_unverified_evidence():
    questions = [
        {
            "id": "q001",
            "question": "Who?",
            "question_type": "entity",
            "importance": "high",
            "rationale": "core fact",
        }
    ]
    answers = {
        "answers": [
            {
                "id": "q001",
                "answerable": True,
                "answer": "Alice",
                "answer_facts": [],
                "evidence": [{"quote": "Bob", "line_start": 99, "line_end": 99}],
                "notes": "",
            }
        ]
    }
    records, warnings = module.merge_and_validate_answers(
        questions, answers, "Alice works here."
    )
    assert records[0]["answerable"] is False
    assert records[0]["evidence"] == []
    assert len(warnings) == 2


def test_validate_questions_assigns_stable_ids():
    payload = {
        "questions": [
            {
                "id": "anything",
                "question": "What is A?",
                "question_type": "attribute",
                "importance": "high",
                "rationale": "important",
            }
        ]
    }
    result = module.validate_questions(payload, 1)
    assert result[0]["id"] == "q001"


def test_compact_document_for_questions_keeps_sections_within_budget():
    document = "# First\n" + ("first fact " * 20) + "\n\n## Second\n" + ("second fact " * 20)

    compacted = module.compact_document_for_questions(document, max_chars=120)

    assert len(compacted) <= 120
    assert "# First" in compacted
    assert "## Second" in compacted
    assert "[section content truncated]" in compacted


def test_configure_logging_writes_all_messages_to_one_file(tmp_path):
    log_path = tmp_path / "run.log"
    logger = module.configure_logging(log_path)

    logger.info("document started")
    logger.warning("document warning")
    for handler in logger.handlers:
        handler.flush()

    contents = log_path.read_text(encoding="utf-8")
    assert "document started" in contents
    assert "document warning" in contents
    assert sum(isinstance(handler, logging.FileHandler) for handler in logger.handlers) == 1


def test_limit_markdown_files_keeps_sorted_prefix():
    files = [Path("a.md"), Path("b.md"), Path("c.md")]

    assert module.limit_markdown_files(files, 2) == files[:2]
    assert module.limit_markdown_files(files, None) == files
