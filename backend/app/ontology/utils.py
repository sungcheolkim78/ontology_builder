"""Shared, dependency-light helpers used by two or more of generate_schema.py/
extract_graph.py/evolve_graph.py -- anything used by only one of those three
lives in that file instead, per app.ontology's own "leaf every other
submodule can import from" convention (see persistence.py, schema_validation.py)."""

import os

from app.utils.paths import chunk_path_for, raw_path_for
from app.utils.store import read_json

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



def _check_document_length(document_text: str, max_chars: int | None = None) -> None:
    limit = max_chars if max_chars is not None else MAX_DOCUMENT_CHARS
    if len(document_text) > limit:
        raise ValueError(
            f"document is too long ({len(document_text)} chars, "
            f"max {limit}) to send to the LLM in one call"
        )


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
    chunked = read_json(chunk_path_for(stem))
    if chunked is None:
        return None
    return [chunked["preamble"], *chunked["chunks"]]


def _require_document_text(stem: str) -> str:
    doc_path = raw_path_for(stem)
    if not doc_path.is_file():
        raise FileNotFoundError(f"document not found: {stem}")
    return doc_path.read_text()
