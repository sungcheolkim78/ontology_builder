"""Re-extracts only the chunk groups that hold a golden passage the extracted
graph missed, under the schema that graph was extracted with, and reports
whether the second extraction cites the passage. See CONTEXT.md ("Retrieval
outcome", "Chunk group").

A second extraction of a group sees exactly the text and schema the first one
did, so a passage it recovers was one a sparse or unlucky run left out, and
the schema could hold it; a passage it still misses is one this schema and
prompt do not get out of the text. That tells whether schema refinement can
move the extraction misses at all, before any candidate schema is built."""

from app.preprocess.goldenset import load_goldenset
from app.utils.paths import raw_path_for

from .chunk_groups import _group_document_text, group_chunks_by_budget, map_concurrently
from .extract_graph import extract_graph
from .persistence import SchemaNotFound, load_schema
from .retrieval_eval import SEMANTIC_THRESHOLD, _cited_spans, _SemanticIndex, evidence_spans, spans_overlap
from .utils import _load_chunk_items


def probe_extraction_misses(
    stem: str, report: dict, granularity: str = "group", semantic_threshold: float | None = SEMANTIC_THRESHOLD
) -> dict:
    """For every `extraction_miss` in `report` (a score_golden_set report),
    extracts again each chunk group whose text quotes one of the question's
    answering passages -- once per group however many questions point at it --
    and classes the question: `recovered` (a node or edge of the new extraction
    cites a passage that answers it), `still_missed`, or `not_locatable` (no
    chunk group quotes its passages, e.g. chunking dropped the line). Raises
    SchemaNotFound if the report's schema version is gone.

    `granularity` is how much text is extracted at once: "group" the chunk group
    (what the real extraction did), "chunk" only the chunk(s) quoting the passage.
    A passage recovered from a chunk but not from its group says extraction
    loses facts when given a lot of text at once, not that the schema cannot
    hold them. Besides `recovered` (a new node or edge quotes the passage, the
    strict reading), each question says whether any new node or edge is within
    `semantic_threshold` cosine of the passage (see retrieval_eval._SemanticIndex);
    None skips that reading."""
    if granularity not in ("group", "chunk"):
        raise ValueError(f"granularity must be 'group' or 'chunk', not {granularity!r}")
    version = report["version"]
    schema = load_schema(stem, version)
    if schema is None:
        raise SchemaNotFound(stem)

    goldenset = load_goldenset(stem)
    if goldenset is None:
        raise ValueError(f"no golden set for {stem!r}")
    questions_by_id = {question["id"]: question for question in goldenset["questions"]}
    raw_text = raw_path_for(stem).read_text()

    chunk_items = _load_chunk_items(stem) or [{"text": raw_text}]
    if granularity == "group":
        group_texts = [_group_document_text(group) for group in group_chunks_by_budget(chunk_items)]
    else:
        group_texts = [_group_document_text([item]) for item in chunk_items]

    missed = [q["id"] for q in report["questions"] if q["outcome"] == "extraction_miss"]
    groups_by_question = {
        qid: [
            index
            for index, text in enumerate(group_texts)
            if any(item.get("quote") and item["quote"] in text for item in questions_by_id[qid]["evidence"])
        ]
        for qid in missed
    }

    wanted_groups = sorted({index for indices in groups_by_question.values() for index in indices})
    extracted = {}
    if wanted_groups:  # map_concurrently needs at least one item
        graphs = map_concurrently(lambda index: extract_graph(group_texts[index], schema), wanted_groups)
        extracted = dict(zip(wanted_groups, graphs))

    index = None
    if semantic_threshold is not None and extracted:
        # ids are only unique within the unit that produced them, so prefix each by its unit
        combined = {"nodes": [], "edges": []}
        for unit, graph in extracted.items():
            combined["nodes"] += [{**n, "id": f"{unit}::{n['id']}"} for n in graph["nodes"]]
            combined["edges"] += [
                {**e, "source": f"{unit}::{e['source']}", "target": f"{unit}::{e['target']}"} for e in graph["edges"]
            ]
        quotes = [item["quote"] for qid in missed for item in questions_by_id[qid]["evidence"] if item.get("quote")]
        index = _SemanticIndex(combined, quotes, semantic_threshold)

    results = []
    for qid in missed:
        indices = groups_by_question[qid]
        if not indices:
            results.append({"id": qid, "outcome": "not_locatable", "semantic": False, "groups": []})
            continue
        wanted = evidence_spans(raw_text, questions_by_id[qid]["evidence"])
        cited = [
            span
            for index in indices
            for span in _cited_spans(raw_text, [*extracted[index]["nodes"], *extracted[index]["edges"]])
        ]
        recovered = any(spans_overlap(w, c) for w in wanted for c in cited)
        said = False
        if index is not None:
            said = any(
                index.says(
                    {**item, "id": f"{unit}::{item['id']}"} if "source" not in item else
                    {**item, "source": f"{unit}::{item['source']}", "target": f"{unit}::{item['target']}"},
                    evidence["quote"],
                )
                for unit in indices
                for item in [*extracted[unit]["nodes"], *extracted[unit]["edges"]]
                for evidence in questions_by_id[qid]["evidence"]
                if evidence.get("quote")
            )
        results.append(
            {"id": qid, "outcome": "recovered" if recovered else "still_missed", "semantic": said, "groups": indices}
        )

    counts = {name: sum(1 for r in results if r["outcome"] == name) for name in ("recovered", "still_missed", "not_locatable")}
    return {
        "stem": stem,
        "version": version,
        "granularity": granularity,
        "questions": results,
        "summary": {
            "probed": len(results),
            **counts,
            "semantic_recovered": sum(1 for r in results if r["semantic"]),
            "llm_calls": len(wanted_groups),
        },
    }
