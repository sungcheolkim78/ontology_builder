import pytest
from langchain_core.exceptions import ModelConnectionError

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


class FlakyModel:
    model_name = "test-model"

    def __init__(self, fail_times, error, response):
        self.fail_times = fail_times
        self.error = error
        self.response = response
        self.call_count = 0

    def invoke(self, prompt):
        self.call_count += 1
        if self.call_count <= self.fail_times:
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


def test_invoke_with_telemetry_retries_on_connection_error_then_succeeds():
    model = FlakyModel(
        fail_times=2,
        error=ModelConnectionError("transient"),
        response=FakeResponse("recovered"),
    )

    result = invoke_with_telemetry(
        "test.operation", model, "prompt", max_retries=2, retry_delay=0
    )

    assert result.content == "recovered"
    assert model.call_count == 3


def test_invoke_with_telemetry_reraises_after_exhausting_retries():
    model = FlakyModel(
        fail_times=99,
        error=ModelConnectionError("still down"),
        response=FakeResponse("never"),
    )

    with pytest.raises(ModelConnectionError, match="still down"):
        invoke_with_telemetry("test.operation", model, "prompt", max_retries=2, retry_delay=0)

    assert model.call_count == 3


def test_invoke_with_telemetry_does_not_retry_non_connection_errors():
    model = FlakyModel(fail_times=1, error=ValueError("boom"), response=FakeResponse("x"))

    with pytest.raises(ValueError, match="boom"):
        invoke_with_telemetry("test.operation", model, "prompt", max_retries=2, retry_delay=0)

    assert model.call_count == 1
