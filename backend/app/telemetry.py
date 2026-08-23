import os
import time

from langchain_core.exceptions import ModelConnectionError
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

SERVICE_NAME = "ontology-builder-backend"

_configured = False


def configure_telemetry() -> None:
    """Registers a real TracerProvider exporting to OTEL_EXPORTER_OTLP_ENDPOINT,
    if that env var is set. Otherwise leaves the OpenTelemetry API's default
    no-op tracer in place, so instrumented code is always safe to call (e.g.
    in tests, where no collector is running) -- it just won't export anywhere."""
    global _configured
    if _configured:
        return
    _configured = True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


_tracer = trace.get_tracer(SERVICE_NAME)


def _prompt_text(prompt) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(getattr(m, "content", str(m)) for m in prompt)


def invoke_with_telemetry(operation: str, model, prompt, max_retries: int = 2, retry_delay: float = 1.0):
    """Calls model.invoke(prompt), recording a span with model/prompt/response
    metadata (not the prompt/response text itself). Span timing is captured
    automatically by OpenTelemetry, so no manual duration tracking here.

    Transient network failures (langchain_core.exceptions.ModelConnectionError,
    the provider-agnostic base class every langchain chat model raises for
    connection-level errors) are retried up to `max_retries` times with a
    fixed delay -- this is a real, if infrequent, failure mode of the
    OpenRouter connection in this environment. Any other exception is not
    retried."""
    model_name = getattr(model, "model_name", None) or getattr(model, "model", "unknown")

    with _tracer.start_as_current_span(f"llm.{operation}") as span:
        span.set_attribute("gen_ai.operation.name", operation)
        span.set_attribute("gen_ai.request.model", str(model_name))
        span.set_attribute("gen_ai.prompt.length", len(_prompt_text(prompt)))

        attempt = 0
        while True:
            try:
                response = model.invoke(prompt)
                break
            except ModelConnectionError as exc:
                attempt += 1
                if attempt > max_retries:
                    span.record_exception(exc)
                    span.set_attribute("gen_ai.call.success", False)
                    span.set_attribute("gen_ai.retry.count", attempt - 1)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
                time.sleep(retry_delay)
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("gen_ai.call.success", False)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

        span.set_attribute("gen_ai.retry.count", attempt)
        span.set_attribute("gen_ai.call.success", True)
        span.set_attribute("gen_ai.response.length", len(response.content))
        usage = getattr(response, "usage_metadata", None)
        if usage:
            span.set_attribute("gen_ai.usage.input_tokens", usage.get("input_tokens", 0))
            span.set_attribute("gen_ai.usage.output_tokens", usage.get("output_tokens", 0))
        return response
