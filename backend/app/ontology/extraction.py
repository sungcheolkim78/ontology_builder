import json
import logging
import math
import os
import re
import shutil
import statistics
from collections import Counter
from pathlib import Path

from app import graphdb
from app import ontology
from app.paths import document_dir_for
from app.prompts import (
    CONSOLIDATION_PROMPT,
    DISCOVERY_PROMPT,
    EVOLUTION_PROMPT,
    EXTRACT_PROMPT,
    SCHEMA_CONSOLIDATION_PROMPT,
    SCHEMA_PROMPTS,
    SUMMARY_PROMPT,
    VALIDATION_PROMPT,
)
from app.schema_validation import normalize_schema
from app.telemetry import embed_with_telemetry, invoke_with_telemetry

from .persistence import (
    DEFAULT_SCHEMA,
    _apply_schema_type_changes,
    _apply_type_change,
    create_schema_version,
    get_active_version,
    list_versions,
    load_schema,
)

logger = logging.getLogger(__name__)

# ~4 chars/token is a conservative rule of thumb. 200_000 was originally sized
# for the default model (gpt-4o-mini, 128k-token context); real OPENROUTER_MODEL
# choices in practice (e.g. Gemini models) commonly have ~1M-token context, and
# real legal/insurance documents routinely exceed 200k chars, so the limit is
# raised 1.5x rather than tuned per-model. Configurable since OPENROUTER_MODEL
# can point at a model with a different context window. Guards against silently
# blowing the context window or getting back truncated/malformed JSON (e.g. an
# edge referencing a node that got cut off mid-response) instead of a clear,
# immediate error.
MAX_DOCUMENT_CHARS = int(os.environ.get("MAX_DOCUMENT_CHARS", 1_000_000))


def _check_document_length(document_text: str, max_chars: int | None = None) -> None:
    limit = max_chars if max_chars is not None else MAX_DOCUMENT_CHARS
    if len(document_text) > limit:
        raise ValueError(
            f"document is too long ({len(document_text)} chars, "
            f"max {limit}) to send to the LLM in one call"
        )


def parse_json_response(text: str) -> dict:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}")


def summarize_document(document_text: str, max_chars: int | None = None) -> str:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model()
    response = invoke_with_telemetry(
        "summarize-document", model, SUMMARY_PROMPT.format(document=document_text)
    )
    summary = response.content.strip()
    if not summary:
        raise ValueError("summary generation returned empty content")
    return summary


def discover_ontology(document_text: str, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model("discover_ontology")
    response = invoke_with_telemetry(
        "discover-ontology", model, DISCOVERY_PROMPT.format(document=document_text)
    )
    report = parse_json_response(response.content)
    if not isinstance(report.get("classes"), list):
        raise ValueError("discovery JSON missing classes list")
    return report


# Chunk-grouped ontology discovery/schema generation -------------------------
#
# discover_ontology() and generate_schema() below each send the whole document
# in one LLM call and are bounded by MAX_DOCUMENT_CHARS -- documents chunked
# into article-level JSON chunks (app.chunking.chunk_markdown_file) routinely
# exceed that in total even though no single chunk does. Rather than keeping
# every group's view of the ontology consistent with every other group's as it
# goes (which would make each group depend on every earlier one and prevent
# groups from being processed independently), discover_ontology_from_chunks
# and generate_schema_from_chunks below both run their single-document
# function once per token-budget-sized group of consecutive chunks (map), then
# fold every group's result into one unified set via a single consolidation
# LLM call (reduce) at the end -- see each function's own docstring for what
# exactly gets consolidated vs. merged in code.
MAX_CHUNK_GROUP_CHARS = int(os.environ.get("MAX_CHUNK_GROUP_CHARS", 60_000))


def group_chunks_by_budget(
    chunk_items: list[dict], max_group_chars: int | None = None
) -> list[list[dict]]:
    """Packs `chunk_items` (each needs a "text" key; order is preserved) into
    consecutive-run groups whose total text length stays under
    `max_group_chars` where possible. A single chunk longer than the budget
    on its own still becomes its own group rather than being split mid-chunk
    -- article-level chunks are the smallest unit this module reasons
    about."""
    limit = max_group_chars if max_group_chars is not None else MAX_CHUNK_GROUP_CHARS
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0
    for item in chunk_items:
        text_len = len(item.get("text") or "")
        if current and current_len + text_len > limit:
            groups.append(current)
            current, current_len = [], 0
        current.append(item)
        current_len += text_len
    if current:
        groups.append(current)
    return groups


def _group_document_text(chunk_items: list[dict]) -> str:
    parts = []
    for item in chunk_items:
        path = item.get("path")
        text = item.get("text") or ""
        parts.append(f"[{path}]\n{text}" if path else text)
    return "\n\n".join(parts)


def _consolidate_types(group_reports: list[dict]) -> dict:
    payload = [
        {
            "group": i,
            "classes": [
                {k: c.get(k) for k in ("name", "definition", "category")}
                for c in report.get("classes", [])
            ],
            "relationships": [
                {k: r.get(k) for k in ("name", "definition", "source", "target", "category")}
                for r in report.get("relationships", [])
            ],
        }
        for i, report in enumerate(group_reports)
    ]
    model = ontology.get_chat_model("discover_ontology")
    prompt = CONSOLIDATION_PROMPT.format(groups=json.dumps(payload, ensure_ascii=False))
    response = invoke_with_telemetry("consolidate-discovery-types", model, prompt)
    consolidated = parse_json_response(response.content)
    if not isinstance(consolidated.get("classes"), list) or not isinstance(
        consolidated.get("relationships"), list
    ):
        raise ValueError("consolidation JSON missing classes/relationships lists")
    return consolidated


def _dedupe_by_key(items: list, key) -> list:
    seen = set()
    deduped = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(item)
    return deduped


def _merge_domain_models(domain_models: list[dict]) -> dict:
    domain = next((d.get("domain") for d in domain_models if d.get("domain")), "")
    merged = {"domain": domain}
    for field in ("subdomains", "document_types", "business_processes", "major_actors"):
        merged[field] = _dedupe_by_key(
            [v for d in domain_models for v in d.get(field, [])], key=lambda v: v
        )
    return merged


def discover_ontology_from_chunks(
    chunk_items: list[dict], max_group_chars: int | None = None
) -> dict:
    """Runs discover_ontology() once per token-budget-sized group of
    consecutive chunks (see group_chunks_by_budget), then consolidates
    every group's classes/relationships into one unified set via
    _consolidate_types. Exists for documents whose full text would exceed
    discover_ontology's own MAX_DOCUMENT_CHARS in a single call; a document
    small enough to fit in one group skips consolidation entirely and
    returns that single group's report untouched, so the common case pays
    for exactly one LLM call, same as discover_ontology()."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to discover ontology from")

    group_reports = [discover_ontology(_group_document_text(group)) for group in groups]
    if len(group_reports) == 1:
        return group_reports[0]

    consolidated_types = _consolidate_types(group_reports)
    return {
        "domain_model": _merge_domain_models([r.get("domain_model", {}) for r in group_reports]),
        "classes": consolidated_types["classes"],
        "relationships": consolidated_types["relationships"],
        "attributes": _dedupe_by_key(
            [a for r in group_reports for a in r.get("attributes", [])],
            key=lambda a: (a.get("name"), a.get("defined_on")),
        ),
        "events": _dedupe_by_key(
            [e for r in group_reports for e in r.get("events", [])], key=lambda e: e.get("name")
        ),
        "rules": _dedupe_by_key(
            [ru for r in group_reports for ru in r.get("rules", [])], key=lambda ru: ru.get("name")
        ),
        "terminology": _dedupe_by_key(
            [t for r in group_reports for t in r.get("terminology", [])],
            key=lambda t: t.get("canonical_term"),
        ),
        "competency_questions": _dedupe_by_key(
            [q for r in group_reports for q in r.get("competency_questions", [])], key=lambda q: q
        ),
        "warnings": _dedupe_by_key(
            [w for r in group_reports for w in r.get("warnings", [])], key=lambda w: w
        ),
    }


def generate_schema(
    document_text: str,
    document_type: str = "general",
    max_chars: int | None = None,
    discovery: dict | None = None,
) -> dict:
    _check_document_length(document_text, max_chars)
    prompt_template = SCHEMA_PROMPTS.get(document_type)
    if prompt_template is None:
        raise ValueError(f"unknown document_type: {document_type!r}")
    model = ontology.get_chat_model("generate_schema")
    prompt = prompt_template.format(document=document_text)
    if discovery:
        # Prepended, not merged into the template's own "Document:" section --
        # keeps SCHEMA_PROMPT/LEGAL_SCHEMA_PROMPT completely unchanged when
        # discovery is None (the default), which is the entire point: this is
        # an optional hint layered on top of the existing prompt, not a
        # replacement for it.
        prompt = (
            "Reference -- a prior ontology-discovery pass over this document already "
            "proposed these candidate classes/relationships/terminology. Use them only "
            "as a starting hint; the schema you propose must still be independently "
            "grounded in the document text below, and you may diverge from this "
            "reference where the document doesn't actually support it.\n"
            f"{json.dumps(discovery)}\n\n"
        ) + prompt
    response = invoke_with_telemetry("generate-schema", model, prompt)
    schema = parse_json_response(response.content)
    if not isinstance(schema.get("node_types"), list) or not isinstance(
        schema.get("edge_types"), list
    ):
        raise ValueError("schema JSON missing node_types/edge_types lists")
    return schema


def _consolidate_schema_types(group_schemas: list[dict]) -> dict:
    payload = [
        {
            "group": i,
            "node_types": schema.get("node_types", []),
            "edge_types": schema.get("edge_types", []),
        }
        for i, schema in enumerate(group_schemas)
    ]
    model = ontology.get_chat_model("generate_schema")
    prompt = SCHEMA_CONSOLIDATION_PROMPT.format(groups=json.dumps(payload, ensure_ascii=False))
    response = invoke_with_telemetry("consolidate-schema-types", model, prompt)
    consolidated = parse_json_response(response.content)
    if not isinstance(consolidated.get("node_types"), list) or not isinstance(
        consolidated.get("edge_types"), list
    ):
        raise ValueError("schema consolidation JSON missing node_types/edge_types lists")
    return consolidated


def generate_schema_from_chunks(
    chunk_items: list[dict],
    document_type: str = "general",
    max_group_chars: int | None = None,
    discovery: dict | None = None,
) -> dict:
    """Runs generate_schema() once per token-budget-sized group of
    consecutive chunks (see group_chunks_by_budget), then consolidates every
    group's node_types/edge_types into one unified schema via
    _consolidate_schema_types. Same shape as discover_ontology_from_chunks:
    a document small enough to fit in one group skips consolidation
    entirely and returns that single group's schema untouched, so the
    common case still costs exactly one LLM call. `discovery`, if given, is
    passed through to every group's generate_schema() call unchanged (it's
    already a document-level hint, not something that needs re-deriving per
    group)."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to generate schema from")

    group_schemas = [
        generate_schema(_group_document_text(group), document_type=document_type, discovery=discovery)
        for group in groups
    ]
    if len(group_schemas) == 1:
        return group_schemas[0]

    return _consolidate_schema_types(group_schemas)


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
    discover_ontology_from_chunks/generate_schema_from_chunks, this never
    sends extracted instances back through an LLM to merge -- a document's
    node/edge count scales with its length, unlike a schema's small,
    fixed-size type list, so an LLM consolidation pass here wouldn't fit the
    same budget it does for types; exact (type, label) matching is used
    instead. A document small enough to fit in one group skips
    namespacing/merging entirely and returns that single group's graph
    untouched, so the common case still costs exactly one LLM call.

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
    higher-numbered files implying more progress than actually happened."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to extract graph from")

    if stem is not None:
        _clear_extraction_progress(stem)

    total = len(groups)
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

    if len(group_graphs) == 1:
        return group_graphs[0]

    return _merge_group_graphs(group_graphs)


def _load_chunk_items(stem: str) -> list[dict] | None:
    chunk_path = document_dir_for(stem) / "chunks.json"
    if not chunk_path.is_file():
        return None
    chunked = json.loads(chunk_path.read_text())
    return [chunked["preamble"], *chunked["chunks"]]


def _require_document_text(stem: str) -> str:
    doc_path = document_dir_for(stem) / "raw.md"
    if not doc_path.is_file():
        raise FileNotFoundError(f"document not found: {stem}")
    return doc_path.read_text()


def discover_for_document(stem: str, max_chars: int | None = None) -> dict:
    """One seam for main.py's /discover route: owns the document-existence
    check and the chunks.json-vs-whole-document routing that route used to
    duplicate inline (see discover_ontology_from_chunks/discover_ontology).
    Raises FileNotFoundError if the document hasn't been parsed yet."""
    document_text = _require_document_text(stem)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        return discover_ontology_from_chunks(chunk_items, max_group_chars=max_chars)
    return discover_ontology(document_text, max_chars=max_chars)


def schema_for_document(
    stem: str,
    document_type: str = "general",
    max_chars: int | None = None,
    discovery: dict | None = None,
) -> dict:
    """One seam for main.py's /schema route: same shape as
    discover_for_document, for generate_schema/generate_schema_from_chunks.
    Raises FileNotFoundError if the document hasn't been parsed yet."""
    document_text = _require_document_text(stem)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        return generate_schema_from_chunks(
            chunk_items, document_type=document_type, max_group_chars=max_chars, discovery=discovery
        )
    return generate_schema(document_text, document_type=document_type, max_chars=max_chars, discovery=discovery)


def extract_for_document(stem: str) -> tuple[dict, dict, int]:
    """One seam for main.py's /extract route: owns the document-existence
    check, the chunks.json-vs-whole-document routing, and the
    no-active-version fallback (create a DEFAULT_SCHEMA version) that route
    used to do inline. Returns (schema, graph, version); the caller is still
    responsible for persisting the graph (save_graph), since that's a
    separate concern (embeddings) from producing it. Raises
    FileNotFoundError if the document hasn't been parsed yet."""
    document_text = _require_document_text(stem)
    version = get_active_version(stem)
    if version is None:
        version = create_schema_version(stem, DEFAULT_SCHEMA, document_type="default")
    schema = load_schema(stem, version)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        graph = extract_graph_from_chunks(chunk_items, schema, stem=stem)
    else:
        graph = extract_graph(document_text, schema)
    return schema, graph, version


def validate_ontology(document_text: str, schema: dict, graph: dict, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model("validate_ontology")
    prompt = VALIDATION_PROMPT.format(
        schema=json.dumps(schema), graph=json.dumps(graph), document=document_text
    )
    response = invoke_with_telemetry("validate-ontology", model, prompt)
    report = parse_json_response(response.content)
    if not isinstance(report.get("validation_summary"), dict) or not isinstance(
        report.get("issues"), list
    ):
        raise ValueError("validation JSON missing validation_summary/issues")
    return report


def propose_evolution(
    document_text: str,
    schema: dict,
    graph: dict,
    validation_report: dict,
    max_chars: int | None = None,
) -> dict:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model()
    prompt = EVOLUTION_PROMPT.format(
        schema=json.dumps(schema),
        graph=json.dumps(graph),
        validation_report=json.dumps(validation_report),
        document=document_text,
    )
    response = invoke_with_telemetry("propose-evolution", model, prompt)
    proposal = parse_json_response(response.content)
    if not isinstance(proposal.get("changes"), list):
        raise ValueError("evolution JSON missing changes list")
    for change in proposal["changes"]:
        if not {"decision", "element_type", "element"} <= change.keys():
            raise ValueError("evolution change missing decision/element_type/element")
    return proposal


def apply_evolution(stem: str, changes: list) -> dict:
    """Applies an already-human-reviewed subset of a propose_evolution() proposal
    (the caller is expected to have filtered `changes` down to only what a
    person accepted -- this function has no opinion on `decision` beyond how
    to mutate schema/graph, per docs/ontology/ontology_evolution_prompt.md's
    rule that evolution must never be fully automatic). Writes the result as
    a NEW schema version (preserving every prior version untouched, per that
    spec's "preserve backward compatibility" principle) rather than mutating
    the active one in place."""
    version = get_active_version(stem)
    if version is None:
        raise ValueError(f"no active schema version for {stem!r}")
    schema = load_schema(stem, version)
    graph = graphdb.load_graph(stem, version=version) or {"nodes": [], "edges": []}
    document_type = next(
        (v["document_type"] for v in list_versions(stem) if v["version"] == version),
        "general",
    )

    new_node_types = list(schema["node_types"])
    new_edge_types = list(schema["edge_types"])
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    edges = list(graph["edges"])

    for change in changes:
        element_type = change["element_type"]
        decision = change["decision"]
        element = change["element"]
        if element_type == "node_type":
            _apply_type_change(new_node_types, element, decision)
        elif element_type == "edge_type":
            _apply_type_change(new_edge_types, element, decision)
        elif element_type == "node":
            if decision == "DEPRECATE":
                nodes_by_id.pop(element["id"], None)
            else:
                nodes_by_id[element["id"]] = element
        elif element_type == "edge":
            if decision == "DEPRECATE":
                edges = [
                    e
                    for e in edges
                    if not (
                        e["source"] == element["source"]
                        and e["target"] == element["target"]
                        and e["type"] == element["type"]
                    )
                ]
            else:
                edges.append(element)

    new_schema = {"node_types": new_node_types, "edge_types": new_edge_types}
    new_version = create_schema_version(stem, new_schema, document_type=document_type)
    new_nodes = list(nodes_by_id.values())
    graphdb.write_graph(stem, new_nodes, edges, version=new_version)
    return {
        "version": new_version,
        "schema": new_schema,
        "node_count": len(new_nodes),
        "edge_count": len(edges),
    }


# Domain schema convergence -------------------------------------------------
#
# generate_schema/extract_graph/validate_ontology/propose_evolution above all
# operate on a single document. Domain schema convergence reuses that same
# extract -> validate -> propose_evolution pipeline across an ordered
# *sequence* of documents from one domain (e.g. a set of insurance policy
# documents) so a single schema can be found that fits all of them, instead
# of generating an independent schema per document or forcing every document
# in the system onto one global schema. See
# docs/ontology/domain_schema_convergence.md for the design rationale.
#
# Only node_type/edge_type changes are folded into the evolving domain
# schema -- node/edge (instance-level) changes propose_evolution returns for
# a given document stay scoped to that document's own graph, since instances
# aren't shared across documents the way schema types are.
#
# Per docs/ontology/ontology_evolution_prompt.md's governance rule that
# ontology evolution must never be fully automatic, this only auto-applies
# decisions the evolution prompt itself judged non-material
# (ADD/MODIFY/MERGE/DEPRECATE); NEEDS_HUMAN_REVIEW changes are collected into
# `pending_review` instead of being applied, so the schema still keeps
# evolving across the rest of the calibration set without silently accepting
# a decision that needed a person.
_AUTO_APPLICABLE_DECISIONS = {"ADD", "MODIFY", "MERGE", "DEPRECATE"}


def converge_domain_schema(
    documents: list[dict],
    seed_schema: dict,
    max_chars: int | None = None,
) -> dict:
    """Evolves `seed_schema` across `documents` (each {"stem", "text"}, in the
    order they should be folded in) by running extract_graph/validate_ontology/
    propose_evolution against each document with the *current* schema, then
    folding in whatever type-level changes that pipeline judged safe before
    moving to the next document. Returns the converged schema, a per-document
    iteration log (for inspecting how the schema evolved and how many
    validation issues each document raised), and the type-level changes that
    still need a person to review before being applied by hand."""
    schema = seed_schema
    iterations = []
    pending_review = []
    for doc in documents:
        stem, text = doc["stem"], doc["text"]
        graph = extract_graph(text, schema)
        validation = validate_ontology(text, schema, graph, max_chars=max_chars)
        proposal = propose_evolution(text, schema, graph, validation, max_chars=max_chars)
        type_changes = [
            c for c in proposal["changes"] if c["element_type"] in ("node_type", "edge_type")
        ]
        auto_changes = [c for c in type_changes if c["decision"] in _AUTO_APPLICABLE_DECISIONS]
        review_changes = [c for c in type_changes if c["decision"] == "NEEDS_HUMAN_REVIEW"]
        schema = _apply_schema_type_changes(schema, auto_changes)
        missing_elements = validation.get("missing_elements", {})
        missing_element_count = (
            sum(len(v) for v in missing_elements.values())
            if isinstance(missing_elements, dict)
            else 0
        )
        iterations.append(
            {
                "stem": stem,
                "changes_applied": auto_changes,
                "changes_pending_review": review_changes,
                "validation_summary": validation.get("validation_summary"),
                "issue_count": len(validation.get("issues", [])),
                # The remaining fields aren't used by convergence itself --
                # they're carried through so evaluate_domain_schema() can
                # compute coverage/utilization/consistency/QA metrics without
                # re-running extraction or validation.
                "doc_chars": len(text),
                "missing_element_count": missing_element_count,
                "node_type_counts": dict(Counter(n["type"] for n in graph["nodes"])),
                "edge_type_counts": dict(Counter(e["type"] for e in graph["edges"])),
                "competency_questions": validation.get("competency_questions", []),
            }
        )
        pending_review.extend({**c, "stem": stem} for c in review_changes)
    return {"schema": schema, "iterations": iterations, "pending_review": pending_review}


def evaluate_domain_schema(schema: dict, iterations: list[dict]) -> dict:
    """Computes the quantitative signals from
    docs/ontology/domain_schema_convergence.md section 3 (coverage, type
    utilization, cross-document consistency, competency-question success
    rate) purely from an already-run converge_domain_schema() iteration log
    -- no extra LLM/embedding calls. Type redundancy and generation
    stability are deliberately not included here: both need LLM/embedding
    calls of their own (see find_redundant_type_pairs/measure_schema_stability
    below) rather than being derivable from the convergence log, so a caller
    that only wants this cheap summary isn't forced to pay for them."""
    if not iterations:
        return {
            "coverage": {"avg_issue_count": 0.0, "avg_missing_element_count": 0.0},
            "type_utilization": {},
            "consistency": {},
            "qa_success_rate": None,
        }

    doc_count = len(iterations)
    avg_issue_count = sum(it["issue_count"] for it in iterations) / doc_count
    avg_missing_element_count = sum(it["missing_element_count"] for it in iterations) / doc_count

    type_utilization = {}
    consistency = {}
    for kind, types, counts_key in (
        ("node_type", schema["node_types"], "node_type_counts"),
        ("edge_type", schema["edge_types"], "edge_type_counts"),
    ):
        for t in types:
            name = t["name"]
            counts = [it[counts_key].get(name, 0) for it in iterations]
            type_utilization[name] = sum(1 for c in counts if c > 0) / doc_count
            # Per-1000-chars density, not raw count, so a long and a short
            # document contributing the same relative amount of a type don't
            # register as "inconsistent" just because of length.
            densities = [
                (count / it["doc_chars"] * 1000) if it["doc_chars"] else 0.0
                for count, it in zip(counts, iterations)
            ]
            consistency[name] = statistics.pstdev(densities) if len(densities) > 1 else 0.0

    total_questions = 0
    answerable = 0
    for it in iterations:
        for q in it.get("competency_questions", []):
            total_questions += 1
            if q.get("answerable"):
                answerable += 1
    qa_success_rate = (answerable / total_questions) if total_questions else None

    return {
        "coverage": {
            "avg_issue_count": avg_issue_count,
            "avg_missing_element_count": avg_missing_element_count,
        },
        "type_utilization": type_utilization,
        "consistency": consistency,
        "qa_success_rate": qa_success_rate,
    }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_redundant_type_pairs(schema: dict, threshold: float = 0.9) -> list[dict]:
    """Flags node_type/edge_type pairs whose name+description embed to
    near-identical vectors (cosine similarity >= threshold) -- a domain
    schema that has grown two types for what's really one concept. Compares
    node_types against node_types and edge_types against edge_types only,
    never across the two, since a node type and an edge type can't be
    merged regardless of how similar their descriptions read."""
    model = ontology.get_embedding_model()
    pairs = []
    for kind, types in (("node_type", schema["node_types"]), ("edge_type", schema["edge_types"])):
        if len(types) < 2:
            continue
        texts = [f"{t['name']}: {t['description']}" for t in types]
        vectors = embed_with_telemetry(f"find-redundant-type-pairs-{kind}", model, texts)
        for i in range(len(types)):
            for j in range(i + 1, len(types)):
                similarity = _cosine_similarity(vectors[i], vectors[j])
                if similarity >= threshold:
                    pairs.append(
                        {
                            "element_type": kind,
                            "a": types[i]["name"],
                            "b": types[j]["name"],
                            "similarity": similarity,
                        }
                    )
    return pairs


def measure_schema_stability(
    document_text: str,
    document_type: str = "general",
    runs: int = 3,
    max_chars: int | None = None,
) -> dict:
    """Regenerates a schema for the same document `runs` times and measures
    how much the proposed type set changes run to run via pairwise Jaccard
    similarity of type-name sets. Low stability signals the *document/prompt*
    is underspecified for schema generation, not that any one generated
    schema is wrong -- see docs/ontology/domain_schema_convergence.md
    section 3."""
    if runs < 2:
        raise ValueError("runs must be at least 2 to compare schemas")
    type_name_sets = []
    for _ in range(runs):
        schema = generate_schema(document_text, document_type=document_type, max_chars=max_chars)
        names = {t["name"] for t in schema["node_types"]} | {t["name"] for t in schema["edge_types"]}
        type_name_sets.append(names)

    similarities = []
    for i in range(len(type_name_sets)):
        for j in range(i + 1, len(type_name_sets)):
            a, b = type_name_sets[i], type_name_sets[j]
            union = a | b
            similarities.append(len(a & b) / len(union) if union else 1.0)

    return {
        "runs": runs,
        "type_name_sets": [sorted(s) for s in type_name_sets],
        "avg_jaccard_similarity": sum(similarities) / len(similarities) if similarities else 1.0,
    }
