"""Instance-extraction stage of the ontology pipeline: given a document and an
already-generated schema (see generate_schema.py), extract actual nodes/edges
conforming to it (extract_graph), plus its chunk-grouped map-reduce variant
for documents too large to send in one LLM call (extract_graph_from_chunks)
and the extract_for_document seam main.py's /extract route calls. See
evolve_graph.py for validating/evolving what this stage produces."""

import json
import logging
import re
import shutil
from pathlib import Path

from app import ontology
from app.llm.prompts import EXTRACT_PROMPT
from app.llm.telemetry import invoke_with_telemetry
from app.utils.paths import document_dir_for

from .persistence import DEFAULT_SCHEMA, create_schema_version, get_active_version, load_schema
from .schema_validation import normalize_schema
from .utils import (
    _dedupe_by_key,
    _group_document_text,
    _load_chunk_items,
    _require_document_text,
    group_chunks_by_budget,
    parse_json_response,
    start_progress,
)

logger = logging.getLogger(__name__)

_CONFIDENCE_LEVELS = {"HIGH", "MEDIUM", "LOW"}

# Matches a bracketed section label on its own line, exactly the shape
# _group_document_text() prepends to each chunk (f"[{path}]\n{text}") --
# this is how EXTRACT_PROMPT's "source_section" instruction lets an LLM call
# that only ever sees one group's concatenated text still attribute a node/
# edge to a specific chunk *within* that group, not just the group as a
# whole: it copies the nearest label already printed in the text it read,
# and this regex is what lets the parser tell a genuine copy from a
# hallucinated one (see _normalize_extracted_item).
_SECTION_LABEL_RE = re.compile(r"^\[(?P<label>.+)\]$", re.MULTILINE)


def _section_labels_in(document_text: str) -> set[str]:
    return {m.group("label") for m in _SECTION_LABEL_RE.finditer(document_text)}


def _find_evidence_span(evidence: str | None, document_text: str) -> dict | None:
    """Verifies `evidence` appears verbatim in `document_text` -- an LLM can
    claim any string as supporting quote, so this is what actually enforces
    "evidence must be exact" rather than trusting the model's own claim (same
    verify-in-code precedent as goldenset.py's answer-evidence check)."""
    if not evidence:
        return None
    start = document_text.find(evidence)
    if start == -1:
        return None
    return {
        "evidence_text": evidence,
        "start_offset": start,
        "end_offset": start + len(evidence),
    }


def _normalize_extracted_properties(raw_properties, declared_properties: dict) -> dict:
    if not isinstance(raw_properties, dict) or not declared_properties:
        return {}
    declared_lower = {name.lower(): name for name in declared_properties}
    normalized = {}
    for key, value in raw_properties.items():
        canonical = declared_lower.get(str(key).lower())
        if canonical is None:
            # Not declared by the active schema for this exact type -- drop
            # rather than invent a property the schema doesn't govern.
            continue
        normalized[canonical] = "" if value is None else str(value)
    return normalized


def _properties_by_type(normalized_schema: dict) -> dict[str, dict]:
    by_type = {}
    for type_kind in ("node_types", "edge_types"):
        for entry in normalized_schema.get(type_kind, []):
            by_type[entry["name"]] = entry.get("properties", {})
    return by_type


def _normalize_extracted_item(
    item: dict, declared_properties: dict, document_text: str, section_labels: set[str]
) -> dict:
    """Adds properties/confidence/evidence*/source_section to `item` only
    when there's a genuine, verified value for them -- an item with none of
    these in the raw LLM output round-trips with exactly its old shape, so
    this is purely additive for documents/schemas that don't use the new
    fields."""
    normalized = dict(item)

    properties = _normalize_extracted_properties(
        item.get("properties"), declared_properties
    )
    if properties:
        normalized["properties"] = properties
    else:
        normalized.pop("properties", None)

    confidence = item.get("confidence")
    if confidence in _CONFIDENCE_LEVELS:
        normalized["confidence"] = confidence
    else:
        normalized.pop("confidence", None)

    evidence = item.get("evidence")
    span = _find_evidence_span(evidence, document_text) if isinstance(evidence, str) else None
    if span:
        normalized.update(span)
    else:
        normalized.pop("evidence", None)
        normalized.pop("evidence_text", None)
        normalized.pop("start_offset", None)
        normalized.pop("end_offset", None)

    source_section = item.get("source_section")
    if isinstance(source_section, str) and source_section in section_labels:
        normalized["source_section"] = source_section
    else:
        normalized.pop("source_section", None)

    return normalized


def extract_graph(document_text: str, schema: dict) -> dict:
    model = ontology.get_chat_model("extract_graph")
    normalized_schema = normalize_schema(schema)
    prompt = EXTRACT_PROMPT.format(
        schema=json.dumps(normalized_schema), document=document_text
    )
    response = invoke_with_telemetry("extract-graph", model, prompt)
    graph = parse_json_response(response.content)
    if not isinstance(graph.get("nodes"), list) or not isinstance(
        graph.get("edges"), list
    ):
        raise ValueError("extraction JSON missing nodes/edges lists")

    properties_by_type = _properties_by_type(normalized_schema)
    section_labels = _section_labels_in(document_text)
    graph["nodes"] = [
        _normalize_extracted_item(
            node, properties_by_type.get(node.get("type"), {}), document_text, section_labels
        )
        for node in graph["nodes"]
    ]

    # The LLM occasionally hallucinates an edge endpoint that isn't among
    # its own extracted nodes (e.g. an implied node it never fully emitted,
    # or output truncated mid-list). graphdb.write_graph would reject the
    # whole extraction over one bad edge, so drop just the offending edges
    # here and keep the rest of the graph.
    node_ids = {n["id"] for n in graph["nodes"]}
    valid_edges = []
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            logger.warning(
                "dropping edge with unknown endpoint: %r -> %r (type=%r)",
                edge.get("source"), edge.get("target"), edge.get("type"),
            )
            continue
        valid_edges.append(
            _normalize_extracted_item(
                edge, properties_by_type.get(edge.get("type"), {}), document_text, section_labels
            )
        )
    graph["edges"] = valid_edges

    return graph


def _merge_group_graphs(group_graphs: list[dict]) -> dict:
    """Merges independently-extracted per-group graphs into one, resolving
    coreference *across* group boundaries by exact (type, label) match --
    the same entity recurring in a later article is expected to reuse the
    document's own term for it verbatim (see EXTRACT_PROMPT's "canonical
    surface form" instruction), so this catches the common case cheaply
    without a second LLM pass. A node id is only ever unique within the
    group that produced it (extract_graph's own contract), so every id is
    first namespaced by its group index before being deduped down to one
    canonical id per (type, label); edges are then rewritten to point at
    those canonical ids."""
    canonical_id_by_key: dict[tuple[str, str], str] = {}
    id_map: dict[tuple[int, str], str] = {}
    merged_nodes = []
    for group_index, graph in enumerate(group_graphs):
        for node in graph["nodes"]:
            key = (node["type"], node["label"])
            canonical_id = canonical_id_by_key.get(key)
            if canonical_id is None:
                canonical_id = f"g{group_index}::{node['id']}"
                canonical_id_by_key[key] = canonical_id
                merged_nodes.append({**node, "id": canonical_id})
            id_map[(group_index, node["id"])] = canonical_id

    merged_edges = []
    for group_index, graph in enumerate(group_graphs):
        for edge in graph["edges"]:
            source = id_map.get((group_index, edge["source"]))
            target = id_map.get((group_index, edge["target"]))
            if source is None or target is None:
                continue
            merged_edges.append({**edge, "source": source, "target": target})
    merged_edges = _dedupe_by_key(merged_edges, key=lambda e: (e["source"], e["target"], e["type"]))

    return {"nodes": merged_nodes, "edges": merged_edges}


def _extraction_progress_dir(stem: str) -> Path:
    return document_dir_for(stem) / "extraction_progress"


def _clear_extraction_progress(stem: str) -> None:
    progress_dir = _extraction_progress_dir(stem)
    if progress_dir.is_dir():
        shutil.rmtree(progress_dir)


def _write_extraction_progress(stem: str, group_number: int, graph: dict) -> None:
    progress_dir = _extraction_progress_dir(stem)
    progress_dir.mkdir(parents=True, exist_ok=True)
    (progress_dir / f"node_proc_{group_number}.json").write_text(
        json.dumps(graph["nodes"], ensure_ascii=False)
    )
    (progress_dir / f"edge_proc_{group_number}.json").write_text(
        json.dumps(graph["edges"], ensure_ascii=False)
    )


def extract_graph_from_chunks(
    chunk_items: list[dict], schema: dict, max_group_chars: int | None = None, stem: str | None = None
) -> dict:
    """Runs extract_graph() once per token-budget-sized group of consecutive
    chunks (see group_chunks_by_budget), then merges every group's nodes/
    edges into one graph via _merge_group_graphs. Unlike
    discover_ontology_from_chunks/generate_schema_from_chunks
    (generate_schema.py), this never sends extracted instances back through
    an LLM to merge -- a document's node/edge count scales with its length,
    unlike a schema's small, fixed-size type list, so an LLM consolidation
    pass here wouldn't fit the same budget it does for types; exact (type,
    label) matching is used instead. A document small enough to fit in one
    group skips namespacing/merging entirely and returns that single
    group's graph untouched, so the common case still costs exactly one
    LLM call.

    A large legal/insurance document can take 1000s of seconds across many
    groups, all inside one synchronous HTTP request with no other visibility
    into how far it's gotten. When `stem` is given (the normal case --
    main.py's extract endpoint always has it), this logs which group out of
    the total is being processed, and -- since each group's own LLM call is
    the slow part, not the merge -- writes that group's own raw nodes/edges
    to documents/{stem}/extraction_progress/{node,edge}_proc_{N}.json as
    soon as it completes, so a person can inspect progress mid-run instead
    of only after the whole extraction (and its own DB write) finishes.
    Cleared at the start of each run so a shorter rerun doesn't leave stale
    higher-numbered files implying more progress than actually happened.

    Separately, `stem` also drives a lightweight summary written via
    .utils.start_progress to documents/{stem}/progress/extract.json --
    group count plus a running node/edge total -- for main.py's GET
    /progress route to poll, same mechanism as
    discover_ontology_from_chunks/generate_schema_from_chunks
    (generate_schema.py). That file is the summary a browser polls; the
    extraction_progress/ dump above stays the detailed, un-merged per-group
    data for manual inspection."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to extract graph from")

    if stem is not None:
        _clear_extraction_progress(stem)

    total = len(groups)
    total_nodes = 0
    total_edges = 0
    with start_progress(stem, "extract", total) as progress:
        group_graphs = []
        for group_number, group in enumerate(groups, start=1):
            logger.info(
                "extract_graph_from_chunks: processing group %d/%d (%d chunks, %d chars)",
                group_number, total, len(group), len(_group_document_text(group)),
            )
            graph = extract_graph(_group_document_text(group), schema)
            group_graphs.append(graph)
            if stem is not None:
                _write_extraction_progress(stem, group_number, graph)
            total_nodes += len(graph["nodes"])
            total_edges += len(graph["edges"])
            progress.advance(nodes=total_nodes, edges=total_edges)

        if len(group_graphs) == 1:
            return group_graphs[0]

        progress.set_stage("merge")
        return _merge_group_graphs(group_graphs)


def extract_for_document(stem: str) -> tuple[dict, dict, int]:
    """One seam for main.py's /extract route: owns the document-existence
    check, the chunks.json-vs-whole-document routing, and the
    no-active-version fallback (create a DEFAULT_SCHEMA version) that route
    used to do inline. Returns (schema, graph, version); the caller is still
    responsible for persisting the graph (save_graph), since that's a
    separate concern (embeddings) from producing it. Raises
    FileNotFoundError if the document hasn't been parsed yet. Also reports
    progress for the whole-document (no chunks.json) branch itself --
    extract_graph_from_chunks reports its own when there are chunks -- so a
    poller always finds a progress record no matter which path this
    document takes."""
    document_text = _require_document_text(stem)
    version = get_active_version(stem)
    if version is None:
        version = create_schema_version(stem, DEFAULT_SCHEMA, document_type="default")
    schema = load_schema(stem, version)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        graph = extract_graph_from_chunks(chunk_items, schema, stem=stem)
    else:
        with start_progress(stem, "extract", 1) as progress:
            graph = extract_graph(document_text, schema)
            progress.advance(nodes=len(graph["nodes"]), edges=len(graph["edges"]))
    return schema, graph, version
