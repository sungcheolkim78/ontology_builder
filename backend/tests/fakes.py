"""Fake models shared by the test modules. Tests install one with
`monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)`
(or `get_embedding_model`, `lambda: model`) -- those two names are the only
patch points, see CLAUDE.md "Testing LLM calls"."""

import threading

from app.preprocess.embeddings import EMBEDDING_DIM


def prompt_text(prompt):
    """Flattens a captured prompt (a plain string, or a list of langchain
    messages) into one string for substring assertions."""
    if isinstance(prompt, str):
        return prompt
    return "\n".join(getattr(m, "content", str(m)) for m in prompt)


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class RecordingChatModel:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("FakeResponse", (), {"content": self.content})()


class SequencedChatModel:
    """Returns each response in order, one per invoke() call. Groups now run
    concurrently, so the read-index-then-increment below is lock-protected --
    otherwise two threads could read the same index."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            content = self.responses[self.calls]
            self.calls += 1
        return type("FakeResponse", (), {"content": content})()


class LoggingSequencedChatModel:
    """Returns each response in order, one per invoke() call. Records the
    messages it was called with so tests can inspect what was actually sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        content = self.responses[len(self.calls) - 1]
        return type("FakeResponse", (), {"content": content})()


class FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.0] * EMBEDDING_DIM for _ in texts]
