"""Post-extraction stage of the ontology pipeline: validate an extracted
graph against its document/schema (validate_ontology), propose changes from
that validation (propose_evolution), and apply an already-human-reviewed
subset of a proposal (apply_evolution) -- see extract_graph.py for the stage
that produces the graph these operate on. Domain schema convergence
(converge_domain_schema/evaluate_domain_schema) reuses this same
extract -> validate -> propose_evolution cycle across a *sequence* of
documents from one domain; see its own comment below."""

import json
import statistics
from collections import Counter

from langchain_core.messages import HumanMessage, SystemMessage

from app import ontology
from app.graph import graphdb
from app.llm.prompts import EVOLUTION_PROMPT, VALIDATION_PROMPT
from app.llm.telemetry import invoke_with_telemetry

from .extract_graph import extract_graph
from .persistence import (
    _apply_schema_type_changes,
    _apply_type_change,
    create_schema_version,
    get_active_version,
    list_versions,
    load_schema,
)
from .utils import _check_document_length, parse_json_response


def validate_ontology(document_text: str, schema: dict, graph: dict, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model("validate_ontology")
    messages = [
        SystemMessage(content=VALIDATION_PROMPT),
        HumanMessage(
            content=(
                f"Ontology schema:\n{json.dumps(schema)}\n\n"
                f"Extracted graph (nodes and edges):\n{json.dumps(graph)}\n\n"
                f"Document:\n{document_text}"
            )
        ),
    ]
    response = invoke_with_telemetry("validate-ontology", model, messages)
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
    # Explicit "propose_evolution" operation (rather than the bare call every
    # other non-ontology prose/JSON caller across the app also makes) is what
    # lets app.llm.chat.get_chat_model recognize this as JSON-expecting (see
    # its _JSON_OPERATIONS) and apply a realistic max_tokens ceiling (see its
    # OPERATION_MAX_TOKENS) -- model *selection* is unaffected, since
    # "propose_evolution" isn't in OPERATION_KEYS either and still falls
    # through to the same "default" bucket as before.
    model = ontology.get_chat_model("propose_evolution")
    messages = [
        SystemMessage(content=EVOLUTION_PROMPT),
        HumanMessage(
            content=(
                f"Current ontology schema:\n{json.dumps(schema)}\n\n"
                f"Current extracted graph (nodes and edges):\n{json.dumps(graph)}\n\n"
                f"Validation report:\n{json.dumps(validation_report)}\n\n"
                f"Document:\n{document_text}"
            )
        ),
    ]
    response = invoke_with_telemetry("propose-evolution", model, messages)
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
    in generate_schema.py) rather than being derivable from the convergence
    log, so a caller that only wants this cheap summary isn't forced to pay
    for them."""
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
