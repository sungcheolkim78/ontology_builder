"""The chunk-group runner: one module behind which discover, schema
generation and graph extraction all run their per-chunk-group map step and
their reduce step. See CONTEXT.md for the vocabulary (chunk group, group
result, resume cache, reduce)."""

import contextvars
import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.utils.paths import document_dir_for

logger = logging.getLogger(__name__)

# Chunk-grouped ontology discovery/schema generation/graph extraction -------
#
# discover_ontology()/generate_schema() (generate_schema.py) and extract_graph()
# (extract_graph.py) each send the whole document in one LLM call and are
# bounded by MAX_DOCUMENT_CHARS -- documents chunked into article-level JSON
# chunks (app.preprocess.chunking.chunk_markdown_file) routinely exceed that in
# total even though no single chunk does. Rather than keeping every group's
# view of the ontology consistent with every other group's as it goes (which
# would make each group depend on every earlier one and prevent groups from
# being processed independently), each module's own *_from_chunks variant runs
# its single-document function once per token-budget-sized group of
# consecutive chunks (map, via group_chunks_by_budget below), then folds every
# group's result into one unified set (reduce) -- see each function's own
# docstring for what exactly gets consolidated/merged and how.
MAX_CHUNK_GROUP_CHARS = int(os.environ.get("MAX_CHUNK_GROUP_CHARS", 12_000))


def group_chunks_by_budget(
    chunk_items: list[dict], max_group_chars: int | None = None
) -> list[list[dict]]:
    """Packs `chunk_items` (each needs a "text" key; order is preserved) into
    consecutive-run groups whose total text length stays under
    `max_group_chars` where possible. A single chunk longer than the budget
    on its own still becomes its own group rather than being split mid-chunk
    -- article-level chunks are the smallest unit this module reasons
    about."""
    limit = max_group_chars if max_group_chars is not None else MAX_CHUNK_GROUP_CHARS
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0
    for item in chunk_items:
        text_len = len(item.get("text") or "")
        if current and current_len + text_len > limit:
            groups.append(current)
            current, current_len = [], 0
        current.append(item)
        current_len += text_len
    if current:
        groups.append(current)
    return groups


def _group_document_text(chunk_items: list[dict]) -> str:
    parts = []
    for item in chunk_items:
        path = item.get("path")
        text = item.get("text") or ""
        parts.append(f"[{path}]\n{text}" if path else text)
    return "\n\n".join(parts)


# In-flight progress reporting -----------------------------------------------
#
# discover_for_document/schema_for_document/extract_for_document (and their
# chunk-grouped inner functions) can each take anywhere from seconds to
# 1000s of seconds for a large document, all inside one synchronous HTTP
# request -- the frontend previously had no way to show anything better than
# a locally-ticking "N초" counter while waiting. ChunkProgress below persists
# a small JSON snapshot to documents/{stem}/progress/{operation}.json as the
# operation goes, so a GET route (main.py's /progress) can be polled from the
# browser while the POST request is still in flight. It's a summary
# (counts/stage), not the per-group data -- that lives in the resume cache.
#
# run_chunk_groups runs its map step concurrently (see map_concurrently
# below), so groups finish in whatever order
# their LLM calls happen to return in -- advance() is only ever called with
# "one more group finished", never "group N finished", and is lock-protected
# so concurrent callers don't corrupt `completed` or race on the file write.
_PROGRESS_STATUSES = ("running", "done", "error")


class ChunkProgress:
    """Real, file-backed progress tracker for one document/operation pair.
    Never constructed directly by pipeline code -- use start_progress()
    below, which returns a _NoopProgress instead when `stem` is None (the
    same has-a-real-backend-or-silently-does-nothing shape as
    app.llm.telemetry's _NoopObservation, so callers never need an `if
    progress:` guard)."""

    def __init__(self, stem: str, operation: str, total: int):
        self._lock = threading.Lock()
        self._path = document_dir_for(stem) / "progress" / f"{operation}.json"
        self._state = {
            "operation": operation,
            "status": "running",
            "stage": "map",
            "total": total,
            "completed": 0,
            "error": None,
        }
        self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, ensure_ascii=False))

    def advance(self, *_args, **fields) -> None:
        """Marks one more unit of work (one chunk group, or the single
        whole-document call) done, optionally merging `fields` into the
        persisted state (e.g. extract_graph_from_chunks's running node/edge
        counts). Accepts and ignores a positional argument too, so it can be
        passed straight as map_concurrently's `on_item_done` callback,
        which calls it with that item's own result."""
        with self._lock:
            self._state["completed"] += 1
            self._state.update(fields)
            self._write()

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._state["stage"] = stage
            self._write()

    def __enter__(self) -> "ChunkProgress":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        with self._lock:
            self._state["status"] = "error" if exc_type else "done"
            self._state["stage"] = "done" if exc_type is None else self._state["stage"]
            if exc is not None:
                self._state["error"] = str(exc)
            self._write()
        return False  # never suppress the exception


class _NoopProgress:
    """Stand-in for ChunkProgress when no `stem` was given (e.g.
    measure_schema_stability, or any other caller that isn't answering one
    specific document's route) -- same shape, no filesystem I/O."""

    def advance(self, *_args, **_kwargs) -> None:
        pass

    def set_stage(self, stage: str) -> None:
        pass

    def __enter__(self) -> "_NoopProgress":
        return self

    def __exit__(self, *_exc_info) -> bool:
        return False


def start_progress(stem: str | None, operation: str, total: int):
    """The one entry point pipeline code should use to get a progress
    tracker -- a real ChunkProgress when `stem` is given, a _NoopProgress
    otherwise. Use as a context manager (`with start_progress(...) as
    progress:`) so status flips to "done"/"error" automatically when the
    `with` block exits, whether or not the wrapped code raises."""
    return ChunkProgress(stem, operation, total) if stem else _NoopProgress()


def load_progress(stem: str, operation: str) -> dict | None:
    """Read side for main.py's GET /progress route -- returns None (not a
    404-raising lookup) when nothing has ever run this operation for this
    document, or a prior run's file was never written, so the route can
    decide what an absent record means (e.g. "hasn't started yet")."""
    path = document_dir_for(stem) / "progress" / f"{operation}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())

# OpenRouter (like most LLM providers) rate-limits by concurrent in-flight
# requests, so map_concurrently() caps how many run at once rather than
# firing one thread per item unconditionally.
MAX_CONCURRENT_LLM_CALLS = int(os.environ.get("MAX_CONCURRENT_LLM_CALLS", 5))


def map_concurrently(fn, items: list, on_item_done=None) -> list:
    """Runs fn(item) for every item in a small thread pool instead of one
    after another, returning results in the same order as `items`. LLM calls
    are I/O-bound (a blocking network round-trip), so overlapping them turns
    N sequential round-trips into roughly ceil(N / MAX_CONCURRENT_LLM_CALLS)
    round-trips' worth of wall-clock time. A single item skips the thread
    pool entirely -- the common case (a document that fits in one chunk
    group) pays no threading overhead at all.

    If any call raises, every other already-submitted call still runs to
    completion first (the executor's `with` block waits for them) and only
    then does the first failure re-raise -- this is what lets a retried run
    resume from the results the surviving calls had time to cache.

    contextvars.copy_context() is taken once per item, right before
    submitting it, and used to run that item's call -- ThreadPoolExecutor
    does not propagate the calling thread's context on its own, and without
    this every concurrent invoke_with_telemetry call would show up as its
    own orphaned trace instead of nesting under the request's trace() span
    (see app.llm.telemetry.trace's own docstring). Each item gets its own
    fresh copy rather than one shared Context, since a Context object
    cannot be entered by more than one thread at a time.

    `on_item_done`, if given, is called with each item's own result right
    after that item's fn() call returns -- from whichever worker thread ran
    it, in whatever order calls happen to finish. It's the callback's own
    job to be safe to call concurrently."""

    def call(item):
        result = fn(item)
        if on_item_done is not None:
            on_item_done(result)
        return result

    if len(items) == 1:
        return [call(items[0])]
    with ThreadPoolExecutor(max_workers=min(len(items), MAX_CONCURRENT_LLM_CALLS)) as executor:
        futures = [executor.submit(contextvars.copy_context().run, call, item) for item in items]
        return [future.result() for future in futures]


# Resume cache ----------------------------------------------------------------
#
# One JSON file per (stage, group index) at documents/{stem}/resume_cache/,
# holding {"fingerprint", "result"}. A stored result is only reused when its
# fingerprint matches -- a hash of the stage, the caller's own
# `fingerprint_inputs` (schema version, document_type, discovery hint,
# prompt text...) and the group's actual text -- so a run with different
# inputs can never silently pick up a previous run's output for a group
# index that merely still exists.
def _fingerprint(stage: str, fingerprint_inputs: dict, group_text: str) -> str:
    payload = json.dumps(
        {"stage": stage, "inputs": fingerprint_inputs, "text": group_text},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(stem: str, stage: str, index: int) -> Path:
    return document_dir_for(stem) / "resume_cache" / f"{stage}_{index}.json"


def _load_cached_result(stem: str | None, stage: str, index: int, fingerprint: str):
    """Returns (hit, result). A missing, unreadable or fingerprint-mismatched
    file is a miss -- never an error, since a cache is only ever an
    optimization."""
    if stem is None:
        return False, None
    path = _cache_path(stem, stage, index)
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return False, None
    if (
        not isinstance(stored, dict)
        or stored.get("fingerprint") != fingerprint
        or "result" not in stored
    ):
        return False, None
    return True, stored["result"]


def _store_result(stem: str | None, stage: str, index: int, fingerprint: str, result) -> None:
    if stem is None:
        return
    path = _cache_path(stem, stage, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written to a temp name then renamed, so a process killed mid-write
    # leaves no half-written file for the next run to trip over. The temp
    # name is unique per process and thread, since two runs of the same stage
    # for one document (e.g. a double-clicked Run button) write this same
    # path at the same time and would otherwise rename each other's file away.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint, "result": result}, ensure_ascii=False))
    os.replace(tmp, path)


def run_chunk_groups(
    chunk_items: list[dict],
    *,
    stage: str,
    group_fn,
    reduce_fn,
    stem: str | None = None,
    fingerprint_inputs: dict | None = None,
    max_group_chars: int | None = None,
    reduce_stage: str = "reduce",
    summarize=None,
):
    """Runs `group_fn(group_text, index)` once per chunk group (concurrently,
    resuming from the resume cache where a group's fingerprint still
    matches), then folds the group results, in group order, with
    `reduce_fn(results)`. A document that fits in one chunk group skips
    `reduce_fn` entirely and returns that group's result untouched, so
    `reduce_fn` can assume two or more results.

    `stem`, if given, enables the resume cache and the progress file
    (documents/{stem}/progress/{stage}.json, reset at the start of every run);
    with `stem=None` the run is entirely in-memory. `fingerprint_inputs` is
    everything besides a group's own text that its result depends on -- a
    cached result is reused only if all of it is unchanged.

    `summarize(result)` may return a dict of numeric counts; they are summed
    across every completed group (cached ones included) and merged into the
    progress file as running totals. `reduce_stage` is the stage label shown
    while `reduce_fn` runs."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError(f"no chunks to run {stage} on")

    def run_group(item):
        index, group = item
        group_text = _group_document_text(group)
        fingerprint = _fingerprint(stage, fingerprint_inputs or {}, group_text)
        hit, cached = _load_cached_result(stem, stage, index, fingerprint)
        if hit:
            return cached
        logger.info(
            "%s: processing group %d/%d (%d chunks, %d chars)",
            stage, index, len(groups), len(group), len(group_text),
        )
        result = group_fn(group_text, index)
        _store_result(stem, stage, index, fingerprint, result)
        return result

    totals: dict[str, int] = {}
    totals_lock = threading.Lock()

    with start_progress(stem, stage, len(groups)) as progress:

        def on_group_done(result):
            with totals_lock:
                for key, value in (summarize(result) if summarize else {}).items():
                    totals[key] = totals.get(key, 0) + value
                progress.advance(**totals)

        results = map_concurrently(
            run_group, list(enumerate(groups, start=1)), on_item_done=on_group_done
        )
        if len(results) == 1:
            return results[0]

        progress.set_stage(reduce_stage)
        return reduce_fn(results)
