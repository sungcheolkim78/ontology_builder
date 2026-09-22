"""Shared, dependency-light helpers used by two or more of generate_schema.py/
extract_graph.py/evolve_graph.py -- anything used by only one of those three
lives in that file instead, per app.ontology's own "leaf every other
submodule can import from" convention (see persistence.py, schema_validation.py)."""

import json
import os
import re
import threading

from app.utils.paths import document_dir_for

# ~4 chars/token is a conservative rule of thumb. 200_000 was originally sized
# for the default model (gpt-4o-mini, 128k-token context); real OPENROUTER_MODEL
# choices in practice (e.g. Gemini models) commonly have ~1M-token context, and
# real legal/insurance documents routinely exceed 200k chars, so the limit is
# raised 1.5x rather than tuned per-model. Configurable since OPENROUTER_MODEL
# can point at a model with a different context window. Guards against silently
# blowing the context window or getting back truncated/malformed JSON (e.g. an
# edge referencing a node that got cut off mid-response) instead of a clear,
# immediate error.
MAX_DOCUMENT_CHARS = int(os.environ.get("MAX_DOCUMENT_CHARS", 1_000_000))

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


def _check_document_length(document_text: str, max_chars: int | None = None) -> None:
    limit = max_chars if max_chars is not None else MAX_DOCUMENT_CHARS
    if len(document_text) > limit:
        raise ValueError(
            f"document is too long ({len(document_text)} chars, "
            f"max {limit}) to send to the LLM in one call"
        )


def _extract_text_block(content: list) -> str:
    """Pulls the 'text' block's own text out of a Responses-API-shaped
    content list -- see parse_json_response's own comment for why this
    shape shows up at all. Searches by `type` rather than assuming a
    position, since which block comes first is provider-dependent (verified
    empirically to differ: e.g. Qwen returns [reasoning, text], Gemini
    returns [text, reasoning] for the exact same request shape)."""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    raise ValueError(f"no text block found in response content: {content!r}")


def parse_json_response(content: str | list) -> dict:
    """Parses a chat model's JSON response. `content` is usually already a
    plain string (langchain's normal `response.content` shape), but once
    `reasoning` is added to a call's model_kwargs (app.llm.chat.get_chat_model's
    _JSON_OPERATIONS), langchain-openai routes the request through OpenAI's
    Responses API instead of the classic Chat Completions API, which returns
    a LIST of content blocks (one 'reasoning' block, one 'text' block, in
    either order) rather than a string -- _extract_text_block above pulls
    the actual answer out of that shape first when needed."""
    text = content if isinstance(content, str) else _extract_text_block(content)
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}")


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


def _dedupe_by_key(items: list, key) -> list:
    seen = set()
    deduped = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(item)
    return deduped


def _load_chunk_items(stem: str) -> list[dict] | None:
    chunk_path = document_dir_for(stem) / "chunks.json"
    if not chunk_path.is_file():
        return None
    chunked = json.loads(chunk_path.read_text())
    return [chunked["preamble"], *chunked["chunks"]]


def _require_document_text(stem: str) -> str:
    doc_path = document_dir_for(stem) / "raw.md"
    if not doc_path.is_file():
        raise FileNotFoundError(f"document not found: {stem}")
    return doc_path.read_text()


# In-flight progress reporting -----------------------------------------------
#
# discover_for_document/schema_for_document/extract_for_document (and their
# chunk-grouped inner functions) can each take anywhere from seconds to
# 1000s of seconds for a large document, all inside one synchronous HTTP
# request -- the frontend previously had no way to show anything better than
# a locally-ticking "N초" counter while waiting. ChunkProgress below persists
# a small JSON snapshot to documents/{stem}/progress/{operation}.json as the
# operation goes, so a GET route (main.py's /progress) can be polled from the
# browser while the POST request is still in flight -- the same
# write-to-a-file-so-another-request-can-read-it-mid-run idea as
# extract_graph.py's own extraction_progress/{node,edge}_proc_{N}.json, just
# generalized to all three chunk-grouped operations and reduced to a summary
# (counts/stage) rather than the raw per-group data.
#
# The three *_from_chunks functions run their map step concurrently (see
# generate_schema.py's _map_concurrently), so groups finish in whatever order
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
        passed straight as _map_concurrently's `on_item_done` callback,
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
