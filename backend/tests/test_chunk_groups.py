"""Tests for app.ontology.chunk_groups.run_chunk_groups, crossing its one
interface with a fake `group_fn` -- no LLM patching needed, since the runner
never talks to an LLM itself (each stage's own `group_fn` does)."""

import json
import shutil
import threading
import time

import pytest

from app.ontology.chunk_groups import map_concurrently, run_chunk_groups
from app.preprocess.parser import DATA_DIR
from app.utils.paths import document_dir_for


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def _chunks(*texts):
    return [{"path": f"P{i}", "text": text} for i, text in enumerate(texts, start=1)]


def test_reduces_group_results_in_group_order():
    result = run_chunk_groups(
        _chunks("a" * 10, "b" * 10),
        stage="schema",
        max_group_chars=10,
        group_fn=lambda text, index: {"index": index, "text": text},
        reduce_fn=lambda results: results,
    )

    assert [r["index"] for r in result] == [1, 2]
    assert result[0]["text"] == "[P1]\n" + "a" * 10
    assert result[1]["text"] == "[P2]\n" + "b" * 10


def test_single_group_skips_reduce_and_returns_its_result_untouched():
    def reduce_fn(results):
        raise AssertionError("reduce_fn must not run for a single group")

    result = run_chunk_groups(
        _chunks("short"),
        stage="schema",
        group_fn=lambda text, index: {"only": True},
        reduce_fn=reduce_fn,
    )

    assert result == {"only": True}


def test_no_chunks_raises_value_error():
    with pytest.raises(ValueError, match="no chunks"):
        run_chunk_groups(
            [], stage="schema", group_fn=lambda t, i: {}, reduce_fn=lambda rs: rs
        )


def test_groups_run_concurrently_and_results_stay_in_group_order():
    lock = threading.Lock()
    active = 0
    peak = 0

    def group_fn(text, index):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05 * (4 - index))  # later groups finish first
        with lock:
            active -= 1
        return index

    result = run_chunk_groups(
        _chunks("a" * 10, "b" * 10, "c" * 10),
        stage="extract",
        max_group_chars=10,
        group_fn=group_fn,
        reduce_fn=lambda results: results,
    )

    assert result == [1, 2, 3]
    assert peak > 1


def test_a_failed_group_lets_in_flight_groups_finish_before_it_raises():
    finished = []

    def group_fn(text, index):
        if index == 1:
            raise RuntimeError("group 1 failed")
        time.sleep(0.1)
        finished.append(index)
        return index

    with pytest.raises(RuntimeError, match="group 1 failed"):
        run_chunk_groups(
            _chunks("a" * 10, "b" * 10, "c" * 10),
            stage="extract",
            max_group_chars=10,
            group_fn=group_fn,
            reduce_fn=lambda results: results,
        )

    assert sorted(finished) == [2, 3]


# --- resume cache -----------------------------------------------------------

STEM = "doc_raw"


class _Recorder:
    """A fake group_fn that records which group indexes it was called for and
    can be told to fail on specific ones."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def __call__(self, text, index):
        self.calls.append(index)
        if index in self.fail_on:
            raise RuntimeError(f"group {index} failed")
        return {"index": index, "text": text}


def _run(group_fn, chunks=None, fingerprint_inputs=None, stem=STEM):
    return run_chunk_groups(
        chunks or _chunks("a" * 10, "b" * 10, "c" * 10),
        stage="schema",
        stem=stem,
        max_group_chars=10,
        fingerprint_inputs=fingerprint_inputs or {"schema_version": 1},
        group_fn=group_fn,
        reduce_fn=lambda results: results,
    )


def test_retry_after_a_failed_group_reruns_only_that_group():
    first = _Recorder(fail_on={2})
    with pytest.raises(RuntimeError):
        _run(first)
    assert sorted(first.calls) == [1, 2, 3]

    second = _Recorder()
    result = _run(second)

    assert second.calls == [2]
    assert [r["index"] for r in result] == [1, 2, 3]


@pytest.mark.parametrize(
    "first_inputs, second_inputs",
    [
        ({"schema_version": 1}, {"schema_version": 2}),
        ({"document_type": "general"}, {"document_type": "legal"}),
    ],
)
def test_changed_fingerprint_inputs_invalidate_a_completed_runs_cache(first_inputs, second_inputs):
    _run(_Recorder(), fingerprint_inputs=first_inputs)

    second = _Recorder()
    _run(second, fingerprint_inputs=second_inputs)

    assert sorted(second.calls) == [1, 2, 3]


def test_unchanged_inputs_reuse_a_completed_runs_cache():
    _run(_Recorder())

    second = _Recorder()
    _run(second)

    assert second.calls == []


def test_changed_chunk_text_invalidates_only_the_affected_group():
    _run(_Recorder())

    second = _Recorder()
    _run(second, chunks=_chunks("a" * 10, "X" * 10, "c" * 10))

    assert second.calls == [2]


def test_without_stem_nothing_is_cached_or_written():
    _run(_Recorder(), stem=None)

    second = _Recorder()
    _run(second, stem=None)

    assert sorted(second.calls) == [1, 2, 3]
    assert not DATA_DIR.exists() or not any(DATA_DIR.rglob("*.json"))


def test_a_corrupt_cache_file_is_treated_as_a_miss():
    _run(_Recorder())
    for path in document_dir_for(STEM).rglob("*.json"):
        path.write_text("{not json")

    second = _Recorder()
    _run(second)

    assert sorted(second.calls) == [1, 2, 3]


# --- progress -----------------------------------------------------------------

from app.ontology.chunk_groups import load_progress  # noqa: E402


def test_a_finished_run_records_done_with_every_group_completed():
    _run(_Recorder())

    progress = load_progress(STEM, "schema")
    assert progress["status"] == "done"
    assert progress["stage"] == "done"
    assert progress["total"] == 3
    assert progress["completed"] == 3


def test_cached_groups_count_as_completed():
    with pytest.raises(RuntimeError):
        _run(_Recorder(fail_on={2}))
    _run(_Recorder())

    progress = load_progress(STEM, "schema")
    assert progress["completed"] == 3


def test_a_failed_run_records_error_and_the_message():
    with pytest.raises(RuntimeError):
        _run(_Recorder(fail_on={2}))

    progress = load_progress(STEM, "schema")
    assert progress["status"] == "error"
    assert progress["error"] == "group 2 failed"
    assert progress["completed"] == 2


@pytest.mark.parametrize("reduce_stage, expected", [(None, "reduce"), ("merge", "merge")])
def test_stage_is_labelled_while_reduce_runs(reduce_stage, expected):
    seen = {}

    def reduce_fn(results):
        seen["stage"] = load_progress(STEM, "schema")["stage"]
        return results

    kwargs = {"reduce_stage": reduce_stage} if reduce_stage else {}
    run_chunk_groups(
        _chunks("a" * 10, "b" * 10),
        stage="schema", stem=STEM, max_group_chars=10,
        group_fn=_Recorder(), reduce_fn=reduce_fn, **kwargs,
    )

    assert seen["stage"] == expected


def test_summarize_counts_are_summed_across_groups_including_cached_ones():
    def group_fn(text, index):
        return {"nodes": ["n"] * index, "edges": ["e"]}

    def summarize(result):
        return {"nodes": len(result["nodes"]), "edges": len(result["edges"])}

    def run():
        return run_chunk_groups(
            _chunks("a" * 10, "b" * 10, "c" * 10),
            stage="extract", stem=STEM, max_group_chars=10,
            group_fn=group_fn, reduce_fn=lambda results: results, summarize=summarize,
        )

    run()
    first = load_progress(STEM, "extract")
    run()  # every group now comes from the resume cache
    second = load_progress(STEM, "extract")

    assert (first["nodes"], first["edges"]) == (6, 3)
    assert (second["nodes"], second["edges"]) == (6, 3)


def test_starting_a_new_run_resets_the_previous_runs_done_record():
    _run(_Recorder())
    assert load_progress(STEM, "schema")["status"] == "done"

    seen = {}

    def group_fn(text, index):
        seen.setdefault("progress", load_progress(STEM, "schema"))
        return index

    _run(group_fn, fingerprint_inputs={"schema_version": 2})

    assert seen["progress"]["status"] == "running"
    assert seen["progress"]["completed"] == 0


# --- map_concurrently -----------------------------------------------------------


def test_map_concurrently_preserves_order_and_overlaps_calls():
    def slow_double(x):
        time.sleep(0.2)
        return x * 2

    start = time.monotonic()
    result = map_concurrently(slow_double, [1, 2, 3, 4, 5])
    elapsed = time.monotonic() - start

    assert result == [2, 4, 6, 8, 10]  # order preserved despite concurrent execution
    assert elapsed < 0.2 * 5  # overlapped, not run one after another


def test_map_concurrently_single_item_skips_thread_pool():
    calls = []

    def record_and_return(x):
        calls.append(threading.current_thread())
        return x

    result = map_concurrently(record_and_return, ["only"])

    assert result == ["only"]
    assert calls == [threading.current_thread()]  # ran inline, no worker thread


def test_logs_which_group_is_being_processed(caplog):
    with caplog.at_level("INFO", logger="app.ontology.chunk_groups"):
        _run(_Recorder())

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "schema: processing group 1/3" in messages
    assert "schema: processing group 3/3" in messages


def test_cached_groups_are_not_logged_as_processing(caplog):
    _run(_Recorder())
    caplog.clear()

    with caplog.at_level("INFO", logger="app.ontology.chunk_groups"):
        _run(_Recorder())

    assert "processing group" not in " ".join(r.getMessage() for r in caplog.records)


# --- review findings ----------------------------------------------------------


def test_a_cache_file_without_a_result_is_treated_as_a_miss():
    _run(_Recorder())
    for path in document_dir_for(STEM).rglob("*.json"):
        stored = json.loads(path.read_text())
        if "fingerprint" in stored:
            path.write_text(json.dumps({"fingerprint": stored["fingerprint"]}))

    second = _Recorder()
    result = _run(second)

    assert sorted(second.calls) == [1, 2, 3]
    assert all(r is not None for r in result)


def test_concurrent_runs_of_the_same_stage_do_not_collide_writing_the_cache():
    # Two requests for the same document and stage (a double-clicked Run
    # button, two tabs) write the same cache path at the same time.
    errors = []

    def worker(worker_id):
        try:
            for attempt in range(40):
                run_chunk_groups(
                    _chunks("only"),
                    stage="schema",
                    stem=STEM,
                    fingerprint_inputs={"worker": worker_id, "attempt": attempt},
                    group_fn=lambda text, index: {"ok": True},
                    reduce_fn=lambda results: results,
                )
        except Exception as exc:  # noqa: BLE001 -- collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
