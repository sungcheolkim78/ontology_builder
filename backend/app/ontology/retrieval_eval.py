"""Scores a document's golden set against its extracted graph: for each golden
question, does the search the chatbot uses (graphrag.search_graph) return nodes
and edges that cite the passage that answers it? See CONTEXT.md ("Golden set",
"Retrieval outcome")."""

import math

from app.graph import graphdb
from app.graph.graphrag import search_graph
from app.llm.calls import embed
from app.preprocess.embeddings import node_embedding_text
from app.preprocess.goldenset import load_goldenset
from app.utils.paths import raw_path_for

from .persistence import OntologyNotExtracted, SchemaNotFound, active_schema, load_schema

# A question counts as answered by the search when at least this share of its
# evidence passages are cited by what the search returned.
HIT_RECALL = 0.5

# The semantic rule (see _SemanticIndex): a node or edge whose text embeds this
# close (cosine) to a golden passage counts as citing it. 0.75 is where an
# earlier by-hand look at the misses (docs/ontology/golden_set_evaluation.md)
# found nodes that state the fact while quoting a neighbouring clause.
SEMANTIC_THRESHOLD = 0.75
_EMBED_BATCH = 256


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


def _passages(raw_text: str, evidence: list[dict]) -> list[tuple[tuple[int, int], str]]:
    """(span in `raw_text`, quote) of each answering passage that can be located."""
    passages = []
    for item in evidence:
        spans = evidence_spans(raw_text, [item])
        if spans:
            passages.append((spans[0], item["quote"]))
    return passages


def _unit(vector: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else None


def _embed_units(name: str, texts: list[str]) -> list[list[float] | None]:
    vectors = []
    for start in range(0, len(texts), _EMBED_BATCH):
        vectors.extend(embed(name, texts[start : start + _EMBED_BATCH]))
    return [_unit(vector) for vector in vectors]


def _item_key(item: dict) -> tuple:
    if "source" in item:
        return ("edge", item["source"], item["target"], item["type"])
    return ("node", item["id"])


class _SemanticIndex:
    """Embeddings of every node and edge of a graph and of the golden quotes, so
    that "does this item say what that passage says" is a cosine comparison.
    A node or edge often states a fact while quoting only the clause next to
    it, which the strict rule (the quoted spans overlap) counts as no citation
    at all; this is the second reading of the same search results, reported
    beside the strict one. Nothing is stored: the vectors live for one scoring
    run, so no extracted graph needs its nodes embedded first."""

    def __init__(self, graph: dict, quotes: list[str], threshold: float):
        self.threshold = threshold
        labels = {node["id"]: node["label"] for node in graph["nodes"]}
        items = [*graph["nodes"], *graph["edges"]]
        texts = [
            node_embedding_text(item) if "source" not in item else
            item.get("detail") or f"{labels.get(item['source'], '')} {item['type']} {labels.get(item['target'], '')}"
            for item in items
        ]
        self.items = items
        self.vectors = dict(zip((_item_key(item) for item in items), _embed_units("embed-eval-items", texts)))
        unique = sorted(set(quotes))
        self.quotes = dict(zip(unique, _embed_units("embed-eval-quotes", unique)))

    def says(self, item: dict, quote: str) -> bool:
        a, b = self.vectors.get(_item_key(item)), self.quotes.get(quote)
        if a is None or b is None:
            return False
        return sum(x * y for x, y in zip(a, b)) >= self.threshold


def _classify(passages, cited_by_returned, in_graph) -> dict:
    """Hit / retrieval miss / extraction miss for one question, given two
    predicates over its passages: was it cited by what the search returned, and
    does anything in the graph cite it."""
    missed = [passage for passage in passages if not cited_by_returned(passage)]
    recall = (len(passages) - len(missed)) / len(passages)
    if recall >= HIT_RECALL:
        outcome = "hit"
    elif any(in_graph(passage) for passage in missed):
        outcome = "retrieval_miss"  # the graph cites it somewhere; the search did not bring it back
    else:
        outcome = "extraction_miss"  # no node or edge cites it at all
    return {"outcome": outcome, "recall": recall}


def score_golden_set(
    stem: str, version: int | None = None, hops: int = 1, semantic_threshold: float | None = SEMANTIC_THRESHOLD
) -> dict:
    """Runs every answerable golden question of `stem` through search_graph
    against the graph extracted under `version` (default: the active one) and
    reports, per question, what share of its answering passages the returned
    nodes and edges cite. That is the strict reading (a node's quoted evidence
    overlaps the passage); unless `semantic_threshold` is None, each question
    and the summary also carry a "semantic" reading, where a node or edge whose
    text is within that cosine similarity of the passage counts as citing it
    too (see _SemanticIndex; costs a few embedding calls, no chat calls).
    Raises ValueError if the document has no golden set, SchemaNotFound if it
    has no such schema version."""
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
    all_items = [*graph["nodes"], *graph["edges"]]
    in_graph = _cited_spans(raw_text, all_items)

    passages_by_question = {
        question["id"]: _passages(raw_text, question["evidence"])
        for question in goldenset["questions"]
        if question.get("answerable")
    }
    index = None
    if semantic_threshold is not None:
        quotes = [quote for passages in passages_by_question.values() for _, quote in passages]
        index = _SemanticIndex(graph, quotes, semantic_threshold) if quotes else None

    def record(question_id, strict, semantic=None):
        entry = {"id": question_id, **strict}
        if semantic_threshold is not None:
            entry["semantic"] = semantic if semantic is not None else dict(strict)
        return entry

    questions = []
    repeated_passages = 0
    for question in goldenset["questions"]:
        if not question.get("answerable"):
            # nothing in the document answers it, so there is no passage to cite
            questions.append(record(question["id"], {"outcome": "unanswerable", "recall": None}))
            continue
        passages = passages_by_question[question["id"]]
        if not passages:
            # its evidence can no longer be located (the document changed since the
            # golden set was made): neither a hit nor a miss of the schema
            questions.append(record(question["id"], {"outcome": "unscorable", "recall": None}))
            continue
        repeated_passages += sum(
            1 for item in question["evidence"] if item.get("quote") and raw_text.count(item["quote"]) > 1
        )
        found = search_graph(question["question"], schema, stem, version=version, hops=hops)
        returned = [*found["related_nodes"], *found["related_edges"]]
        cited = _cited_spans(raw_text, returned)

        def strict_cited(passage):
            return any(spans_overlap(passage[0], c) for c in cited)

        def strict_in_graph(passage):
            return any(spans_overlap(passage[0], c) for c in in_graph)

        strict = _classify(passages, strict_cited, strict_in_graph)
        semantic = None
        if index is not None:
            semantic = _classify(
                passages,
                lambda p: strict_cited(p) or any(index.says(item, p[1]) for item in returned),
                lambda p: strict_in_graph(p) or any(index.says(item, p[1]) for item in all_items),
            )
        questions.append(record(question["id"], strict, semantic))
    summary = _summarize(goldenset["questions"], questions, graph, repeated_passages)
    if semantic_threshold is not None:
        semantic_summary = _summarize(
            goldenset["questions"], [q["semantic"] for q in questions], graph, repeated_passages
        )
        summary["semantic"] = {
            "threshold": semantic_threshold,
            **{key: semantic_summary[key] for key in ("outcomes", "scored", "hit_rate", "mean_recall", "by_question_type")},
        }
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
