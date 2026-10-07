"""call_json: the one place an LLM call that must return a JSON object is made,
parsed and shape-checked. See app/llm/operations.py for what each operation
declares, and CONTEXT.md for the vocabulary."""

import json
import re

from app.llm.chat import get_chat_model
from app.llm.operations import OPERATIONS
from app.llm.telemetry import embed_with_telemetry, invoke_with_telemetry
from app.preprocess.embeddings import get_embedding_model


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
    `reasoning` is added to a call's model kwargs (see app.llm.operations),
    langchain-openai routes the request through OpenAI's
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


def embed(name: str, texts: list) -> list:
    """Embeds `texts` with the app's embedding model and returns one vector per
    text. `name` is the telemetry observation name (e.g. "embed-query"); unlike
    a chat operation an embedding call has no model choice or output limit of
    its own, so there is nothing to register beyond that name."""
    model = get_embedding_model()
    return embed_with_telemetry(name, model, texts)


def call_text(operation: str, prompt) -> str:
    """Calls the model for the prose `operation` with `prompt` (a string or a
    list of langchain messages) and returns the reply text. The prose
    counterpart of call_json: same seam, same telemetry and connection-retry
    behaviour, no parsing."""
    spec = OPERATIONS.get(operation)
    if spec is None:
        raise ValueError(f"unknown operation: {operation!r}")
    if spec.json:
        raise ValueError(f"{operation} is not a prose operation; use call_json")
    model = get_chat_model(operation)
    response = invoke_with_telemetry(spec.telemetry_name, model, prompt)
    return response.content


def call_json(operation: str, prompt) -> dict:
    """Calls the model for `operation` with `prompt` (a string or a list of
    langchain messages), parses the reply as a JSON object and checks that it
    has every field the operation declares (see app/llm/operations.py), each
    of the declared type. Raises ValueError for anything else -- unparseable
    output, a non-object, a missing or wrongly typed field. Nothing is retried
    beyond the connection-error retry invoke_with_telemetry already does."""
    spec = OPERATIONS.get(operation)
    if spec is None:
        raise ValueError(f"unknown operation: {operation!r}")
    if not spec.json:
        raise ValueError(f"{operation} is not a JSON operation; use call_text")
    model = get_chat_model(operation)
    response = invoke_with_telemetry(spec.telemetry_name, model, prompt)
    result = parse_json_response(response.content)
    if not isinstance(result, dict):
        raise ValueError(f"{operation} response is not a JSON object")
    invalid = [
        f"{name} ({expected.__name__})"
        for name, expected in spec.required.items()
        if not isinstance(result.get(name), expected)
    ]
    if invalid:
        raise ValueError(f"{operation} JSON missing or invalid: {', '.join(invalid)}")
    return result
