"""All LLM prompt templates used by app.ontology and app.goldenset, kept in
one module so the prompt text (and the design-rationale comments attached to
each one) can be read/edited independently of the extraction/storage logic
that fills them in and parses their output."""

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

For a simple, single relationship, propose a direct edge_type between the two \
concepts it connects (e.g. "PAYS", "REQUIRES", "EXCLUDES"). But when a single \
provision states a relationship with several conditions, exceptions, or \
qualifiers at once (e.g. "암 진단 확정 시 가입금액의 50%를 지급하되, 계약일로부터 \
90일 이내에는 지급하지 않는다" -- one payment obligation with a bearer, a \
condition, an amount, AND an exception), don't try to squeeze all of that onto \
one edge's properties. Instead, propose a node_type for the rule itself (e.g. \
"Norm", "Rule", "PaymentNorm") and connect it to each of its participants with \
its own edge_type (e.g. "HAS_BEARER", "HAS_CONDITION", "HAS_AMOUNT", \
"HAS_EXCEPTION") -- this keeps every qualifier independently searchable and \
citable instead of flattened into one edge no one part of which can be found \
on its own.

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

If the schema's entry for this node/edge's exact type declares a "properties" \
object (property name -> {{"datatype": ..., "required": ...}}), also include a \
"properties" object on the node/edge with a value for every declared property \
the document text actually supports -- use only property names the schema \
declares for that exact type, expressed as plain strings even for a numeric, \
boolean, or date datatype (e.g. "50" not 50), and omit any declared property \
the document doesn't clearly support rather than guessing. Never invent a \
property name the schema didn't declare for that type.

For each node and edge, also include an "evidence" field: the single shortest \
verbatim span (a clause or sentence) copied character-for-character from the \
document text that most directly supports this node/edge. This is checked \
against the document text afterward, and any "evidence" that isn't found \
verbatim is discarded -- so copy it exactly rather than paraphrasing or \
lightly editing it. Omit "evidence" if no single span of the text supports \
this node/edge better than the surrounding text in general.

If the document text contains bracketed section labels marking distinct \
parts (e.g. a line reading exactly "[주계약 > 제17조(보험금의 지급)]"), also \
include a "source_section" field copying the single nearest such label \
exactly, character-for-character, for wherever this node/edge's evidence \
appears. Omit "source_section" if the document text has no such labels, or if \
you're not sure which one applies.

For each node and edge, also include a "confidence" field: "HIGH" if the \
document states this fact directly and unambiguously, "MEDIUM" if it requires \
minor inference (e.g. resolving a pronoun or an implied subject), or "LOW" if \
it's a plausible but uncertain reading.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"nodes": [{{"id": "...", "label": "...", "type": "<a node type name from the schema>", \
"detail": "...", "properties": {{}}, "evidence": "...", "source_section": "...", \
"confidence": "HIGH|MEDIUM|LOW"}}], "edges": [{{"source": "<node id>", "target": "<node id>", \
"type": "<an edge type name from the schema>", "detail": "...", "properties": {{}}, \
"evidence": "...", "source_section": "...", "confidence": "HIGH|MEDIUM|LOW"}}]}}

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


# The consolidation call only ever sees name/definition/category(/source/
# target) -- never full group text or instance data -- so its input size
# stays flat regardless of how many groups or how long the document is, and
# it can't accidentally re-derive a class/relationship from raw text a
# per-group pass already read once.
CONSOLIDATION_PROMPT = """You are a senior Ontology Architect. Multiple independent ontology-discovery \
passes were run over different sections of the SAME document, each seeing only \
its own section's text. Their candidate classes and relationships are listed \
below, grouped by which pass produced them. Merge these into ONE unified set:

- Merge classes that name the same real concept even if the name or wording \
differs across groups (pick or adapt the clearest name and definition); keep \
genuinely distinct concepts separate rather than merging on lexical \
similarity alone.
- After merging classes, rewrite every relationship's "source"/"target" to \
use the FINAL merged class names -- never leave a relationship pointing at a \
class name that no longer exists in the merged set.
- Merge relationships that describe the same connection between the same \
pair of (merged) classes, even if named differently across groups; keep \
genuinely distinct relationships separate.
- Do not invent a class or relationship that isn't grounded in at least one \
of the groups below.

Write every definition/rationale value in the same language the input values \
are already in.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"classes": [{{"name": "...", "definition": "...", "category": \
"CONCEPT|ENTITY|EVENT|VALUE_OBJECT|ATTRIBUTE|DOCUMENT|RULE|RELATIONSHIP", \
"parent": "...", "rationale": "...", "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}}], \
"relationships": [{{"name": "...", "definition": "...", "source": "...", \
"target": "...", "category": "TAXONOMY|BUSINESS", "rationale": "...", \
"confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}}]}}

Candidate classes and relationships by group:
{groups}
"""


# Same map/reduce shape as CONSOLIDATION_PROMPT above, but for
# generate_schema()'s node_types/edge_types output instead of
# discover_ontology()'s classes/relationships -- the two prompts read almost
# identically because the underlying problem (merge same-concept types found
# independently by different groups, then re-point every edge/relationship at
# the merged names) is the same one either way.
SCHEMA_CONSOLIDATION_PROMPT = """You are a senior Ontology Architect. Multiple independent ontology-schema-\
generation passes were run over different sections of the SAME document, each \
seeing only its own section's text. Their candidate node_types and edge_types \
are listed below, grouped by which pass produced them. Merge these into ONE \
unified schema:

- Merge node_types that name the same real concept even if the name or \
wording differs across groups (pick or adapt the clearest name and \
description); keep genuinely distinct types separate rather than merging on \
lexical similarity alone.
- After merging node_types, rewrite every edge_type's "source"/"target" to \
use the FINAL merged node_type names -- never leave an edge_type pointing at \
a node_type name that no longer exists in the merged set.
- Merge edge_types that describe the same connection between the same pair \
of (merged) node_types, even if named differently across groups; keep \
genuinely distinct edge_types separate.
- Do not invent a node_type or edge_type that isn't grounded in at least one \
of the groups below.
- Every "name" value MUST stay a valid identifier (letters, digits, \
underscores only, starting with a letter or underscore); if merging forces a \
rename, keep it a valid identifier and disambiguate any collision with \
another type's identifier.

Write every "description" value in the same language the input values are \
already in.

Respond with ONLY valid JSON in this exact shape, no other text:
{{"node_types": [{{"name": "...", "description": "..."}}], \
"edge_types": [{{"name": "...", "description": "...", "source": "<node type name>", \
"target": "<node type name>"}}]}}

Candidate node_types and edge_types by group:
{groups}
"""


# Ported from scripts/prepare_goldenset/prompts.py (the standalone golden-QA
# generator's prompts) so app.goldenset can run the same document-grounded
# question/answer generation from a UI button, on one already-uploaded
# document, instead of only as an offline CLI pass over a folder of Markdown
# files. Question generation and answer generation stay two separate calls
# here for the same reason the original script splits them: asking one model
# call to both invent the questions and immediately prove them from the text
# tends to produce answers that quietly assume things the document doesn't
# actually say.
QUESTION_PROMPT = """You are designing a golden evaluation set for an ontology and
knowledge-graph question answering system.

Read the Markdown document and propose exactly {question_count} important,
non-duplicative questions that a real user would ask. Cover the document's main
facts, concepts, relationships, rules, conditions, exceptions, numbers, dates,
and multi-step connections where the text supports them. Prefer questions whose
answers matter for understanding or using the document, rather than trivia about
formatting or headings.

Use a balanced mix of these question types when the document permits it:
- entity: identify an important person, organization, object, or concept
- attribute: retrieve a property, number, date, condition, or definition
- relation: retrieve a direct relationship between entities or concepts
- multi_hop: combine two or more facts explicitly stated in the document
- list: return all matching items from a clearly bounded set
- boolean: verify or refute a claim using the document
- unanswerable: an important-sounding question that this document alone does not answer

At least one question should be unanswerable when {question_count} is 5 or more.
Do not answer the questions. Do not rely on outside knowledge. Write questions in
the document's primary language.

Return ONLY valid JSON in this exact shape:
{{"questions": [{{"id": "q001", "question": "...", "question_type":
"entity|attribute|relation|multi_hop|list|boolean|unanswerable",
"importance": "high|medium", "rationale": "why this tests an important
document capability"}}]}}

Source file: {source_file}

Document:
<document>
{document}
</document>
"""


ANSWER_PROMPT = """You are producing auditable gold answers for a document QA
evaluation set. Answer each question using ONLY the Markdown document below.

Rules:
1. If the document does not contain enough information, set answerable=false,
   answer to null, evidence to [], and briefly state what information is missing.
2. If answerable=true, every material claim in the answer must be supported by
   one or more evidence entries.
3. Each evidence.quote must be an exact, contiguous substring copied from the
   document. Keep it as short as possible while still proving the answer.
4. line_start and line_end are 1-based inclusive line numbers for that exact quote.
5. Do not use outside knowledge or fill gaps with plausible assumptions.
6. For list questions, include every item supported within the scope of the
   question. answer_facts should contain normalized atomic facts, not prose.
7. For multi-hop questions, answer_facts must expose every intermediate fact
   needed to derive the answer.
8. Preserve exact units, dates, thresholds, exceptions, and negation.
9. Write answer and notes in the document's primary language.

Return ONLY valid JSON in this exact shape:
{{"answers": [{{"id": "q001", "answerable": true, "answer": "... or null",
"answer_facts": [{{"subject": "...", "predicate": "...", "object": "..."}}],
"evidence": [{{"quote": "exact source substring", "line_start": 1,
"line_end": 1}}], "notes": "..."}}]}}

Questions:
{questions}

Source file: {source_file}

Document:
<document>
{document}
</document>
"""
