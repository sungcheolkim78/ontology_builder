import pytest

from app.llm.chat import (
    MODEL_CATALOG,
    get_chat_model,
    get_model_max_tokens,
    set_model_name,
)


# Every operation whose prompts (app/llm/prompts.py) ask for a JSON object
# back -- see app.llm.chat._JSON_OPERATIONS's own comment for why this is an
# allowlist rather than "everything except a known-prose set".
JSON_OPERATIONS = ("discover_ontology", "generate_schema", "extract_graph", "validate_ontology", "propose_evolution")


@pytest.mark.parametrize("operation", JSON_OPERATIONS)
def test_get_chat_model_adds_json_response_format_for_json_operations(operation):
    model = get_chat_model(operation)

    assert model.model_kwargs == {"response_format": {"type": "json_object"}}


@pytest.mark.parametrize("operation", [None, "summarize_document", "some_other_operation"])
def test_get_chat_model_omits_response_format_for_non_json_operations(operation):
    model = get_chat_model(operation)

    assert model.model_kwargs == {}


def test_get_model_max_tokens_uses_operation_cap_when_smaller_than_model_cap():
    # Every cataloged model's own cap is >= the largest OPERATION_MAX_TOKENS
    # entry, so under normal catalog selection the operation cap is always
    # the tighter (and thus winning) one.
    assert get_model_max_tokens("generate_schema") == 8_000
    assert get_model_max_tokens("discover_ontology") == 16_000


def test_get_model_max_tokens_uses_model_cap_when_smaller_than_operation_cap(monkeypatch):
    monkeypatch.setattr(
        "app.llm.chat.MODEL_CATALOG", [{"id": "test/tiny-model", "max_tokens": 500}]
    )
    set_model_name("test/tiny-model", operation="generate_schema")
    try:
        assert get_model_max_tokens("generate_schema") == 500
    finally:
        set_model_name(None, "generate_schema")


def test_get_model_max_tokens_returns_none_when_neither_cap_applies(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "some/uncataloged-model")

    assert get_model_max_tokens(None) is None
    assert get_model_max_tokens("some_operation_with_no_cap_entry") is None


def test_get_chat_model_max_tokens_matches_get_model_max_tokens():
    model = get_chat_model("extract_graph")

    assert model.max_tokens == get_model_max_tokens("extract_graph") == 16_000


def test_model_catalog_caps_are_all_above_every_operation_cap():
    # Sanity check on the two tables' relationship: if this ever fails, some
    # OPERATION_MAX_TOKENS entry is no longer a *tighter* ceiling than every
    # cataloged model's own cap, which would silently defeat the "only ever
    # tightens, never loosens" guarantee get_model_max_tokens documents.
    from app.llm.chat import OPERATION_MAX_TOKENS

    smallest_model_cap = min(m["max_tokens"] for m in MODEL_CATALOG)
    assert all(cap <= smallest_model_cap for cap in OPERATION_MAX_TOKENS.values())
