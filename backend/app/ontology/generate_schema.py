"""Schema-generation half of the ontology pipeline: propose candidate
classes/relationships for a document (discover_ontology) and, from those (or
independently), propose a node_types/edge_types schema (generate_schema).
Both have chunk-grouped map-reduce variants (see .utils's module comment) for
documents too large to send in one LLM call, plus the discover_for_document/
schema_for_document seams main.py's /discover and /schema routes call.
summarize_document (a lighter, non-JSON LLM call) and the schema-quality
checks at the bottom (find_redundant_type_pairs, measure_schema_stability)
live here too, since they all operate on a document/schema level, before any
node/edge instances exist -- see extract_graph.py for that stage and
evolve_graph.py for validating/evolving what extract_graph produces."""

import json
import math

from app import ontology
from app.llm.prompts import (
    CONSOLIDATION_PROMPT,
    DISCOVERY_PROMPT,
    SCHEMA_CONSOLIDATION_PROMPT,
    SCHEMA_PROMPTS,
    SUMMARY_PROMPT,
)
from app.llm.telemetry import embed_with_telemetry, invoke_with_telemetry

from .utils import (
    _check_document_length,
    _dedupe_by_key,
    _group_document_text,
    _load_chunk_items,
    _require_document_text,
    group_chunks_by_budget,
    parse_json_response,
)


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
