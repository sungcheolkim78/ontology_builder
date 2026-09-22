import pytest

from app.llm.chat import (
    MODEL_CATALOG,
    get_chat_model,
    get_model_max_tokens,
    get_model_name,
    set_model_name,
)


# Every operation whose prompts (app/llm/prompts.py) ask for a JSON object
# back -- see app.llm.chat._JSON_OPERATIONS's own comment for why this is an
# allowlist rather than "everything except a known-prose set".
JSON_OPERATIONS = (
    "discover_ontology", "generate_schema", "extract_graph", "validate_ontology", "propose_evolution",
    "consolidate_discovery", "consolidate_schema",
)


@pytest.mark.parametrize("operation", JSON_OPERATIONS)
def test_get_chat_model_adds_json_response_format_for_json_operations(operation):
    model = get_chat_model(operation)

    assert model.model_kwargs == {"response_format": {"type": "json_object"}}


@pytest.mark.parametrize("operation", JSON_OPERATIONS)
def test_get_chat_model_asks_for_low_reasoning_effort_for_json_operations(operation):
    # Verified live (see _JSON_OPERATIONS's own comment) that reasoning-token
    # volume, not chunk/document size, is what actually drives per-call
    # latency for these prompts -- this is the lever that cuts it.
    model = get_chat_model(operation)

    assert model.reasoning == {"effort": "low"}


@pytest.mark.parametrize("operation", [None, "summarize_document", "some_other_operation"])
def test_get_chat_model_omits_response_format_for_non_json_operations(operation):
    model = get_chat_model(operation)

    assert model.model_kwargs == {}


@pytest.mark.parametrize("operation", [None, "summarize_document", "some_other_operation"])
def test_get_chat_model_omits_reasoning_for_non_json_operations(operation):
    model = get_chat_model(operation)

    assert model.reasoning is None


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

    assert model.max_tokens == get_model_max_tokens("extract_graph") == 40_000


def test_model_catalog_caps_are_all_above_every_operation_cap():
    # Sanity check on the two tables' relationship: if this ever fails, some
    # OPERATION_MAX_TOKENS entry is no longer a *tighter* ceiling than every
    # cataloged model's own cap, which would silently defeat the "only ever
    # tightens, never loosens" guarantee get_model_max_tokens documents.
    from app.llm.chat import OPERATION_MAX_TOKENS

    smallest_model_cap = min(m["max_tokens"] for m in MODEL_CATALOG)
    assert all(cap <= smallest_model_cap for cap in OPERATION_MAX_TOKENS.values())


def test_consolidation_operations_get_a_larger_cap_than_their_own_single_group_call():
    # Reproduced live: merging a real 12-group document's 274 candidate
    # types needed ~16,700 output tokens (~6,700-8,000 of it reasoning) --
    # far more than a single group's own generate_schema()/discover_ontology()
    # call ever needs. See OPERATION_MAX_TOKENS's own comment for the numbers.
    assert get_model_max_tokens("consolidate_schema") > get_model_max_tokens("generate_schema")
    assert get_model_max_tokens("consolidate_discovery") > get_model_max_tokens("discover_ontology")


def test_consolidation_operations_follow_the_selected_model_of_their_single_group_counterpart():
    # _MODEL_SELECTION_ALIAS: consolidate_schema/consolidate_discovery are
    # their own operation keys (for OPERATION_MAX_TOKENS purposes only) --
    # nobody ever explicitly picks a model for them in the settings UI, so
    # without this alias they'd silently fall back to the unrelated
    # "default" bucket instead of following whatever a person picked for
    # "generate_schema"/"discover_ontology".
    set_model_name("anthropic/claude-opus-5", operation="generate_schema")
    set_model_name("z-ai/glm-5.2", operation="discover_ontology")
    try:
        assert get_model_name("consolidate_schema") == "anthropic/claude-opus-5"
        assert get_model_name("consolidate_discovery") == "z-ai/glm-5.2"
    finally:
        set_model_name(None, "generate_schema")
        set_model_name(None, "discover_ontology")


def test_consolidation_operations_fall_back_to_default_like_their_counterpart_would():
    assert get_model_name("consolidate_schema") == get_model_name("generate_schema")
    assert get_model_name("consolidate_discovery") == get_model_name("discover_ontology")
