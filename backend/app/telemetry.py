import os
import time
from contextlib import contextmanager

from langchain_core.exceptions import ModelConnectionError

_client = None
_configured = False


def configure_telemetry() -> None:
    """Initializes the Langfuse client from LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY/
    LANGFUSE_HOST, if LANGFUSE_PUBLIC_KEY is set (podman-compose wires these from
    backend/.env, pointing at the self-hosted Langfuse server -- see
    docs/LANGFUSE.md for what that server is and how to run it). Otherwise leaves
    `_client` as None, so instrumented code stays safe to call with zero
    configuration (e.g. in tests): `get_client()` itself degrades to a silent
    no-op without credentials, but constructing it unconditionally would print an
    auth-check warning on every test run, so we skip that construction entirely
    when there's no key to authenticate with."""
    global _configured, _client
    if _configured:
        return
    _configured = True

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return

    from langfuse import get_client

    _client = get_client()


def _prompt_text(prompt) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(getattr(m, "content", str(m)) for m in prompt)


class _NoopObservation:
    """Stand-in for a Langfuse observation when telemetry isn't configured, so
    invoke_with_telemetry/embed_with_telemetry don't need an `if _client` branch
    at every call site -- `with _start_observation(...) as x: x.update(...)`
    works identically whether or not a real client is behind it."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def update(self, **kwargs):
        pass


def _start_observation(as_type: str, name: str, model_name: str, input_data):
    if _client is None:
        return _NoopObservation()
    return _client.start_as_current_observation(
        as_type=as_type, name=name, model=model_name, input=input_data
    )


@contextmanager
def trace(
    name: str,
    *,
    session_id: str | None = None,
    tags: list | None = None,
    metadata: dict | None = None,
    input: object = None,
):
    """Opens one root Langfuse span for a whole logical operation -- one
    `/api/chat` turn, one document's schema-generation request, etc. -- so
    every invoke_with_telemetry/embed_with_telemetry call made while it's
    open nests under it as a child observation instead of each becoming its
    own top-level trace. Per Langfuse's own trace-instrumentation guidance
    (https://langfuse.com/docs/observability/best-practices), a trace is "one
    self-contained unit of work" (a chat turn, a pipeline run), not a single
    model call -- so a route handler that makes several LLM calls to serve
    one request (e.g. `analyze-question` then `answer-chat`, or one
    `generate-schema` call per chunk group plus a `consolidate-schema-types`
    reduce) should show up as one trace with several nested generations, not
    several unrelated traces. `session_id`/`tags`/`metadata` are propagated
    to every nested observation via `propagate_attributes` -- see that
    function's own docstring for why attributes must be set this early
    (before any child observation is created) to reliably show up in
    Langfuse's aggregations. No-ops (still runs the wrapped code, yielding a
    stand-in with the same shape) when telemetry isn't configured.

    `input` should be what a reviewer needs at a glance -- the user's actual
    question, not the raw request body -- per the same best-practices page:
    "the root observation's input and output are shown in the tracing table,
    read by evaluators, and compared across runs." The caller is responsible
    for calling `.update(output=...)` on the yielded span with an equally
    glanceable summary before the `with` block exits."""
    configure_telemetry()
    if _client is None:
        yield _NoopObservation()
        return

    from langfuse import propagate_attributes

    with _client.start_as_current_observation(as_type="span", name=name, input=input) as span:
        with propagate_attributes(session_id=session_id, tags=tags, metadata=metadata):
            yield span


def _call_with_retry(observation, call, max_retries: int, retry_delay: float):
    """Shared retry loop for invoke_with_telemetry/embed_with_telemetry: retries
    `call()` up to `max_retries` times, with a fixed delay, on
    ModelConnectionError (the provider-agnostic base class every langchain model
    raises for connection-level errors) -- a real, if infrequent, failure mode of
    the OpenRouter connection in this environment. Any other exception is not
    retried. Records the retry count and, on failure, an ERROR level plus
    status_message on `observation` before re-raising -- the caller still sets
    its own output/usage_details since the response shape differs per call
    site."""
    attempt = 0
    while True:
        try:
            result = call()
            observation.update(metadata={"retry_count": attempt})
            return result
        except ModelConnectionError as exc:
            attempt += 1
            if attempt > max_retries:
                observation.update(
                    level="ERROR",
                    status_message=str(exc),
                    metadata={"retry_count": attempt - 1},
                )
                raise
            time.sleep(retry_delay)
        except Exception as exc:
            observation.update(level="ERROR", status_message=str(exc))
            raise


def invoke_with_telemetry(operation: str, model, prompt, max_retries: int = 2, retry_delay: float = 1.0):
    """Calls model.invoke(prompt), recording a Langfuse `generation` observation
    with model/prompt/response/usage -- Langfuse's generation-level view (cost,
    token usage, prompt/response side by side) is the reason this module exists,
    versus a generic OTEL span. Observation timing is captured automatically.

    Transient network failures (langchain_core.exceptions.ModelConnectionError,
    the provider-agnostic base class every langchain chat model raises for
    connection-level errors) are retried up to `max_retries` times with a fixed
    delay -- this is a real, if infrequent, failure mode of the OpenRouter
    connection in this environment. Any other exception is not retried."""
    configure_telemetry()
    model_name = getattr(model, "model_name", None) or getattr(model, "model", "unknown")
    prompt_text = _prompt_text(prompt)

    with _start_observation("generation", operation, str(model_name), prompt_text) as generation:
        response = _call_with_retry(generation, lambda: model.invoke(prompt), max_retries, retry_delay)

        usage = getattr(response, "usage_metadata", None)
        generation.update(
            output=response.content,
            usage_details=(
                {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                }
                if usage
                else None
            ),
        )
        return response


def embed_with_telemetry(
    operation: str, model, texts: list, max_retries: int = 2, retry_delay: float = 1.0
) -> list:
    """Calls model.embed_documents(texts), recording an `embedding` observation
    analogous to invoke_with_telemetry's -- text count in/vector count out,
    since an embedding call's input is a batch of independent strings and its
    output is vectors, not a single reply worth logging in full."""
    configure_telemetry()
    model_name = getattr(model, "model", "unknown")

    with _start_observation(
        "embedding", operation, str(model_name), {"input_count": len(texts)}
    ) as generation:
        vectors = _call_with_retry(
            generation, lambda: model.embed_documents(texts), max_retries, retry_delay
        )

        generation.update(
            output={"output_count": len(vectors)},
            usage_details={"input_tokens": len(texts)},
        )
        return vectors
