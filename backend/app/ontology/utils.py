"""Shared, dependency-light helpers used by two or more of generate_schema.py/
extract_graph.py/evolve_graph.py -- anything used by only one of those three
lives in that file instead, per app.ontology's own "leaf every other
submodule can import from" convention (see persistence.py, schema_validation.py)."""

import json
import os
import re

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
MAX_CHUNK_GROUP_CHARS = int(os.environ.get("MAX_CHUNK_GROUP_CHARS", 60_000))


def _check_document_length(document_text: str, max_chars: int | None = None) -> None:
    limit = max_chars if max_chars is not None else MAX_DOCUMENT_CHARS
    if len(document_text) > limit:
        raise ValueError(
            f"document is too long ({len(document_text)} chars, "
            f"max {limit}) to send to the LLM in one call"
        )


def parse_json_response(text: str) -> dict:
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
