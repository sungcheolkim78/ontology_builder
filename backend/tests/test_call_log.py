"""Tests for app.llm.call_log: recording the model, duration and token use of every
LLM call made inside a `with CallLog()` block, without changing what is called."""

import json
import threading
import time

import pytest

from app.llm import calls
from app.llm.call_log import CallLog
from app.llm.calls import call_json, embed


class _Usage(dict):
    pass


class TimedModel:
    """A chat model that takes `delay` seconds, reports `model_name` and token usage."""

    def __init__(self, content='{"nodes": [], "edges": []}', delay=0.0, model_name="vendor/model-x", tokens=(10, 5)):
        self.content, self.delay, self.model_name, self.tokens = content, delay, model_name, tokens

    def invoke(self, messages):
        time.sleep(self.delay)
        response = type("R", (), {"content": self.content})()
        response.usage_metadata = {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}
        return response


class FailingModel:
    model_name = "vendor/model-x"

    def invoke(self, messages):
        raise RuntimeError("provider is down")


def _use(monkeypatch, model):
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)


def test_a_call_is_recorded_with_its_operation_model_duration_and_tokens(monkeypatch):
    _use(monkeypatch, TimedModel(delay=0.02))

    with CallLog() as log:
        call_json("extract_graph", "prompt")

    (call,) = log.calls
    assert call["operation"] == "extract-graph"  # the telemetry name of the registered operation
    assert call["model"] == "vendor/model-x"
    assert call["seconds"] >= 0.02
    assert (call["input_tokens"], call["output_tokens"]) == (10, 5)
    assert call["error"] is None


def test_the_summary_totals_calls_seconds_and_tokens_per_operation(monkeypatch):
    _use(monkeypatch, TimedModel(delay=0.01))

    with CallLog() as log:
        call_json("extract_graph", "a")
        call_json("extract_graph", "b")
    summary = log.summary()

    assert summary["calls"] == 2 and summary["errors"] == 0
    assert summary["models"] == ["vendor/model-x"]
    assert summary["by_operation"]["extract-graph"]["calls"] == 2
    assert summary["by_operation"]["extract-graph"]["input_tokens"] == 20
    assert summary["by_operation"]["extract-graph"]["seconds_max"] >= 0.01
    assert summary["seconds_in_calls"] >= 0.02


def test_a_failing_call_is_recorded_as_an_error_and_still_raises(monkeypatch):
    _use(monkeypatch, FailingModel())

    with CallLog() as log:
        with pytest.raises(RuntimeError):
            call_json("extract_graph", "prompt")

    assert log.summary()["errors"] == 1
    assert log.calls[0]["error"] == "RuntimeError: provider is down"


def test_an_embedding_call_is_recorded_too(monkeypatch):
    class Embeddings:
        model = "vendor/embed"

        def embed_documents(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: Embeddings())

    with CallLog() as log:
        embed("embed-eval-items", ["a", "b", "c"])

    (call,) = log.calls
    assert (call["operation"], call["model"], call["kind"]) == ("embed-eval-items", "vendor/embed", "embedding")


def test_calls_made_from_other_threads_are_all_recorded(monkeypatch):
    _use(monkeypatch, TimedModel(delay=0.01))

    with CallLog() as log:
        threads = [threading.Thread(target=lambda: call_json("extract_graph", "p")) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]

    assert log.summary()["calls"] == 8


def test_the_real_functions_are_back_after_the_block_and_nothing_is_recorded_outside_it(monkeypatch):
    _use(monkeypatch, TimedModel())
    original = calls.invoke_with_telemetry

    with CallLog() as log:
        assert calls.invoke_with_telemetry is not original
    call_json("extract_graph", "p")

    assert calls.invoke_with_telemetry is original
    assert log.calls == []
