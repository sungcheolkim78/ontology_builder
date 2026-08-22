import json

from app.graphrag import extract_keywords, find_relevant_nodes, retrieve_graph_context

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


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


def test_find_relevant_nodes_matches_case_insensitively():
    matched = find_relevant_nodes(NODES, ["ada"])
    assert matched == ["n1"]


def test_find_relevant_nodes_returns_empty_when_no_match():
    matched = find_relevant_nodes(NODES, ["nonexistent"])
    assert matched == []


def test_retrieve_graph_context_includes_one_hop_neighbors():
    context = retrieve_graph_context(GRAPH, ["ada lovelace"], hops=1)

    assert context is not None
    assert "Ada Lovelace" in context
    assert "Analytical Engine" in context
    assert "Charles Babbage" not in context  # 2 hops away, not included at hops=1


def test_retrieve_graph_context_expands_further_with_more_hops():
    # n1(Ada) -1-> n2(Engine) -1-> n3(Babbage) -1-> n4(Royal Society): 3 hops from n1
    context = retrieve_graph_context(GRAPH, ["ada lovelace"], hops=3)

    assert context is not None
    assert "Charles Babbage" in context
    assert "Royal Society" in context


def test_retrieve_graph_context_returns_none_when_no_nodes_match():
    context = retrieve_graph_context(GRAPH, ["nonexistent keyword"], hops=1)

    assert context is None


def test_extract_keywords_parses_llm_response(monkeypatch):
    monkeypatch.setattr(
        "app.graphrag.get_chat_model",
        lambda: FakeChatModel(json.dumps(["Ada Lovelace", "Analytical Engine"])),
    )

    keywords = extract_keywords("What did Ada Lovelace work on?")

    assert keywords == ["Ada Lovelace", "Analytical Engine"]


def test_extract_keywords_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "app.graphrag.get_chat_model", lambda: FakeChatModel("not json")
    )

    try:
        extract_keywords("some question")
        assert False, "expected ValueError"
    except ValueError:
        pass
