import importlib.util
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
