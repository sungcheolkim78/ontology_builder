import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from app.chat import get_chat_model
from app.embeddings import get_embedding_model, node_embedding_text
from app.telemetry import invoke_with_telemetry, embed_with_telemetry
from app import graphdb
from app.paths import data_dir

GRAPH_DIR = data_dir() / "graph"

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
MAX_DOCUMENT_CHARS = int(os.environ.get("MAX_DOCUMENT_CHARS", 300_000))


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

# Shared by every schema-generation prompt variant below -- the identifier rule
# and output shape are policy, not something that should vary per document type.
_SCHEMA_OUTPUT_INSTRUCTIONS = """Before finalizing edge_types, check each one against the document: you \
must be able to point to at least one concrete pair of entities already in the \
text that the relationship actually connects. Drop any edge_type you can't \
ground in a specific instance like this, even if it seems like a relationship \
this kind of document would generally have. Also collapse edge_types that \
describe the same underlying relationship into one (e.g. don't propose both \
"WRITTEN_BY" and "AUTHORED_BY") -- near-duplicates split usage between them and \
extraction ends up favoring one, leaving the other unused.

After drafting node_types, check the reverse direction too: prefer that every \
node_type appear as the "source" or "target" of at least one edge_type, since \
a node_type with none will always end up producing disconnected nodes with no \
relationships at extraction time. Where the document actually supports a \
relationship for such a type, add the missing edge_type rather than leaving it \
unreachable. But don't force a relationship that isn't really there -- a type \
that is genuinely useful on its own for browsing or filtering (e.g. Date, \
Event, DocumentSection) may stay edgeless if the document doesn't clearly \
connect it to anything.

Name edge_types as verbs or verb phrases describing what the source does to \
or has with the target (e.g. COVERS, REQUIRES, EXCLUDES, PAYS), not as nouns. \
Avoid vague catch-all relationships such as RELATED_TO, ASSOCIATED_WITH, or \
HAS_INFO -- use one only when the document genuinely offers nothing more \
specific to say about how two entities connect. Separately, if the document \
has a classification/hierarchy relationship (a general category containing \
more specific subtypes, e.g. a product line and its individual products), \
represent that with its own taxonomic edge_type (e.g. IS_A or SUBTYPE_OF) \
rather than folding it into a business-meaning edge_type like COVERS or \
BELONGS_TO -- keep "this is a kind of that" separate from "this does \
something to/for that".

Before finalizing the schema, silently check it against a handful of \
questions a user would realistically ask about this document (do not include \
these questions in the output). If answering one of them would clearly \
require a node_type or edge_type the schema is missing, add it; if every \
type you have is unused by any such question, reconsider whether it belongs.

Every "name" value (for both node_types and edge_types) MUST be a valid \
identifier: letters, digits, and underscores only, no spaces or other \
characters, and it must start with a letter or underscore (e.g. "JobTitle" \
or "Job_Title", not "Job Title"). This applies even if the document is not \
in English -- transliterate or translate the name into an ASCII identifier.

Write every "description" value in the same language as the document itself \
(e.g. Korean descriptions for a Korean document, English descriptions for an \
English document), regardless of what language the "name" identifier above \
ends up in. For each edge_type, also state the direction inside the \
description itself -- what the "source" side and "target" side each are (e.g. \
"WRITTEN_BY: source is the document, target is its author") -- so the \
direction is unambiguous even without looking anywhere else.

Aim for around 5-12 node_types and 5-15 edge_types as a starting budget; only \
go beyond that if the document clearly has that much genuine variety, not by \
splitting things finer to fill the range. If nothing in the document fits a \
meaningful ontology, return empty node_types/edge_types arrays rather than \
inventing types to fill them. Prefer canonical, reusable type names (roles, \
categories the document domain generally has) over document-specific one-off \
labels, unless the document itself defines a term precisely enough that a \
generic name would lose that precision. When transliterating a non-English \
name into an ASCII identifier, check it doesn't collide with another type's \
identifier -- add a disambiguating suffix if it would.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": [{{"name": "...", "description": "..."}}], \
"edge_types": [{{"name": "...", "description": "...", "source": "<node type name>", \
"target": "<node type name>"}}]}}

Document:
{document}
"""

SCHEMA_PROMPT = """Given the following document, propose an ontology schema for \
extracting entities and relationships from it.

This document is a general-purpose text (e.g. report, article, manual, memo, \
meeting notes) rather than a formally structured legal/insurance document. When \
proposing node_types, look for the categories of thing the document actually \
returns to more than once: people and organizations (and their roles, e.g. \
author, customer, department); named concepts, topics, products, or systems that \
are defined or discussed repeatedly; places; and events, dates, or time periods \
that matter to the content. When proposing edge_types, look for how those \
categories actually connect: structural or hierarchical relationships (part of, \
belongs to, contains), attribution (authored by, owns, responsible for), causal \
or sequential relationships (causes, leads to, precedes, follows), and plain \
association where the document doesn't specify anything more precise. Favor a \
small number of types that each cover many instances in the document over a \
large number of narrow, one-off types; use the document's own terminology for \
names rather than generic labels like "Entity" or "RelatedTo" wherever the \
document supports something more specific, and fall back to a generic type only \
for content that genuinely doesn't fit anything narrower. Only propose types the \
document actually supports -- do not invent categories the text never actually \
uses.

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

# For statutes, contracts, and insurance policies/terms: documents built from a
# formal internal structure (chapters/articles/paragraphs/items, e.g. 장/절/조/항/호)
# and explicitly defined terms reused throughout. The generic SCHEMA_PROMPT above
# tends to propose only surface-level named entities and misses definitions,
# obligations/conditions/exclusions, and cross-references between provisions --
# this variant asks the LLM to look for those specifically, without forcing any of
# them into a schema that doesn't actually have them.
LEGAL_SCHEMA_PROMPT = """Given the following document, propose an ontology schema for \
extracting entities and relationships from it.

This document is a legal or insurance-style document (e.g. statute, contract, \
insurance policy/terms) with a formal internal structure (chapters/articles/\
paragraphs/items, e.g. 장/절/조/항/호) and defined terms reused throughout. When \
proposing node_types and edge_types, look specifically for: defined terms from a \
definitions clause (kept separate from the entities that later use them); parties \
and roles (e.g. insurer/policyholder/insured/beneficiary); obligations, rights, \
and benefits each party has; conditions and exclusions that trigger or bar them \
(e.g. waiting periods, 면책사유); cross-references between the document's own \
provisions (e.g. "제15조에 따라", "전항에도 불구하고"); and the document's own \
structural units (e.g. "Article") as their own node_type only when cross-\
references between them are frequent enough that a section tree is actually \
worth navigating or tracing -- not just because the document happens to have \
numbered sections. Only propose types the document actually supports, using \
its own terminology rather than generic labels wholesale.

Keep a structural node_type like "Article" purely structural: it identifies \
*where* something is written, not *what* it says. Never let it become a \
catch-all for the substantive content of the provisions themselves -- a single \
article routinely states more than one kind of thing (e.g. both a payment \
condition and an exclusion in the same clause), so representing that content \
as a property of the Article node, or as one more Article instance, collapses \
distinct meanings into an undifferentiated bucket. Instead, pull the actual \
substance out into its own concept node_types (parties, benefits, conditions/\
events, exclusions, defined terms -- as above) and connect each back to the \
article it comes from with an edge_type (e.g. "STATES", "DEFINES", "TRIGGERS"). \
The same rule applies to any other structural/sectioning node_type you \
introduce (e.g. "Clause", "Schedule", "Appendix").

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

SCHEMA_PROMPTS = {
    "general": SCHEMA_PROMPT,
    "legal": LEGAL_SCHEMA_PROMPT,
}

EXTRACT_PROMPT = """Using this ontology schema:
{schema}

Extract entities and relationships from the following document that conform to \
this schema.

For each node: "type" is the node_type name from the schema; "id" is a short, \
stable identifier unique within this document (your own choice of slug -- it \
never has to appear in the document text verbatim); "label" is the entity's \
canonical surface form as it actually appears in the document -- prefer the \
first occurrence, or the fullest/most complete form if later occurrences add \
detail the first one lacks (e.g. a full name after an initial short mention).

If the document refers to the same real-world entity multiple times under \
different names, aliases, abbreviations, or pronouns (coreference) -- e.g. \
"김철수" and "김 대표", or a company and "동사" -- extract exactly ONE node for \
that entity, not one per mention. Do not create a separate node per surface \
form of the same thing.

For each node and edge, also include a "detail" field: one or two sentences of \
specific supporting information from the document -- exact conditions, exceptions, \
figures, dates, or phrasing -- that isn't captured by the label/type alone. Every \
claim in "detail" must be directly supported by the document text -- don't add \
inference, summary judgment, or outside/general knowledge about the entity. For \
a condition, exception, figure, or date specifically, quote the document's own \
wording for it rather than paraphrasing, so the exact number/date/phrasing is \
preserved verbatim; paraphrase only the surrounding context needed to make that \
quote make sense. When "detail" includes a quantity, percentage, or duration, \
state it with its unit exactly as the document does (e.g. "90일 이내", "가입금액의 \
50%") rather than a vaguer description, so figures stay comparable across nodes. \
Omit "detail" (or leave it an empty string) if the document has nothing beyond \
the label worth adding.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"nodes": [{{"id": "...", "label": "...", "type": "<a node type name from the schema>", \
"detail": "..."}}], "edges": [{{"source": "<node id>", "target": "<node id>", \
"type": "<an edge type name from the schema>", "detail": "..."}}]}}

Document:
{document}
"""


# Adapted from docs/ontology/ontology_validation_prompt.md (the team's
# ontology-validation agent spec) into a single-call JSON-output prompt, the
# same shape every other prompt in this module uses. Keeps that doc's
# validation dimensions (semantic/structural/provenance/rule/extraction/
# consistency), severity scale, and "flag, don't fix" rule; folds its
# competency-question check in by having the LLM derive a handful of
# competency questions from the schema itself, since this app has no
# separately-authored competency-question list to validate against.
VALIDATION_PROMPT = """You are a senior Ontology Validator and Knowledge Graph \
Quality Engineer. Validate the ontology schema and the knowledge graph \
extracted from it against the source document below. Identify errors, \
omissions, contradictions, and ontology weaknesses -- do NOT silently fix \
anything; only report what should change and why.

Check each of these dimensions:

- Semantic: is every node/edge type well-defined? Are similar concepts \
incorrectly split into different types, or different concepts incorrectly \
merged into one? Are edge directions and meanings semantically correct?
- Structural: for each edge instance, do its source/target node types match \
what the schema's edge_type declares?
- Provenance: for nodes/edges with a "detail" field, is that detail actually \
supported by the document text, or is it invented/overstated? Flag \
MISSING_EVIDENCE, INCORRECT_EVIDENCE, or WEAK_EVIDENCE.
- Rule/figures: for conditions, exceptions, thresholds, dates, and amounts \
mentioned in the document, has any numerical or qualifying detail been lost \
or altered in the extracted nodes/edges?
- Extraction completeness: does the document contain important entities, \
relationships, attributes, events, or rules that the schema or the \
extraction missed? Flag MISSING_ENTITY, MISSING_RELATIONSHIP, \
MISSING_ATTRIBUTE, MISSING_EVENT, MISSING_RULE.
- Consistency: flag CONTRADICTION, DUPLICATE_ENTITY, DUPLICATE_RELATION, \
AMBIGUOUS_ENTITY, AMBIGUOUS_RELATION.

Competency questions: derive 3-5 realistic questions a user of this document \
would ask (grounded in what the schema's own types/relationships suggest the \
document is about), then for each determine whether the current schema and \
graph can answer it, and if not, what node/edge types or instances are \
missing.

Assign each issue a severity: CRITICAL (produces materially incorrect \
knowledge), HIGH (important business meaning lost or incorrect), MEDIUM \
(significant modeling/extraction weakness), LOW (minor inconsistency), INFO \
(non-critical observation).

Write every text field (description, evidence, recommended_action, question, \
missing_elements entries) in the same language as the document.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"validation_summary": {{"ontology_valid": true/false, "extraction_valid": \
true/false, "provenance_valid": true/false, \
"competency_questions_answerable": true/false, "overall_quality": \
"<one short sentence>"}}, "issues": [{{"severity": \
"CRITICAL|HIGH|MEDIUM|LOW|INFO", "category": "...", "description": "...", \
"affected_element": "...", "evidence": "...", "recommended_action": "..."}}], \
"missing_elements": {{"classes": ["..."], "relationships": ["..."], \
"attributes": ["..."], "events": ["..."], "rules": ["..."]}}, \
"contradictions": ["..."], "ambiguities": ["..."], "competency_questions": \
[{{"question": "...", "answerable": true/false, "missing_elements": ["..."], \
"evidence": "..."}}], "recommended_changes": ["..."]}}

Ontology schema:
{schema}

Extracted graph (nodes and edges):
{graph}

Document:
{document}
"""


def parse_json_response(text: str) -> dict:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}")


def generate_schema(
    document_text: str, document_type: str = "general", max_chars: int | None = None
) -> dict:
    _check_document_length(document_text, max_chars)
    prompt_template = SCHEMA_PROMPTS.get(document_type)
    if prompt_template is None:
        raise ValueError(f"unknown document_type: {document_type!r}")
    model = get_chat_model()
    response = invoke_with_telemetry(
        "ontology.generate_schema", model, prompt_template.format(document=document_text)
    )
    schema = parse_json_response(response.content)
    if not isinstance(schema.get("node_types"), list) or not isinstance(
        schema.get("edge_types"), list
    ):
        raise ValueError("schema JSON missing node_types/edge_types lists")
    return schema


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


# Adapted from docs/ontology/ontology_evolution_prompt.md (the team's
# ontology-evolution agent spec) into a single-call JSON-output prompt. Takes
# a validation report as input rather than re-discovering problems itself, so
# it only proposes changes for issues already found -- a targeted patch, not
# a from-scratch schema/extraction redo. Mirrors the spec's decision set
# (ADD/MODIFY/MERGE/DEPRECATE/REJECT/NEEDS_HUMAN_REVIEW) and its governance
# rule that anything with material business/semantic impact must not be
# applied automatically -- this module only ever proposes; apply_evolution
# below applies whatever the caller (after human review) actually sends back.
EVOLUTION_PROMPT = """You are a senior Ontology Governance and Evolution Architect. The \
existing ontology schema below is authoritative -- do not propose changing it \
just because a new term appears in the document. Given the document, its \
current schema, its current extracted knowledge graph, and a validation \
report that already found problems, propose a minimal, disciplined set of \
changes that fix flagged issues and fill genuinely missing pieces -- not a \
redo from scratch.

For every schema-level candidate (a node_type or edge_type to add, fix, or \
retire) and every instance-level candidate (a specific node or edge to add \
to the graph, grounded in the document, to fill a MISSING_ENTITY/ \
MISSING_RELATIONSHIP/MISSING_ATTRIBUTE/MISSING_EVENT/MISSING_RULE the \
validation report flagged), choose exactly one decision:

ADD -- the new element has independent meaning, cannot be represented by an \
existing element, occurs in meaningful context, and is supported by evidence.
MODIFY -- an existing element's definition is demonstrably incomplete or \
incorrect; state what it was and what it becomes.
MERGE -- two elements are semantically identical, not just lexically \
similar; name the merge target.
DEPRECATE -- an existing element is obsolete; never propose deleting it \
outright.
REJECT -- a document-specific one-off phrase, an already-covered synonym, \
insufficient evidence, or something that would add complexity without \
improving the ontology.
NEEDS_HUMAN_REVIEW -- the change would materially affect business \
semantics, regulatory/contractual interpretation, class hierarchy, \
relationship semantics, or cardinality -- these must never be applied \
automatically.

Only propose ADD for a graph instance (a node or edge) when you can point \
to the specific document text that supports it -- put that text in \
"evidence". Do not propose MERGE or DEPRECATE for a graph instance unless \
the validation report specifically flagged a duplicate or contradiction; \
prefer schema-level changes over expanding the graph indiscriminately.

Write every reason/evidence value in the same language as the document.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"evolution_summary": {{"changes_proposed": <int>, "human_review_required": \
true/false}}, "changes": [{{"change_id": "...", "decision": \
"ADD|MODIFY|MERGE|DEPRECATE|REJECT|NEEDS_HUMAN_REVIEW", "element_type": \
"node_type|edge_type|node|edge", "element": {{...}}, "reason": "...", \
"evidence": "...", "confidence": "HIGH|MEDIUM|LOW"}}]}}

For element_type "node_type": element is {{"name": "...", "description": "..."}}.
For element_type "edge_type": element is {{"name": "...", "description": \
"...", "source": "<node type name>", "target": "<node type name>"}}.
For element_type "node": element is {{"id": "...", "label": "...", "type": \
"<a node type name from the schema, existing or newly proposed>", "detail": "..."}}.
For element_type "edge": element is {{"source": "<node id, existing or one \
you're adding in this same response>", "target": "<node id>", "type": "<an \
edge type name>", "detail": "..."}}.

Current ontology schema:
{schema}

Current extracted graph (nodes and edges):
{graph}

Validation report:
{validation_report}

Document:
{document}
"""


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


def graph_dir_for(stem: str) -> Path:
    return GRAPH_DIR / stem


def versions_path(stem: str) -> Path:
    return graph_dir_for(stem) / "versions.json"


def _load_versions_manifest(stem: str) -> dict:
    path = versions_path(stem)
    if not path.is_file():
        return {"active_version": None, "versions": []}
    return json.loads(path.read_text())


def _save_versions_manifest(stem: str, manifest: dict) -> None:
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    versions_path(stem).write_text(json.dumps(manifest))


def list_versions(stem: str) -> list[dict]:
    return _load_versions_manifest(stem)["versions"]


def get_active_version(stem: str) -> int | None:
    return _load_versions_manifest(stem)["active_version"]


def schema_path_for_version(stem: str, version: int) -> Path:
    return graph_dir_for(stem) / f"schema_v{version}.json"


def save_schema(stem: str, version: int, schema: dict) -> None:
    d = graph_dir_for(stem)
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


def save_document_manifest(stem: str, original_filename: str) -> None:
    """Records the one piece of per-document info the rest of this module's
    stem-based file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md". Schema
    and graph presence are deliberately NOT duplicated here -- load_schema
    and graphdb.has_graph already answer those live, so there's nothing to
    keep in sync."""
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"original_filename": original_filename}))


def load_document_manifest(stem: str) -> dict | None:
    path = graph_dir_for(stem) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


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
    if not GRAPH_DIR.is_dir():
        return []
    return [
        d.name
        for d in GRAPH_DIR.iterdir()
        if d.is_dir() and (d / "versions.json").is_file()
    ]


def load_graph(stem: str, version: int = 1) -> dict | None:
    return graphdb.load_graph(stem, version=version)
