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

from app.llm.chat import get_model_max_tokens, get_model_name
from app.ontology.utils import _check_document_length, _load_chunk_items, _require_document_text
from app.utils.paths import progress_path_for, resume_cache_path_for
from app.utils.store import read_json, write_json

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
# being processed independently), each stage's entry point (discover_for_document,
# schema_for_document, extract_for_document) runs its single-document function once per token-budget-sized group of
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
    """File-backed progress tracker for one document/operation pair, opened by
    run_chunk_groups. Use it as a context manager so status flips to
    "done"/"error" automatically when the `with` block exits, whether or not
    the wrapped code raises."""

    def __init__(self, stem: str, operation: str, total: int):
        self._lock = threading.Lock()
        self._path = progress_path_for(stem, operation)
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
        write_json(self._path, self._state)

    def advance(self, *_args, **fields) -> None:
        """Marks one more unit of work (one chunk group, or the single
        whole-document call) done, optionally merging `fields` into the
        persisted state (e.g. extract_for_document's running node/edge
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


def load_progress(stem: str, operation: str) -> dict | None:
    """Read side for main.py's GET /progress route -- returns None (not a
    404-raising lookup) when nothing has ever run this operation for this
    document, or a prior run's file was never written, so the route can
    decide what an absent record means (e.g. "hasn't started yet")."""
    return read_json(progress_path_for(stem, operation))

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
def _operation_identity(operation: str) -> dict:
    """The part of a group result that depends on how the operation runs
    rather than on what the stage fed it: the model currently selected for it
    and its effective output-token ceiling. A different model (or ceiling)
    can produce a different, or truncated, result for identical input."""
    return {
        "operation": operation,
        "model": get_model_name(operation),
        "max_tokens": get_model_max_tokens(operation),
    }


def _fingerprint(
    stage: str, fingerprint_inputs: dict, group_text: str, operation_identity: dict
) -> str:
    payload = json.dumps(
        {
            "stage": stage,
            "inputs": fingerprint_inputs,
            "operation": operation_identity,
            "text": group_text,
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cached_result(stem: str, stage: str, index: int, fingerprint: str):
    """Returns (hit, result). A missing, unreadable or fingerprint-mismatched
    file is a miss -- never an error, since a cache is only ever an
    optimization."""
    try:
        stored = read_json(resume_cache_path_for(stem, stage, index))
    except (OSError, ValueError):
        return False, None
    if (
        not isinstance(stored, dict)
        or stored.get("fingerprint") != fingerprint
        or "result" not in stored
    ):
        return False, None
    return True, stored["result"]


def _store_result(stem: str, stage: str, index: int, fingerprint: str, result) -> None:
    # write_json is atomic and safe for two runs of the same stage writing this
    # path at once (a double-clicked Run button): see app.utils.store.
    write_json(
        resume_cache_path_for(stem, stage, index),
        {"fingerprint": fingerprint, "result": result},
    )


def _load_chunk_items_for_run(stem: str, max_chars: int | None) -> list[dict]:
    """The document's chunks if it has a chunks.json, otherwise its whole
    raw.md text as a single chunk -- an unchunked document is one chunk group
    holding the whole text (CONTEXT.md, "Chunk group"). `max_chars` caps that
    whole text only -- it is a whole-document limit, never a grouping budget,
    so a chunked document ignores it (MAX_CHUNK_GROUP_CHARS alone decides how
    it is split)."""
    chunk_items = _load_chunk_items(stem)
    if chunk_items is None:
        document_text = _require_document_text(stem)
        _check_document_length(document_text, max_chars)
        chunk_items = [{"text": document_text}]
    return chunk_items


def run_chunk_groups(
    *,
    stem: str,
    stage: str,
    operation: str,
    group_fn,
    reduce_fn,
    fingerprint_inputs: dict | None = None,
    max_group_chars: int | None = None,
    max_chars: int | None = None,
    reduce_stage: str = "reduce",
    summarize=None,
):
    """Runs `group_fn(group_text)` once per chunk group of the document `stem`
    names -- its chunks.json, or with none its whole raw.md as a single group
    (concurrently, resuming from the resume cache where a group's fingerprint still
    matches), then folds the group results, in group order, with
    `reduce_fn(results)`. A document that fits in one chunk group skips
    `reduce_fn` entirely and returns that group's result untouched, so
    `reduce_fn` can assume two or more results.

    Progress is written to documents/{stem}/progress/{stage}.json (reset at
    the start of every run) and group results to the resume cache.
    `fingerprint_inputs` is everything besides a group's own text that its result depends on -- a
    cached result is reused only if all of it is unchanged. `operation` is
    the name of the operation `group_fn` runs; its currently selected model
    and output-token ceiling are added to every fingerprint automatically.
    `max_chars` is the whole-document size cap for a document with no chunks
    (see _load_chunk_items_for_run); it has no effect on a chunked one.

    `summarize(result)` may return a dict of numeric counts; they are summed
    across every completed group (cached ones included) and merged into the
    progress file as running totals. `reduce_stage` is the stage label shown
    while `reduce_fn` runs."""
    chunk_items = _load_chunk_items_for_run(stem, max_chars)
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)

    def run_group(item):
        index, group = item
        group_text = _group_document_text(group)
        fingerprint = _fingerprint(
            stage, fingerprint_inputs or {}, group_text, _operation_identity(operation)
        )
        hit, cached = _load_cached_result(stem, stage, index, fingerprint)
        if hit:
            return cached
        logger.info(
            "%s: processing group %d/%d (%d chunks, %d chars)",
            stage, index, len(groups), len(group), len(group_text),
        )
        result = group_fn(group_text)
        _store_result(stem, stage, index, fingerprint, result)
        return result

    totals: dict[str, int] = {}
    totals_lock = threading.Lock()

    with ChunkProgress(stem, stage, len(groups)) as progress:

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
