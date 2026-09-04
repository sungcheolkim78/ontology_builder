import json

import pytest

from app import graphdb
from app.embeddings import EMBEDDING_DIM
from app.graphrag import (
    analyze_question,
    search_graph,
)

NODES = [
    {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
    {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    {"id": "n3", "label": "Charles Babbage", "type": "Person"},
    {"id": "n4", "label": "Royal Society", "type": "Organization"},
]
EDGES = [
    {"source": "n1", "target": "n2", "type": "WORKED_ON"},
    {"source": "n3", "target": "n2", "type": "WORKED_ON"},
    {"source": "n3", "target": "n4", "type": "MEMBER_OF"},
]

SCHEMA = {
    "node_types": [
        {"name": "Person", "description": "a person"},
        {"name": "Concept", "description": "a concept"},
        {"name": "Organization", "description": "an organization"},
    ],
    "edge_types": [
        {"name": "WORKED_ON", "description": "worked on", "source": "Person", "target": "Concept"},
        {"name": "MEMBER_OF", "description": "member of", "source": "Person", "target": "Organization"},
    ],
}

STEM = "doc_raw"


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class FakeEmbeddingModel:
    """Returns the same fixed vector for every text -- good enough for
    tests that only need embedding calls to not hit the network. Tests
    that actually verify similarity ranking pass their own vector."""

    def __init__(self, vector=None):
        self.vector = vector if vector is not None else [0.0] * EMBEDDING_DIM
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(texts)
        return [self.vector for _ in texts]


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    monkeypatch.setattr("app.graphrag.get_embedding_model", lambda: FakeEmbeddingModel())


class SequencedChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        content = self.responses[len(self.calls) - 1]
        return type("FakeResponse", (), {"content": content})()


def _remove_db_path():
    import shutil

    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_dir():
            shutil.rmtree(graphdb.DB_PATH)
        else:
            graphdb.DB_PATH.unlink()


def setup_function():
    graphdb.reset_connection()
    _remove_db_path()


def teardown_function():
    graphdb.reset_connection()
    _remove_db_path()


def test_analyze_question_parses_and_filters_hallucinated_types(monkeypatch):
    monkeypatch.setattr(
        "app.graphrag.get_chat_model",
        lambda: FakeChatModel(
            json.dumps(
                {
                    "node_types": ["Person", "NotARealType"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"], "NotARealType": ["x"]},
                }
            )
        ),
    )

    result = analyze_question("What did Ada Lovelace work on?", SCHEMA)

    assert result == {
        "node_types": ["Person"],
        "edge_types": ["WORKED_ON"],
        "keywords": {"Person": ["Ada Lovelace"]},
        "property_filters": {},
    }


def test_analyze_question_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: FakeChatModel("not json"))

    try:
        analyze_question("some question", SCHEMA)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_search_graph_finds_context_when_types_and_keywords_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["edge_types"] == ["WORKED_ON"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Analytical Engine" in result["context"]
    assert len(model.calls) == 1  # single combined analysis call
    # 1-hop expansion from Ada Lovelace also pulls in Charles Babbage, since
    # he shares the Analytical Engine node via his own WORKED_ON edge.
    assert {n["label"] for n in result["related_nodes"]} == {
        "Ada Lovelace",
        "Analytical Engine",
        "Charles Babbage",
    }
    assert {e["type"] for e in result["related_edges"]} == {"WORKED_ON"}


def test_search_graph_skips_keyword_extraction_when_no_types_relevant(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [json.dumps({"node_types": [], "edge_types": [], "keywords": {}})]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("completely unrelated question", SCHEMA, STEM, hops=1)

    assert result == {
        "node_types": [],
        "edge_types": [],
        "context": None,
        "related_nodes": [],
        "related_edges": [],
    }
    assert len(model.calls) == 1


def test_search_graph_falls_back_to_all_instances_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": [],
                    "keywords": {"Person": ["someone who does not exist in the graph"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("who are the people?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Charles Babbage" in result["context"]


def test_search_graph_falls_back_per_type_when_only_one_type_has_no_keyword_match(monkeypatch):
    # Person has a real keyword match, but Concept was also determined
    # relevant and got no matching keyword -- Concept should still fall back
    # to all its instances instead of contributing nothing just because
    # Person's search succeeded.
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person", "Concept"],
                    "edge_types": [],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=0)

    assert result["node_types"] == ["Person", "Concept"]
    assert {n["label"] for n in result["related_nodes"]} == {"Ada Lovelace", "Analytical Engine"}


def test_search_graph_prefers_embedding_match_over_all_instances_when_available(monkeypatch):
    # Ada's embedding is set to exactly the (mocked) query vector; Charles's
    # is orthogonal to it. With the embedding fallback's top_k capped at 1,
    # only Ada should be selected. If embedding search weren't actually
    # ranking -- e.g. if this silently fell through to the "all instances"
    # tier -- Charles would show up too.
    monkeypatch.setattr("app.graphrag.EMBEDDING_FALLBACK_TOP_K", 1)
    query_vector = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    orthogonal_vector = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "embedding": query_vector},
        {"id": "n3", "label": "Charles Babbage", "type": "Person", "embedding": orthogonal_vector},
    ]
    graphdb.write_graph(STEM, nodes, [])
    model = SequencedChatModel(
        [json.dumps({"node_types": ["Person"], "edge_types": [], "keywords": {}})]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.graphrag.get_embedding_model", lambda: FakeEmbeddingModel(query_vector))

    result = search_graph("who is Ada?", SCHEMA, STEM, hops=0)

    assert {n["label"] for n in result["related_nodes"]} == {"Ada Lovelace"}


def test_search_graph_uses_property_filter_when_keyword_and_embedding_both_miss(monkeypatch):
    # Neither find_relevant_nodes (no keyword names a specific instance --
    # this is a threshold question, not a named one) nor find_similar_nodes
    # (no node here has a stored embedding) can answer "which coverage pays
    # >= 30". Only the property-filter tier can -- if it didn't run, this
    # would fall through to "every Coverage instance" and wrongly include
    # the 10-amount node too.
    schema_with_properties = {
        "node_types": [
            {
                "name": "Coverage",
                "description": "a coverage",
                "properties": {"amount": {"datatype": "number"}},
            }
        ],
        "edge_types": [],
    }
    nodes = [
        {"id": "c1", "label": "암보장", "type": "Coverage", "properties": {"amount": "50"}},
        {"id": "c2", "label": "골절보장", "type": "Coverage", "properties": {"amount": "10"}},
    ]
    graphdb.write_graph(STEM, nodes, [])
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Coverage"],
                    "edge_types": [],
                    "keywords": {},
                    "property_filters": {
                        "Coverage": {"property": "amount", "operator": "gte", "value": "30"}
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph(
        "30 이상 지급하는 보장은?", schema_with_properties, STEM, hops=0
    )

    assert {n["label"] for n in result["related_nodes"]} == {"암보장"}


def test_search_graph_falls_back_to_all_edges_of_type_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [json.dumps({"node_types": [], "edge_types": ["MEMBER_OF"], "keywords": {}})]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("what memberships exist?", SCHEMA, STEM, hops=1)

    assert result["edge_types"] == ["MEMBER_OF"]
    assert result["context"] is not None
    assert "Charles Babbage" in result["context"]
    assert "Royal Society" in result["context"]


def test_search_graph_returns_none_when_determined_type_has_no_instances(monkeypatch):
    nodes_without_organizations = [n for n in NODES if n["type"] != "Organization"]
    edges_without_organizations = [e for e in EDGES if e["type"] != "MEMBER_OF"]
    graphdb.write_graph(STEM, nodes_without_organizations, edges_without_organizations)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Organization"],
                    "edge_types": [],
                    "keywords": {"Organization": ["nonexistent keyword"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("what organizations?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Organization"]
    assert result["context"] is None


def test_search_graph_includes_node_and_edge_detail_in_context(monkeypatch):
    nodes_with_detail = [
        {
            "id": "n1",
            "label": "Ada Lovelace",
            "type": "Person",
            "detail": "Corresponded with Babbage from 1833 onward.",
        },
        {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    ]
    edges_with_detail = [
        {
            "source": "n1",
            "target": "n2",
            "type": "WORKED_ON",
            "detail": "Wrote the first published algorithm for the machine in 1843.",
        },
    ]
    graphdb.write_graph(STEM, nodes_with_detail, edges_with_detail)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert "Corresponded with Babbage from 1833 onward." in result["context"]
    assert "Wrote the first published algorithm for the machine in 1843." in result["context"]


def test_search_graph_context_omits_missing_detail_gracefully(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["context"] is not None
    assert "None" not in result["context"]


def test_search_graph_includes_evidence_and_source_section_in_context(monkeypatch):
    nodes_with_evidence = [
        {
            "id": "n1",
            "label": "Ada Lovelace",
            "type": "Person",
            "evidence_text": "Ada Lovelace worked on the Analytical Engine.",
            "source_section": "제1조",
        },
        {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    ]
    edges_with_evidence = [
        {
            "source": "n1",
            "target": "n2",
            "type": "WORKED_ON",
            "evidence_text": "worked on the Analytical Engine",
        },
    ]
    graphdb.write_graph(STEM, nodes_with_evidence, edges_with_evidence)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert "Ada Lovelace worked on the Analytical Engine." in result["context"]
    assert "제1조" in result["context"]
    assert "worked on the Analytical Engine" in result["context"]


def test_search_graph_excludes_low_confidence_nodes_when_threshold_set(monkeypatch):
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "confidence": "HIGH"},
        {"id": "n2", "label": "Analytical Engine", "type": "Concept", "confidence": "LOW"},
    ]
    graphdb.write_graph(STEM, nodes, [])
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person", "Concept"],
                    "edge_types": [],
                    "keywords": {"Person": ["Ada Lovelace"], "Concept": ["Analytical Engine"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.graphrag.MIN_CONFIDENCE", "MEDIUM")

    result = search_graph("Tell me about Ada Lovelace and the Analytical Engine", SCHEMA, STEM, hops=0)

    assert "Ada Lovelace" in result["context"]
    assert "Analytical Engine" not in result["context"]


def test_search_graph_includes_nodes_with_no_confidence_regardless_of_threshold(monkeypatch):
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph(STEM, nodes, [])
    model = SequencedChatModel(
        [
            json.dumps(
                {"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Ada Lovelace"]}}
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.graphrag.MIN_CONFIDENCE", "HIGH")

    result = search_graph("Who is Ada Lovelace?", SCHEMA, STEM, hops=0)

    assert "Ada Lovelace" in result["context"]


def test_search_graph_scoped_to_active_version(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES, version=1)
    graphdb.write_graph(
        STEM,
        [{"id": "n9", "label": "Grace Hopper", "type": "Person"}],
        [],
        version=2,
    )
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": [],
                    "keywords": {"Person": ["Grace Hopper"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("Who is Grace Hopper?", SCHEMA, STEM, version=2, hops=0)

    assert {n["label"] for n in result["related_nodes"]} == {"Grace Hopper"}
