"""Instance-extraction stage of the ontology pipeline: given a document and an
already-generated schema (see generate_schema.py), extract actual nodes/edges
conforming to it (extract_graph), plus the extract_for_document seam
main.py's /extract route calls, which runs it over chunk groups through
run_chunk_groups and merges the group graphs. See
evolve_graph.py for validating/evolving what this stage produces."""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.calls import call_json
from app.llm.prompts import EXTRACT_PROMPT

from .persistence import DEFAULT_SCHEMA, create_schema_version, get_active_version, load_schema, save_graph
from .schema_validation import normalize_schema
from .chunk_groups import run_chunk_groups
from .utils import (
    _dedupe_by_key,
    _require_document_text,
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
    normalized_schema = normalize_schema(schema)
    # The schema, not just the instructions, goes into the system message --
    # it's identical across every group of one extract_for_document
    # call (unlike the group's own document text), so folding it in here
    # keeps that whole message byte-identical across the call's groups (see
    # EXTRACT_PROMPT's own comment in prompts.py).
    system_prompt = EXTRACT_PROMPT.format(schema=json.dumps(normalized_schema))
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=f"Document:\n{document_text}")]
    graph = call_json("extract_graph", messages)

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


def _anchor_evidence_to_document(items: list[dict], document_text: str) -> None:
    """Recomputes each item's evidence offsets against `document_text` (the
    document's raw.md), in place. extract_graph() reports offsets relative to
    whatever text it was handed, which for a chunk group is the labelled,
    concatenated group text -- not something any other reader of a stored
    offset can use. A quote that is verbatim in the text the LLM saw but not
    in raw.md (chunking drops "---" and page-marker lines, so a quote can span
    one) keeps its evidence_text and loses only its offsets: no offset is
    better than one into a different text."""
    for item in items:
        evidence = item.get("evidence_text")
        if not evidence:
            continue
        span = _find_evidence_span(evidence, document_text)
        if span:
            item.update(span)
        else:
            item.pop("start_offset", None)
            item.pop("end_offset", None)


def extract_for_document(stem: str) -> tuple[dict, dict, int]:
    """One seam for main.py's /extract route: runs extract_graph over the
    document's chunk groups (or, with no chunks.json, over its whole text as
    one group) through run_chunk_groups, which owns the progress file (with
    running node/edge totals) and the resume cache either way, then merges
    the group graphs with _merge_group_graphs, and re-anchors every node's and
    edge's evidence offsets to raw.md (see _anchor_evidence_to_document).
    Also owns the no-active-version fallback (create a DEFAULT_SCHEMA version), and saves
    the graph for that version (without embeddings -- embedding is the
    separate embed_graph step). Returns (schema, graph, version). Raises
    FileNotFoundError if the document hasn't been parsed yet.

    A group's cached graph is reused on a retry only if the schema,
    EXTRACT_PROMPT and the group's text are all unchanged -- so activating a
    different schema version and re-extracting never picks up the previous
    schema's output. Unlike the discover/schema stages, merging never goes
    back through an LLM -- a document's node/edge count scales with its
    length, so exact (type, label) matching is used instead (see
    _merge_group_graphs)."""
    document_text = _require_document_text(stem)
    version = get_active_version(stem)
    if version is None:
        version = create_schema_version(stem, DEFAULT_SCHEMA, document_type="default")
    schema = load_schema(stem, version)
    graph = run_chunk_groups(
        stage="extract",
        operation="extract_graph",
        stem=stem,
        fingerprint_inputs={"schema": schema, "prompt": EXTRACT_PROMPT},
        group_fn=lambda group_text: extract_graph(group_text, schema),
        reduce_fn=_merge_group_graphs,
        reduce_stage="merge",
        summarize=lambda graph: {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
    )
    _anchor_evidence_to_document([*graph["nodes"], *graph["edges"]], document_text)
    save_graph(stem, graph, version=version)
    return schema, graph, version
