import json

from app import graphdb
from app.graphrag import (
    determine_relevant_types,
    extract_keywords,
    format_type_preview,
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


def test_determine_relevant_types_parses_and_filters_hallucinated_types(monkeypatch):
    monkeypatch.setattr(
        "app.graphrag.get_chat_model",
        lambda: FakeChatModel(
            json.dumps({"node_types": ["Person", "NotARealType"], "edge_types": ["WORKED_ON"]})
        ),
    )

    result = determine_relevant_types("What did Ada Lovelace work on?", SCHEMA)

    assert result == {"node_types": ["Person"], "edge_types": ["WORKED_ON"]}


def test_determine_relevant_types_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: FakeChatModel("not json"))

    try:
        determine_relevant_types("some question", SCHEMA)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_format_type_preview_lists_types():
    text = format_type_preview(["Person"], ["WORKED_ON"])
    assert "Person" in text
    assert "WORKED_ON" in text


def test_format_type_preview_shows_none_when_empty():
    text = format_type_preview([], [])
    assert "없음" in text


def test_search_graph_finds_context_when_types_and_keywords_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["edge_types"] == ["WORKED_ON"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Analytical Engine" in result["context"]
    assert len(model.calls) == 2  # type analysis, then keyword extraction


def test_search_graph_skips_keyword_extraction_when_no_types_relevant(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel([json.dumps({"node_types": [], "edge_types": []})])
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("completely unrelated question", SCHEMA, STEM, hops=1)

    assert result == {"node_types": [], "edge_types": [], "context": None}
    assert len(model.calls) == 1


def test_search_graph_falls_back_to_all_instances_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": []}),
            json.dumps(["someone who does not exist in the graph"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("who are the people?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Charles Babbage" in result["context"]


def test_search_graph_falls_back_to_all_edges_of_type_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": [], "edge_types": ["MEMBER_OF"]}),
            json.dumps(["nonexistent keyword"]),
        ]
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
            json.dumps({"node_types": ["Organization"], "edge_types": []}),
            json.dumps(["nonexistent keyword"]),
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
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
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
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["context"] is not None
    assert "None" not in result["context"]
