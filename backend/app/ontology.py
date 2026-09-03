import json
import logging
import math
import os
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.chat import get_chat_model
from app.embeddings import get_embedding_model, node_embedding_text
from app.telemetry import invoke_with_telemetry, embed_with_telemetry
from app import graphdb
from app.paths import data_dir, document_dir_for, documents_dir
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

DOCUMENTS_DIR = documents_dir()

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

DEFAULT_SCHEMA = {
    "node_types": [
        {"name": "Entity", "description": "A generic named entity mentioned in the document."}
    ],
    "edge_types": [
        {
            "name": "RELATED_TO",
            "description": "A generic relationship between two entities.",
            "source": "Entity",
            "target": "Entity",
        }
    ],
}

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
    model = get_chat_model()
    response = invoke_with_telemetry(
        "ontology.summarize_document", model, SUMMARY_PROMPT.format(document=document_text)
    )
    summary = response.content.strip()
    if not summary:
        raise ValueError("summary generation returned empty content")
    return summary


def discover_ontology(document_text: str, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = get_chat_model()
    response = invoke_with_telemetry(
        "ontology.discover_ontology", model, DISCOVERY_PROMPT.format(document=document_text)
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
    model = get_chat_model()
    prompt = CONSOLIDATION_PROMPT.format(groups=json.dumps(payload, ensure_ascii=False))
    response = invoke_with_telemetry("ontology.consolidate_discovery_types", model, prompt)
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
    model = get_chat_model()
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
    response = invoke_with_telemetry("ontology.generate_schema", model, prompt)
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
    model = get_chat_model()
    prompt = SCHEMA_CONSOLIDATION_PROMPT.format(groups=json.dumps(payload, ensure_ascii=False))
    response = invoke_with_telemetry("ontology.consolidate_schema_types", model, prompt)
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


def extract_graph(document_text: str, schema: dict) -> dict:
    model = get_chat_model()
    prompt = EXTRACT_PROMPT.format(
        schema=json.dumps(schema), document=document_text
    )
    response = invoke_with_telemetry("ontology.extract_graph", model, prompt)
    graph = parse_json_response(response.content)
    if not isinstance(graph.get("nodes"), list) or not isinstance(
        graph.get("edges"), list
    ):
        raise ValueError("extraction JSON missing nodes/edges lists")

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
        valid_edges.append(edge)
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


def extract_graph_from_chunks(
    chunk_items: list[dict], schema: dict, max_group_chars: int | None = None
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
    untouched, so the common case still costs exactly one LLM call."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to extract graph from")

    group_graphs = [extract_graph(_group_document_text(group), schema) for group in groups]
    if len(group_graphs) == 1:
        return group_graphs[0]

    return _merge_group_graphs(group_graphs)


def validate_ontology(document_text: str, schema: dict, graph: dict, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = get_chat_model()
    prompt = VALIDATION_PROMPT.format(
        schema=json.dumps(schema), graph=json.dumps(graph), document=document_text
    )
    response = invoke_with_telemetry("ontology.validate_ontology", model, prompt)
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
    model = get_chat_model()
    prompt = EVOLUTION_PROMPT.format(
        schema=json.dumps(schema),
        graph=json.dumps(graph),
        validation_report=json.dumps(validation_report),
        document=document_text,
    )
    response = invoke_with_telemetry("ontology.propose_evolution", model, prompt)
    proposal = parse_json_response(response.content)
    if not isinstance(proposal.get("changes"), list):
        raise ValueError("evolution JSON missing changes list")
    for change in proposal["changes"]:
        if not {"decision", "element_type", "element"} <= change.keys():
            raise ValueError("evolution change missing decision/element_type/element")
    return proposal


def _apply_type_change(type_list: list, element: dict, decision: str) -> None:
    idx = next((i for i, t in enumerate(type_list) if t["name"] == element["name"]), None)
    if decision == "DEPRECATE":
        if idx is not None:
            type_list[idx] = {**type_list[idx], "description": f"[DEPRECATED] {type_list[idx]['description']}"}
    elif idx is not None:
        type_list[idx] = element
    else:
        type_list.append(element)


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


def _apply_schema_type_changes(schema: dict, changes: list) -> dict:
    node_types = list(schema["node_types"])
    edge_types = list(schema["edge_types"])
    for change in changes:
        target = node_types if change["element_type"] == "node_type" else edge_types
        _apply_type_change(target, change["element"], change["decision"])
    return {"node_types": node_types, "edge_types": edge_types}


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
    model = get_embedding_model()
    pairs = []
    for kind, types in (("node_type", schema["node_types"]), ("edge_type", schema["edge_types"])):
        if len(types) < 2:
            continue
        texts = [f"{t['name']}: {t['description']}" for t in types]
        vectors = embed_with_telemetry(f"ontology.find_redundant_type_pairs.{kind}", model, texts)
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


# Domain schema storage/reuse -----------------------------------------------
#
# converge_domain_schema() above is a pure function -- it takes a schema in
# and returns one out, with no notion of "the schema for domain X" persisting
# between calls. This section adds that persistence, separate from the
# per-document schema_v{N}.json layout above: a domain schema belongs to a
# domain (e.g. "insurance_policy"), not to any one document, and is meant to
# be reused across every document in that domain via use_domain_schema()
# rather than regenerated per document. See
# docs/ontology/domain_schema_convergence.md section 4.
DOMAIN_SCHEMA_DIR = data_dir() / "domain_schemas"


def domain_dir_for(domain: str) -> Path:
    return DOMAIN_SCHEMA_DIR / domain


def domain_schema_path(domain: str) -> Path:
    return domain_dir_for(domain) / "schema.json"


def save_domain_schema(domain: str, schema: dict) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    domain_schema_path(domain).write_text(json.dumps(schema))


def load_domain_schema(domain: str) -> dict | None:
    path = domain_schema_path(domain)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def list_domains() -> list[str]:
    if not DOMAIN_SCHEMA_DIR.is_dir():
        return []
    return sorted(
        p.name for p in DOMAIN_SCHEMA_DIR.iterdir() if p.is_dir() and (p / "schema.json").is_file()
    )


def _domain_manifest_path(domain: str) -> Path:
    return domain_dir_for(domain) / "manifest.json"


def _load_domain_manifest(domain: str) -> dict:
    path = _domain_manifest_path(domain)
    if not path.is_file():
        return {"calibration_stems": [], "history": []}
    return json.loads(path.read_text())


def _save_domain_manifest(domain: str, manifest: dict) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    _domain_manifest_path(domain).write_text(json.dumps(manifest))


def domain_calibration_stems(domain: str) -> list[str]:
    return _load_domain_manifest(domain)["calibration_stems"]


def domain_convergence_history(domain: str) -> list[dict]:
    return _load_domain_manifest(domain)["history"]


def _domain_pending_review_path(domain: str) -> Path:
    return domain_dir_for(domain) / "pending_review.json"


def load_domain_pending_review(domain: str) -> list[dict]:
    path = _domain_pending_review_path(domain)
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def _save_domain_pending_review(domain: str, items: list[dict]) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    _domain_pending_review_path(domain).write_text(json.dumps(items))


def run_domain_convergence(domain: str, documents: list[dict], max_chars: int | None = None) -> dict:
    """Runs converge_domain_schema() over `documents` and persists the
    result under backend/data/domain_schemas/{domain}/. If `domain` already
    has a stored schema, that schema is the seed and every document in
    `documents` is folded in -- calling this again later with newly
    calibrated documents keeps refining the same domain schema rather than
    starting over. If `domain` has no stored schema yet, `documents[0]`
    seeds it (via generate_schema) and the rest are folded in, exactly like
    a fresh converge_domain_schema() call.

    NEEDS_HUMAN_REVIEW changes accumulate in the domain's pending_review
    store across calls (not just this one) until apply_domain_schema_changes
    resolves them, since they were never applied to the schema."""
    existing_schema = load_domain_schema(domain)
    if existing_schema is not None:
        seed_schema = existing_schema
        remaining = documents
    else:
        if not documents:
            raise ValueError(f"no domain schema stored for {domain!r} and no documents to seed one from")
        seed_schema = generate_schema(documents[0]["text"], max_chars=max_chars)
        remaining = documents[1:]

    result = converge_domain_schema(remaining, seed_schema, max_chars=max_chars)
    save_domain_schema(domain, result["schema"])

    manifest = _load_domain_manifest(domain)
    stems = [doc["stem"] for doc in documents]
    manifest["calibration_stems"] = sorted(set(manifest["calibration_stems"]) | set(stems))
    manifest["history"].append(
        {
            "stems": stems,
            "changes_applied_count": sum(len(it["changes_applied"]) for it in result["iterations"]),
            "changes_pending_review_count": len(result["pending_review"]),
            "converged_at": datetime.now().isoformat(),
        }
    )
    _save_domain_manifest(domain, manifest)

    if result["pending_review"]:
        pending = load_domain_pending_review(domain)
        pending.extend(result["pending_review"])
        _save_domain_pending_review(domain, pending)

    return {**result, "domain": domain, "seed_schema": seed_schema}


def apply_domain_schema_changes(domain: str, changes: list) -> dict:
    """Applies a human-reviewed subset of a domain's accumulated
    pending_review changes (same contract as apply_evolution: the caller is
    expected to have already filtered `changes` down to what a person
    accepted) and removes exactly those change_ids from the pending queue."""
    schema = load_domain_schema(domain)
    if schema is None:
        raise ValueError(f"no domain schema stored for {domain!r}")
    new_schema = _apply_schema_type_changes(schema, changes)
    save_domain_schema(domain, new_schema)

    applied_ids = {c.get("change_id") for c in changes}
    remaining = [c for c in load_domain_pending_review(domain) if c.get("change_id") not in applied_ids]
    _save_domain_pending_review(domain, remaining)
    return {"schema": new_schema, "pending_review": remaining}


def use_domain_schema(stem: str, domain: str, document_type: str = "general") -> int:
    """Copies domain `domain`'s current schema onto document `stem` as a new
    schema version -- the reuse half of this feature, mirroring how
    main.py's existing /schema/use endpoint copies one document's schema
    onto another, except the source is a domain schema rather than another
    document's."""
    schema = load_domain_schema(domain)
    if schema is None:
        raise ValueError(f"no domain schema stored for {domain!r}")
    return create_schema_version(stem, schema, document_type=document_type)


def versions_path(stem: str) -> Path:
    return document_dir_for(stem) / "versions.json"


def _load_versions_manifest(stem: str) -> dict:
    path = versions_path(stem)
    if not path.is_file():
        return {"active_version": None, "versions": []}
    return json.loads(path.read_text())


def _save_versions_manifest(stem: str, manifest: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    versions_path(stem).write_text(json.dumps(manifest))


def list_versions(stem: str) -> list[dict]:
    return _load_versions_manifest(stem)["versions"]


def get_active_version(stem: str) -> int | None:
    return _load_versions_manifest(stem)["active_version"]


def schema_path_for_version(stem: str, version: int) -> Path:
    return document_dir_for(stem) / f"schema_v{version}.json"


def save_schema(stem: str, version: int, schema: dict) -> None:
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    schema_path_for_version(stem, version).write_text(json.dumps(schema))


def load_schema(stem: str, version: int) -> dict | None:
    path = schema_path_for_version(stem, version)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def create_schema_version(stem: str, schema: dict, document_type: str = "general") -> int:
    manifest = _load_versions_manifest(stem)
    next_version = max((v["version"] for v in manifest["versions"]), default=0) + 1
    save_schema(stem, next_version, schema)
    manifest["versions"].append(
        {
            "version": next_version,
            "document_type": document_type,
            "created_at": datetime.now().isoformat(),
        }
    )
    manifest["active_version"] = next_version
    _save_versions_manifest(stem, manifest)
    return next_version


def activate_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    if not any(v["version"] == version for v in manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    manifest["active_version"] = version
    _save_versions_manifest(stem, manifest)


def delete_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    remaining = [v for v in manifest["versions"] if v["version"] != version]
    if len(remaining) == len(manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    schema_path_for_version(stem, version).unlink(missing_ok=True)
    graphdb.delete_version_data(stem, version)
    manifest["versions"] = remaining
    if manifest["active_version"] == version:
        manifest["active_version"] = max((v["version"] for v in remaining), default=None)
    _save_versions_manifest(stem, manifest)


def save_document_manifest(stem: str, original_filename: str, converter: str = "anydoc") -> None:
    """Records the per-document info the rest of this module's stem-based
    file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md", and
    which PDF-to-Markdown converter produced that Markdown ("anydoc" or
    "table_aware" -- see app.chunking). Schema and graph presence are
    deliberately NOT duplicated here -- load_schema and graphdb.has_graph
    already answer those live, so there's nothing to keep in sync."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"original_filename": original_filename, "converter": converter})
    )


def load_document_manifest(stem: str) -> dict | None:
    path = document_dir_for(stem) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def discovery_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "discovery.json"


def save_discovery(stem: str, report: dict) -> None:
    """One discovery report per document, not per schema version -- discovery
    is an exploratory, re-runnable read of the document itself, not tied to
    any particular schema/extraction attempt, so overwriting on every run
    (rather than versioning it like schema_v{N}.json) is intentional."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    discovery_path_for(stem).write_text(json.dumps(report))


def load_discovery(stem: str) -> dict | None:
    path = discovery_path_for(stem)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def summary_path_for(stem: str) -> Path:
    return document_dir_for(stem) / "summary.json"


def save_document_summary(stem: str, summary: str) -> None:
    """One summary per document, overwritten on regeneration -- same
    exploratory-artifact model as discover_ontology/save_discovery above."""
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    summary_path_for(stem).write_text(json.dumps({"summary": summary}, ensure_ascii=False))


def load_document_summary(stem: str) -> str | None:
    path = summary_path_for(stem)
    if not path.is_file():
        return None
    return json.loads(path.read_text())["summary"]


def embed_nodes(nodes: list) -> list:
    """Attaches an "embedding" vector to each node (label + detail text),
    so graphdb.find_similar_nodes has something to rank against later when
    a question's keywords don't literally match any node's label. Returns
    new dicts rather than mutating the input."""
    if not nodes:
        return []
    model = get_embedding_model()
    texts = [node_embedding_text(n) for n in nodes]
    vectors = embed_with_telemetry("ontology.embed_nodes", model, texts)
    return [{**node, "embedding": vector} for node, vector in zip(nodes, vectors)]


def save_graph(stem: str, graph: dict, version: int = 1) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"], version=version)


def embed_graph(stem: str, version: int = 1) -> int:
    """Embeds this document version's already-extracted nodes in a separate
    pass from extraction, so a large document's LLM extraction call doesn't
    also pay for the embedding call before anything is visible. Reads the
    nodes graphdb already has (written by save_graph with no embedding),
    computes vectors, and updates them in place via graphdb.update_node_embeddings
    -- rerunning this is safe and simply recomputes/overwrites every node's
    embedding."""
    graph = graphdb.load_graph(stem, version=version)
    if graph is None or not graph["nodes"]:
        return 0
    nodes = embed_nodes(graph["nodes"])
    graphdb.update_node_embeddings(stem, nodes, version=version)
    return len(nodes)


def list_schema_stems() -> list[str]:
    if not DOCUMENTS_DIR.is_dir():
        return []
    return [
        d.name
        for d in DOCUMENTS_DIR.iterdir()
        if d.is_dir() and (d / "versions.json").is_file()
    ]


def load_graph(stem: str, version: int = 1) -> dict | None:
    return graphdb.load_graph(stem, version=version)
