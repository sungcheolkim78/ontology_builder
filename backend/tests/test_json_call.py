"""Tests for app.llm.json_call.call_json, crossing its one interface with a
fake chat model patched in at the module's own `get_chat_model` seam -- no
real LLM, no per-caller patching."""

import json

import pytest

from app.llm.json_call import call_json, parse_json_response


class FakeChatModel:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("FakeResponse", (), {"content": self.content})()


@pytest.fixture
def fake_model(monkeypatch):
    """Installs a FakeChatModel as the module's model, recording which
    operation each get_chat_model() call asked for."""

    def install(content):
        model = FakeChatModel(content if isinstance(content, str) else json.dumps(content))
        asked = []

        def fake_get_chat_model(operation=None):
            asked.append(operation)
            return model

        monkeypatch.setattr("app.llm.json_call.get_chat_model", fake_get_chat_model)
        model.asked = asked
        return model

    return install


def test_call_json_returns_the_parsed_response_for_the_operation(fake_model):
    model = fake_model({"node_types": [], "edge_types": []})

    result = call_json("generate_schema", "the prompt")

    assert result == {"node_types": [], "edge_types": []}
    assert model.asked == ["generate_schema"]
    assert model.prompts == ["the prompt"]


def test_a_missing_required_field_raises_naming_the_operation_and_field(fake_model):
    fake_model({"node_types": []})

    with pytest.raises(ValueError, match=r"generate_schema.*edge_types"):
        call_json("generate_schema", "p")


@pytest.mark.parametrize(
    "operation, payload",
    [
        ("generate_schema", {"node_types": [], "edge_types": "not a list"}),
        ("validate_ontology", {"validation_summary": [], "issues": []}),
        ("validate_ontology", {"validation_summary": {}, "issues": {}}),
    ],
)
def test_a_wrongly_typed_required_field_raises(fake_model, operation, payload):
    fake_model(payload)

    with pytest.raises(ValueError, match=operation):
        call_json(operation, "p")


def test_a_dict_and_a_list_field_are_both_accepted_where_declared(fake_model):
    fake_model({"validation_summary": {"ok": True}, "issues": [], "extra": 1})

    assert call_json("validate_ontology", "p")["extra"] == 1


def test_unparseable_output_raises_value_error(fake_model):
    fake_model("this is not json")

    with pytest.raises(ValueError, match="valid JSON"):
        call_json("generate_schema", "p")


def test_an_unknown_operation_raises_value_error(fake_model):
    fake_model({})

    with pytest.raises(ValueError, match="unknown operation"):
        call_json("no_such_operation", "p")


def test_the_registered_telemetry_name_is_used(fake_model, monkeypatch):
    fake_model({"node_types": [], "edge_types": []})
    names = []
    real = __import__("app.llm.json_call", fromlist=["invoke_with_telemetry"]).invoke_with_telemetry

    def spy(operation, model, prompt, *args, **kwargs):
        names.append(operation)
        return real(operation, model, prompt, *args, **kwargs)

    monkeypatch.setattr("app.llm.json_call.invoke_with_telemetry", spy)

    call_json("generate_schema", "p")

    assert names == ["generate-schema"]


def test_a_response_that_is_not_a_json_object_raises_value_error(fake_model):
    fake_model("[1, 2, 3]")

    with pytest.raises(ValueError, match="not a JSON object"):
        call_json("generate_schema", "p")


# --- parse_json_response -----------------------------------------------


def test_parse_json_response_parses_a_plain_string():
    assert parse_json_response('{"node_types": []}') == {"node_types": []}


def test_parse_json_response_strips_a_markdown_code_fence():
    assert parse_json_response('```json\n{"ok": true}\n```') == {"ok": True}


def test_parse_json_response_raises_on_invalid_json():
    with pytest.raises(ValueError):
        parse_json_response("not json at all")


def test_parse_json_response_extracts_text_block_from_a_content_list():
    # get_chat_model's reasoning=low kwarg (app/llm/chat.py) routes the
    # request through OpenAI's Responses API, which returns `response.content`
    # as a list of blocks instead of a plain string once that's set.
    content = [
        {"type": "reasoning", "content": [{"text": "thinking...", "type": "reasoning_text"}]},
        {"type": "text", "text": '{"node_types": []}'},
    ]

    assert parse_json_response(content) == {"node_types": []}


def test_parse_json_response_finds_text_block_regardless_of_order():
    # Verified live that block order is provider-dependent -- Qwen returns
    # [reasoning, text], Gemini returns [text, reasoning] for the same
    # request shape.
    content = [
        {"type": "text", "text": '{"ok": true}'},
        {"type": "reasoning", "content": []},
    ]

    assert parse_json_response(content) == {"ok": True}


def test_parse_json_response_raises_when_no_text_block_present():
    with pytest.raises(ValueError):
        parse_json_response([{"type": "reasoning", "content": []}])


def test_parse_json_response_ignores_trailing_garbage_after_valid_json():
    # Regression: observed live -- a response_format=json_object reply is
    # never itself markdown-fenced, but a model can still tack on a leftover
    # fence-closer habit afterward (here, just "``", not even a full "```"),
    # which used to fail the whole parse with "Extra data" even though a
    # complete, valid JSON value came first.
    content = '{"node_types": [], "edge_types": []}\n``'

    assert parse_json_response(content) == {"node_types": [], "edge_types": []}


def test_parse_json_response_ignores_trailing_garbage_in_a_content_list():
    content = [
        {"type": "reasoning", "content": []},
        {"type": "text", "text": '{"ok": true}\n```'},
    ]

    assert parse_json_response(content) == {"ok": True}
