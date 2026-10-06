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


_JSON_DECODER = json.JSONDecoder()


def parse_json_response(content: str | list) -> dict:
    """Parses a chat model's JSON response. `content` is usually already a
    plain string (langchain's normal `response.content` shape), but once
    `reasoning` is added to a call's model_kwargs (app.llm.chat.get_chat_model's
    _JSON_OPERATIONS), langchain-openai routes the request through OpenAI's
    Responses API instead of the classic Chat Completions API, which returns
    a LIST of content blocks (one 'reasoning' block, one 'text' block, in
    either order) rather than a string -- _extract_text_block above pulls
    the actual answer out of that shape first when needed.

    Parses via json.JSONDecoder.raw_decode rather than json.loads, so
    trailing garbage *after* an otherwise-complete, valid JSON value doesn't
    fail the whole parse -- observed live: a model can still tack on a
    leftover markdown code-fence closer (just "``", not even a full "```")
    after a response_format=json_object-mode reply that was never fenced to
    begin with, old habit from before that mode existed. raw_decode finds
    and returns the first complete JSON value in the string and ignores
    anything after it, which json.loads (whole-string-must-be-one-value)
    would reject outright as "Extra data"."""
    text = content if isinstance(content, str) else _extract_text_block(content)
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        obj, _end = _JSON_DECODER.raw_decode(stripped)
        return obj
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}")


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
