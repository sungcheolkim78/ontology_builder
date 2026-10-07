"""Tests for app.ontology.schema_quality: the standalone schema diagnostics."""

import json
import threading

import pytest

from app.ontology.schema_quality import find_redundant_type_pairs, measure_schema_stability


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class SequencedChatModel:
    """Returns each response in order, one per invoke() call. The runs of
    measure_schema_stability invoke concurrently, so the read-then-increment is
    lock-protected."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            content = self.responses[self.calls]
            self.calls += 1
        return type("FakeResponse", (), {"content": content})()


def test_find_redundant_type_pairs_flags_near_duplicate_descriptions(monkeypatch):
    schema = {
        "node_types": [
            {"name": "Customer", "description": "a paying customer"},
            {"name": "Client", "description": "a paying customer"},
            {"name": "Product", "description": "something sold"},
        ],
        "edge_types": [],
    }

    class VectorFakeEmbeddingModel:
        def embed_documents(self, texts):
            # Customer/Client get identical vectors; Product gets an
            # orthogonal one, so only the first pair should pass threshold.
            return [[0.0, 1.0] if text.startswith("Product") else [1.0, 0.0] for text in texts]

    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: VectorFakeEmbeddingModel())

    pairs = find_redundant_type_pairs(schema, threshold=0.9)

    assert pairs == [{"element_type": "node_type", "a": "Customer", "b": "Client", "similarity": pytest.approx(1.0)}]


def test_find_redundant_type_pairs_skips_types_with_fewer_than_two_entries():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}

    pairs = find_redundant_type_pairs(schema)

    assert pairs == []


def test_measure_schema_stability_perfect_agreement_across_runs(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = measure_schema_stability("some document text", runs=3)

    assert result["avg_jaccard_similarity"] == 1.0
    assert result["type_name_sets"] == [["Person"]] * 3


def test_measure_schema_stability_disagreement_lowers_similarity(monkeypatch):
    schemas = [
        {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []},
        {"node_types": [{"name": "Individual", "description": "a person"}], "edge_types": []},
    ]
    fake_model = SequencedChatModel([json.dumps(s) for s in schemas])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    result = measure_schema_stability("some document text", runs=2)

    assert result["avg_jaccard_similarity"] == 0.0


def test_measure_schema_stability_raises_for_fewer_than_two_runs():
    with pytest.raises(ValueError):
        measure_schema_stability("doc", runs=1)
