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


# Adapted from docs/ontology/ontology_discovery_prompt.md (the team's earlier,
# richer "candidate ontology" stage) into a single-call JSON-output prompt.
# Deliberately produces a different, broader artifact than SCHEMA_PROMPT/
# LEGAL_SCHEMA_PROMPT above -- a domain model, taxonomy, attributes, events,
# rules, terminology map, and competency questions, none of which
# generate_schema's node_types/edge_types shape has room for -- rather than
# trying to force it into that shape. Kept as a separate, optional,
# read-only step: generate_schema's default behavior (discovery=None) is
# unchanged, so existing schema generation/extraction/validation/evolution
# keep working exactly as before regardless of whether this ever runs. See
# generate_schema's `discovery` param below for the one place the two
# connect -- an opt-in hint, not a replacement.
DISCOVERY_PROMPT = """You are a senior Ontology Architect and Knowledge Engineer. Discover a \
candidate domain ontology from the document below -- you are NOT performing \
final entity extraction. Identify the conceptual structure needed to \
represent the document's meaning, suitable for knowledge graph \
construction, GraphRAG, and question answering. The result is a candidate \
ontology for a person to review and refine, not a final one.

Follow these principles:
- Model meaning, not vocabulary -- don't create a class just because a noun \
appears often; only for concepts with independent semantic meaning.
- Separate taxonomy relationships (isA/subClassOf/partOf) from business \
relationships (covers/requires/pays/excludes/appliesTo/...); do not mix \
them in one relationship.
- Prefer meaningful directional relationship names (covers, requires, \
triggers, causes, belongsTo, definedBy, derivedFrom, ...) over vague ones \
(relatedTo, associatedWith, hasInformation) unless nothing better fits.
- Note temporal semantics (effective/expiration dates, versions, event \
sequence) and provenance wherever the document supports it.
- Minimize unnecessary complexity -- a minimal but expressive ontology \
beats a maximal one.
- Do not invent classes, relationships, or business concepts the document \
doesn't support; do not silently resolve ambiguity or merge concepts on \
lexical similarity alone -- mark confidence UNKNOWN or add a warning \
instead.

For each candidate class, classify it as one of: CONCEPT, ENTITY, EVENT, \
VALUE_OBJECT, ATTRIBUTE, DOCUMENT, RULE, RELATIONSHIP. For each candidate \
relationship, classify it as TAXONOMY or BUSINESS. Generate 10-20 \
competency questions the eventual ontology should be able to answer.

Write every definition/description/rationale value in the same language as \
the document.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"domain_model": {{"domain": "...", "subdomains": ["..."], "document_types": \
["..."], "business_processes": ["..."], "major_actors": ["..."]}}, "classes": \
[{{"name": "...", "definition": "...", "category": \
"CONCEPT|ENTITY|EVENT|VALUE_OBJECT|ATTRIBUTE|DOCUMENT|RULE|RELATIONSHIP", \
"parent": "...", "rationale": "...", "confidence": \
"HIGH|MEDIUM|LOW|UNKNOWN"}}], "relationships": [{{"name": "...", \
"definition": "...", "source": "...", "target": "...", "category": \
"TAXONOMY|BUSINESS", "rationale": "...", "confidence": \
"HIGH|MEDIUM|LOW|UNKNOWN"}}], "attributes": [{{"name": "...", "defined_on": \
"...", "definition": "...", "datatype": "...", "unit": "...", "required": \
true/false, "rationale": "..."}}], "events": [{{"name": "...", "definition": \
"...", "trigger": "...", "affected_entities": ["..."]}}], "rules": \
[{{"name": "...", "description": "...", "conditions": ["..."], \
"consequences": ["..."], "exceptions": ["..."]}}], "terminology": \
[{{"canonical_term": "...", "synonyms": ["..."], "abbreviations": ["..."], \
"source_terms": ["..."]}}], "competency_questions": ["..."], "warnings": \
["..."]}}

Document:
{document}
"""


SUMMARY_PROMPT = """다음 문서를 한국어로 2~3문장으로 간결하게 요약하세요. \
설명이나 머리말 없이 요약문만 출력하세요.

문서:
{document}
"""


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
