"""Scores a document's golden set against its extracted graph: for each golden
question, does the search the chatbot uses (graphrag.search_graph) return nodes
and edges that cite the passage that answers it? See CONTEXT.md ("Golden set",
"Retrieval outcome")."""

from app.graph import graphdb
from app.graph.graphrag import search_graph
from app.preprocess.goldenset import load_goldenset
from app.utils.paths import raw_path_for

from .persistence import OntologyNotExtracted, SchemaNotFound, active_schema, load_schema

# A question counts as answered by the search when at least this share of its
# evidence passages are cited by what the search returned.
HIT_RECALL = 0.5


def evidence_spans(raw_text: str, evidence: list[dict]) -> list[tuple[int, int]]:
    """The (start, end) character span in `raw_text` of each golden evidence
    quote. A quote is looked for inside the line range the golden set recorded
    for it, so a sentence that appears twice resolves to the occurrence the
    question was actually about; if it is not there (or the range is wrong)
    the first occurrence anywhere is used, and a quote not in the document at
    all is skipped."""
    lines = raw_text.splitlines(keepends=True)
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    spans = []
    for item in evidence:
        quote = item.get("quote") or ""
        if not quote:
            continue
        first_line = min(max(int(item.get("line_start", 1)), 1), len(lines))
        last_line = min(max(int(item.get("line_end", first_line)), first_line), len(lines))
        start = raw_text.find(quote, line_starts[first_line - 1], line_starts[last_line])
        if start < 0:
            start = raw_text.find(quote)
        if start < 0:
            continue
        spans.append((start, start + len(quote)))
    return spans


def spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two character spans cite the same passage: at least half of the
    shorter one lies inside the other. A node that quotes a longer sentence
    around the golden quote, or a shorter piece of it, still counts; a node
    that merely touches or brushes it does not."""
    shared = min(a[1], b[1]) - max(a[0], b[0])
    if shared <= 0:
        return False
    shorter = min(a[1] - a[0], b[1] - b[0])
    return shared * 2 >= shorter


def _cited_spans(raw_text: str, items: list[dict]) -> list[tuple[int, int]]:
    """The span in `raw_text` of each node's or edge's `evidence_text`, found
    from the text itself. The stored start/end offsets are not used: a graph
    extracted before they were anchored to raw.md holds offsets into the
    labelled chunk-group text instead, so they cannot be trusted here."""
    spans = []
    for item in items:
        quote = item.get("evidence_text")
        if not quote:
            continue
        start = raw_text.find(quote)
        if start >= 0:
            spans.append((start, start + len(quote)))
    return spans


def score_golden_set(stem: str, version: int | None = None, hops: int = 1) -> dict:
    """Runs every answerable golden question of `stem` through search_graph
    against the graph extracted under `version` (default: the active one) and
    reports, per question, what share of its answering passages the returned
    nodes and edges cite. Raises ValueError if the document has no golden set,
    SchemaNotFound if it has no such schema version."""
    goldenset = load_goldenset(stem)
    if goldenset is None:
        raise ValueError(f"no golden set for {stem!r}")
    raw_text = raw_path_for(stem).read_text()
    if version is None:
        version, schema = active_schema(stem)
    else:
        schema = load_schema(stem, version)
        if schema is None:
            raise SchemaNotFound(stem)

    graph = graphdb.load_graph(stem, version=version)
    if graph is None:
        raise OntologyNotExtracted(stem)
    in_graph = _cited_spans(raw_text, [*graph["nodes"], *graph["edges"]])

    questions = []
    repeated_passages = 0
    for question in goldenset["questions"]:
        if not question.get("answerable"):
            # nothing in the document answers it, so there is no passage to cite
            questions.append({"id": question["id"], "outcome": "unanswerable", "recall": None})
            continue
        wanted = evidence_spans(raw_text, question["evidence"])
        if not wanted:
            # its evidence can no longer be located (the document changed since the
            # golden set was made): neither a hit nor a miss of the schema
            questions.append({"id": question["id"], "outcome": "unscorable", "recall": None})
            continue
        repeated_passages += sum(
            1 for item in question["evidence"] if item.get("quote") and raw_text.count(item["quote"]) > 1
        )
        found = search_graph(question["question"], schema, stem, version=version, hops=hops)
        cited = _cited_spans(raw_text, [*found["related_nodes"], *found["related_edges"]])
        missed = [span for span in wanted if not any(spans_overlap(span, c) for c in cited)]
        recall = (len(wanted) - len(missed)) / len(wanted)
        if recall >= HIT_RECALL:
            outcome = "hit"
        elif any(spans_overlap(span, c) for span in missed for c in in_graph):
            outcome = "retrieval_miss"  # the graph cites it somewhere; the search did not bring it back
        else:
            outcome = "extraction_miss"  # no node or edge cites it at all
        questions.append({"id": question["id"], "outcome": outcome, "recall": recall})
    summary = _summarize(goldenset["questions"], questions, graph, repeated_passages)
    return {"stem": stem, "version": version, "hops": hops, "questions": questions, "summary": summary}


def _summarize(golden: list[dict], scored_questions: list[dict], graph: dict, repeated_passages: int) -> dict:
    outcomes = {name: 0 for name in ("hit", "retrieval_miss", "extraction_miss", "unanswerable", "unscorable")}
    by_type: dict[str, dict] = {}
    recalls = []
    for question, result in zip(golden, scored_questions):
        outcomes[result["outcome"]] += 1
        if result["recall"] is None:
            continue
        recalls.append(result["recall"])
        entry = by_type.setdefault(question.get("question_type") or "unknown", {"scored": 0, "hits": 0})
        entry["scored"] += 1
        entry["hits"] += result["outcome"] == "hit"
    scored = len(recalls)
    return {
        "outcomes": outcomes,
        "scored": scored,
        "hit_rate": outcomes["hit"] / scored if scored else None,
        "mean_recall": sum(recalls) / scored if scored else None,
        "by_question_type": by_type,
        "repeated_passages": repeated_passages,
        "graph": {
            "nodes": len(graph["nodes"]),
            "nodes_with_evidence": sum(1 for n in graph["nodes"] if n.get("evidence_text")),
            "edges": len(graph["edges"]),
            "edges_with_evidence": sum(1 for e in graph["edges"] if e.get("evidence_text")),
        },
    }
