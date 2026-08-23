import pytest

from app.telemetry import invoke_with_telemetry


class FakeResponse:
    def __init__(self, content, usage_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata


class FakeModel:
    model_name = "test-model"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.received_prompt = None

    def invoke(self, prompt):
        self.received_prompt = prompt
        if self.error:
            raise self.error
        return self.response


def test_invoke_with_telemetry_returns_model_response():
    model = FakeModel(response=FakeResponse("hello"))

    result = invoke_with_telemetry("test.operation", model, "some prompt")

    assert result.content == "hello"
    assert model.received_prompt == "some prompt"


def test_invoke_with_telemetry_passes_through_list_prompts():
    model = FakeModel(response=FakeResponse("hi"))
    messages = ["msg1", "msg2"]

    result = invoke_with_telemetry("test.operation", model, messages)

    assert result.content == "hi"
    assert model.received_prompt == messages


def test_invoke_with_telemetry_reraises_on_error():
    model = FakeModel(error=ValueError("boom"))

    with pytest.raises(ValueError, match="boom"):
        invoke_with_telemetry("test.operation", model, "some prompt")


def test_invoke_with_telemetry_works_without_configured_exporter():
    # No OTEL_EXPORTER_OTLP_ENDPOINT set in the test environment -- the
    # default no-op tracer must still let calls through with no error.
    model = FakeModel(response=FakeResponse("plain"))

    result = invoke_with_telemetry("test.operation", model, "prompt")

    assert result.content == "plain"
