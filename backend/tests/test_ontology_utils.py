import json
import shutil
import threading

import pytest

from app.ontology.utils import ChunkProgress, load_progress, parse_json_response, start_progress
from app.preprocess.parser import DATA_DIR
from app.utils.paths import document_dir_for


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def _progress_path(stem, operation):
    return document_dir_for(stem) / "progress" / f"{operation}.json"


def _read_progress(stem, operation):
    return json.loads(_progress_path(stem, operation).read_text())


def test_chunk_progress_writes_initial_state_on_construction():
    ChunkProgress("doc_raw", "schema", total=4)

    state = _read_progress("doc_raw", "schema")

    assert state == {
        "operation": "schema",
        "status": "running",
        "stage": "map",
        "total": 4,
        "completed": 0,
        "error": None,
    }


def test_chunk_progress_advance_increments_completed_and_merges_fields():
    progress = ChunkProgress("doc_raw", "extract", total=2)

    progress.advance(nodes=3, edges=1)
    progress.advance(nodes=7, edges=2)

    state = _read_progress("doc_raw", "extract")
    assert state["completed"] == 2
    assert state["nodes"] == 7
    assert state["edges"] == 2


def test_chunk_progress_advance_ignores_a_positional_argument():
    # _map_concurrently's on_item_done calls back with the item's own
    # result as a positional arg -- advance() must tolerate that.
    progress = ChunkProgress("doc_raw", "discover", total=1)

    progress.advance({"classes": []})

    assert _read_progress("doc_raw", "discover")["completed"] == 1


def test_chunk_progress_set_stage_updates_stage():
    progress = ChunkProgress("doc_raw", "schema", total=2)

    progress.set_stage("reduce")

    assert _read_progress("doc_raw", "schema")["stage"] == "reduce"


def test_chunk_progress_context_manager_marks_done_on_success():
    with ChunkProgress("doc_raw", "schema", total=1) as progress:
        progress.advance()

    state = _read_progress("doc_raw", "schema")
    assert state["status"] == "done"
    assert state["stage"] == "done"
    assert state["error"] is None


def test_chunk_progress_context_manager_marks_error_without_suppressing():
    with pytest.raises(ValueError):
        with ChunkProgress("doc_raw", "schema", total=1):
            raise ValueError("boom")

    state = _read_progress("doc_raw", "schema")
    assert state["status"] == "error"
    assert state["error"] == "boom"


def test_chunk_progress_advance_is_thread_safe():
    progress = ChunkProgress("doc_raw", "schema", total=50)
    barrier = threading.Barrier(10)

    def bump():
        barrier.wait()
        progress.advance()

    threads = [threading.Thread(target=bump) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert _read_progress("doc_raw", "schema")["completed"] == 10


def test_start_progress_returns_noop_when_stem_is_none():
    progress = start_progress(None, "schema", total=3)

    # Should be safe to call every method with no filesystem effect at all.
    with progress:
        progress.advance(nodes=1)
        progress.set_stage("reduce")

    assert not (DATA_DIR / "documents").exists()


def test_start_progress_returns_real_tracker_when_stem_given():
    progress = start_progress("doc_raw", "extract", total=3)

    assert isinstance(progress, ChunkProgress)
    assert _progress_path("doc_raw", "extract").is_file()


def test_load_progress_returns_none_when_nothing_recorded():
    assert load_progress("doc_raw", "schema") is None


def test_load_progress_returns_the_persisted_state():
    ChunkProgress("doc_raw", "schema", total=5).advance(nodes=2)

    result = load_progress("doc_raw", "schema")

    assert result["total"] == 5
    assert result["completed"] == 1


# --- parse_json_response -----------------------------------------------


def test_parse_json_response_parses_a_plain_string():
    assert parse_json_response('{"node_types": []}') == {"node_types": []}


def test_parse_json_response_strips_a_markdown_code_fence():
    assert parse_json_response('```json\n{"ok": true}\n```') == {"ok": True}


def test_parse_json_response_raises_on_invalid_json():
    with pytest.raises(ValueError):
        parse_json_response("not json at all")


def test_parse_json_response_extracts_text_block_from_a_content_list():
    # get_chat_model's reasoning=low kwarg (app/llm/chat.py) routes the
    # request through OpenAI's Responses API, which returns `response.content`
    # as a list of blocks instead of a plain string once that's set.
    content = [
        {"type": "reasoning", "content": [{"text": "thinking...", "type": "reasoning_text"}]},
        {"type": "text", "text": '{"node_types": []}'},
    ]

    assert parse_json_response(content) == {"node_types": []}


def test_parse_json_response_finds_text_block_regardless_of_order():
    # Verified live that block order is provider-dependent -- Qwen returns
    # [reasoning, text], Gemini returns [text, reasoning] for the same
    # request shape.
    content = [
        {"type": "text", "text": '{"ok": true}'},
        {"type": "reasoning", "content": []},
    ]

    assert parse_json_response(content) == {"ok": True}


def test_parse_json_response_raises_when_no_text_block_present():
    with pytest.raises(ValueError):
        parse_json_response([{"type": "reasoning", "content": []}])


def test_parse_json_response_ignores_trailing_garbage_after_valid_json():
    # Regression: observed live -- a response_format=json_object reply is
    # never itself markdown-fenced, but a model can still tack on a leftover
    # fence-closer habit afterward (here, just "``", not even a full "```"),
    # which used to fail the whole parse with "Extra data" even though a
    # complete, valid JSON value came first.
    content = '{"node_types": [], "edge_types": []}\n``'

    assert parse_json_response(content) == {"node_types": [], "edge_types": []}


def test_parse_json_response_ignores_trailing_garbage_in_a_content_list():
    content = [
        {"type": "reasoning", "content": []},
        {"type": "text", "text": '{"ok": true}\n```'},
    ]

    assert parse_json_response(content) == {"ok": True}
