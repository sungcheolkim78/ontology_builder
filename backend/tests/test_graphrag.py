import json

from app.graphrag import (
    determine_relevant_types,
    extract_keywords,
    find_matching_edges,
    find_relevant_nodes,
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
GRAPH = {"nodes": NODES, "edges": EDGES}

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


def test_find_relevant_nodes_matches_case_insensitively():
    matched = find_relevant_nodes(NODES, ["ada"])
    assert matched == ["n1"]


def test_find_relevant_nodes_returns_empty_when_no_match():
    matched = find_relevant_nodes(NODES, ["nonexistent"])
    assert matched == []


def test_find_relevant_nodes_filters_by_allowed_types():
    # "ada" matches n1's label, but n1 is type Person, not Organization
    matched = find_relevant_nodes(NODES, ["ada"], allowed_types=["Organization"])
    assert matched == []


def test_find_relevant_nodes_empty_allowed_types_matches_nothing():
    matched = find_relevant_nodes(NODES, ["ada"], allowed_types=[])
    assert matched == []


def test_find_matching_edges_filters_by_type_and_connection():
    matched = find_matching_edges(EDGES, ["WORKED_ON"], {"n1"})
    assert matched == [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]


def test_find_matching_edges_excludes_unconnected_nodes():
    matched = find_matching_edges(EDGES, ["MEMBER_OF"], {"n1"})
    assert matched == []


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
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, GRAPH, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["edge_types"] == ["WORKED_ON"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Analytical Engine" in result["context"]
    assert len(model.calls) == 2  # type analysis, then keyword extraction


def test_search_graph_skips_keyword_extraction_when_no_types_relevant(monkeypatch):
    model = SequencedChatModel([json.dumps({"node_types": [], "edge_types": []})])
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("completely unrelated question", SCHEMA, GRAPH, hops=1)

    assert result == {"node_types": [], "edge_types": [], "context": None}
    assert len(model.calls) == 1  # only type analysis, no keyword extraction


def test_search_graph_returns_none_context_when_no_node_instance_matches(monkeypatch):
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": []}),
            json.dumps(["someone who does not exist in the graph"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("question about a stranger", SCHEMA, GRAPH, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["context"] is None
