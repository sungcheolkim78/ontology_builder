"""All LLM prompt templates used by app.ontology and app.preprocess.goldenset, kept in
one module so the prompt text (and the design-rationale comments attached to
each one) can be read/edited independently of the extraction/storage logic
that fills them in and parses their output.

Every app.ontology.*_PROMPT constant below (everything except QUESTION_PROMPT/
ANSWER_PROMPT, still owned by app.preprocess.goldenset and untouched by the
split below) is STATIC instruction text only -- no `{document}`/`{schema}`/
etc. placeholder for the actual per-call content, EXTRACT_PROMPT's `{schema}`
being the one exception (see its own comment). The caller builds a
[SystemMessage(content=<this constant, filled in if it has a placeholder>),
HumanMessage(content=<the per-call variable text>)] pair and passes that list
to invoke_with_telemetry, instead of interpolating everything into one giant
formatted string the way this module used to. This exists for two reasons:

- Every one of these operations' prompts also gets shorter as a direct
  effect, since the previous single-string design required literally
  restating "Document:\n{document}" (and, for a few, "Ontology schema:\n
  {schema}", "Extracted graph:\n{graph}", etc.) inside the same block as the
  instructions -- now those live only in the human message, built in code.
- app.ontology.generate_schema's *_from_chunks functions call their
  single-document function once per chunk group with IDENTICAL instructions
  and only the group's own text differing -- putting the identical part in
  a stable system message (and, for generate_schema/extract_graph, folding
  in whatever else is identical across an entire *_from_chunks call --
  discovery's hint, extract's schema -- rather than just the literal prompt
  constant) maximizes how much of the request is a byte-identical prefix
  across those repeated calls, which is what lets a provider with prompt
  caching (Anthropic, DeepSeek, and others OpenRouter passes it through
  for) skip re-processing that prefix on group 2, 3, .... Whether a given
  OpenRouter-routed model actually honors this depends on that model's own
  provider support -- this only sets up the *shape* that makes caching
  possible, it can't itself guarantee a hit.

Every prompt below also now relies on get_chat_model's response_format=
{"type": "json_object"} (app/llm/chat.py's _JSON_OPERATIONS) rather than
prose ("Respond with ONLY valid JSON, no other text") to guarantee
syntactically valid JSON back -- the prose instruction still appears, in
shortened form, because OpenAI-compatible json_object mode requires the
literal word "JSON" to appear somewhere in the messages or the provider
rejects the request; what's gone is the previous fully-worked-out example
object (down to a compact `key: type` shape), since json_object mode only
guarantees valid JSON syntax, not this exact schema, so the prompt still has
to name every key -- it just no longer has to demonstrate one with a full
example value for each."""

# Shared by every schema-generation prompt variant below -- the identifier rule
# and output shape are policy, not something that should vary per document type.
_SCHEMA_OUTPUT_INSTRUCTIONS = """Rules:
- Ground every edge_type in a concrete pair of entities already in the document text; drop any you can't. Collapse near-duplicate edge_types describing the same relationship into one (e.g. don't propose both WRITTEN_BY and AUTHORED_BY).
- Prefer every node_type to be the source or target of at least one edge_type -- one with none always produces disconnected nodes at extraction time. Add the missing edge_type where the document supports it, but a genuinely standalone type (e.g. Date, Event, DocumentSection) may stay edgeless.
- Name edge_types as verbs/verb phrases (COVERS, REQUIRES, EXCLUDES, PAYS), not nouns. Avoid vague catch-alls (RELATED_TO, ASSOCIATED_WITH, HAS_INFO) unless nothing more specific applies. Keep a taxonomic relationship (a category containing subtypes) on its own edge_type (IS_A, SUBTYPE_OF), separate from business-meaning edge_types like COVERS/BELONGS_TO.
- "name" (node_types and edge_types) must be a valid identifier: letters/digits/underscores only, starting with a letter or underscore -- transliterate a non-English name into ASCII, and check it doesn't collide with another type's identifier.
- Write "description" in the document's own language regardless of "name"'s language. For each edge_type, state direction inside the description itself (e.g. "WRITTEN_BY: source is the document, target is its author").
- Target roughly 5-12 node_types and 5-15 edge_types; go beyond only if the document has that much genuine variety, not by splitting finer to fill the range. Return empty arrays if nothing fits rather than inventing types. Prefer canonical, reusable names over document-specific one-offs, unless the document defines a term precisely enough that a generic name would lose that precision.

Output a single JSON object, no other text, with exactly these keys:
{"node_types": [{"name": str, "description": str}], "edge_types": [{"name": str, "description": str, "source": "<node type name>", "target": "<node type name>"}]}"""

SCHEMA_PROMPT = """Propose an ontology schema (node_types, edge_types) for extracting entities/relationships from a general-purpose document (report, article, manual, memo, meeting notes).

- node_types: categories the document returns to more than once -- people/organizations (and roles, e.g. author, customer, department); named concepts/topics/products/systems discussed repeatedly; places; events/dates/periods that matter to the content.
- edge_types: how those categories connect -- structural/hierarchical (part of, belongs to, contains), attribution (authored by, owns, responsible for), causal/sequential (causes, leads to, precedes, follows), or plain association where nothing more precise applies.
- Favor a few types that each cover many instances over many narrow one-off types. Use the document's own terminology over generic labels ("Entity", "RelatedTo") wherever it's more specific; fall back to generic only for content that genuinely fits nothing narrower. Only propose types the document actually supports.

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

# For statutes, contracts, and insurance policies/terms: documents built from a
# formal internal structure (chapters/articles/paragraphs/items, e.g. 장/절/조/항/호)
# and explicitly defined terms reused throughout. The generic SCHEMA_PROMPT above
# tends to propose only surface-level named entities and misses definitions,
# obligations/conditions/exclusions, and cross-references between provisions --
# this variant asks the LLM to look for those specifically, without forcing any of
# them into a schema that doesn't actually have them.
LEGAL_SCHEMA_PROMPT = """Propose an ontology schema (node_types, edge_types) for extracting entities/relationships from a legal or insurance-style document (statute, contract, insurance policy/terms) with a formal internal structure (chapters/articles/paragraphs/items, e.g. 장/절/조/항/호) and defined terms reused throughout.

- Look specifically for: defined terms from a definitions clause (kept separate from the entities that later use them); parties/roles (e.g. insurer/policyholder/insured/beneficiary); obligations/rights/benefits each party has; conditions and exclusions that trigger or bar them (e.g. waiting periods, 면책사유); cross-references between the document's own provisions (e.g. "제15조에 따라", "전항에도 불구하고"); and a structural unit (e.g. "Article") as its own node_type only when cross-references between them are frequent enough to be worth navigating, not just because the document has numbered sections. Only propose types the document actually supports, using its own terminology.
- Keep a structural node_type (Article, Clause, Schedule, Appendix, ...) purely structural -- it identifies *where* something is written, not *what* it says. A single article routinely states more than one kind of thing (e.g. both a payment condition and an exclusion in the same clause), so never let it become a catch-all for that substantive content. Pull the actual substance into its own concept node_types (parties, benefits, conditions/events, exclusions, defined terms) and connect each back to its article with an edge_type (e.g. STATES, DEFINES, TRIGGERS).
- For a simple, single relationship, propose a direct edge_type (PAYS, REQUIRES, EXCLUDES). But when one provision states a relationship with several conditions/exceptions/qualifiers at once (e.g. "암 진단 확정 시 가입금액의 50%를 지급하되, 계약일로부터 90일 이내에는 지급하지 않는다" -- one payment obligation with a bearer, a condition, an amount, AND an exception), don't squeeze all of that onto one edge's properties. Instead propose a node_type for the rule itself (Norm, Rule, PaymentNorm) and connect it to each participant with its own edge_type (HAS_BEARER, HAS_CONDITION, HAS_AMOUNT, HAS_EXCEPTION), so every qualifier stays independently searchable and citable.

""" + _SCHEMA_OUTPUT_INSTRUCTIONS

SCHEMA_PROMPTS = {
    "general": SCHEMA_PROMPT,
    "legal": LEGAL_SCHEMA_PROMPT,
}

# The one prompt in this module that keeps a `.format()` placeholder
# ({schema}) rather than moving everything into the human message: a
# document's active schema is per-document, not a document-independent
# constant the way every other prompt's static instructions are, but it IS
# identical across every group of one extract_graph_from_chunks call (unlike
# the group's own document text) -- filling it in here, so it lands in the
# system message alongside the instructions, still gives every group of that
# one call a byte-identical system message, maximizing the cacheable prefix
# for exactly the repeated-call pattern this module has.
EXTRACT_PROMPT = """Using this ontology schema:
{schema}

Extract entities and relationships from the document that conform to this schema.

- "type": the node_type/edge_type name from the schema.
- "id" (nodes only): a short, stable slug unique within this document -- your own choice; it never has to appear in the text verbatim.
- "label" (nodes only): the entity's canonical surface form as it appears in the document -- prefer the first occurrence, or the fullest form if a later mention adds detail the first lacks.
- Coreference: the same real-world entity mentioned multiple times under different names/aliases/abbreviations/pronouns (e.g. "김철수" and "김 대표", or a company and "동사") gets exactly ONE node, never one per mention.
- "detail": 1-2 sentences of document-specific nuance (exact conditions, exceptions, figures, dates, phrasing) not captured by label/type alone. Every claim must be directly supported by the text -- no inference, summary judgment, or outside knowledge. For a condition/exception/figure/date, quote the document's own wording verbatim (with its unit, e.g. "90일 이내", "가입금액의 50%") rather than paraphrasing it; paraphrase only the surrounding context needed to make the quote make sense. Omit if there's nothing beyond the label worth adding.
- "properties": only when the schema declares a properties object for this exact type (name -> {{"datatype": ..., "required": ...}}) -- include a value, as a plain string even for numeric/boolean/date (e.g. "50" not 50), for every declared property the text supports; omit properties the text doesn't clearly support; never invent an undeclared property name.
- "evidence": the single shortest verbatim span (clause or sentence) copied character-for-character that most directly supports this node/edge -- checked against the document text afterward, and discarded if not found verbatim, so copy exactly. Omit if no single span stands out more than the surrounding text.
- "source_section": if the document has a bracketed section label on its own line (e.g. "[주계약 > 제17조(보험금의 지급)]"), copy the nearest one exactly for wherever this node/edge's evidence appears. Omit if there are no such labels, or you're unsure which applies.
- "confidence": "HIGH" (stated directly/unambiguously), "MEDIUM" (minor inference, e.g. resolving a pronoun), or "LOW" (plausible but uncertain).

Output a single JSON object, no other text, with exactly these keys:
{{"nodes": [{{"id": str, "label": str, "type": "<node type from schema>", "detail": str, "properties": {{}}, "evidence": str, "source_section": str, "confidence": "HIGH|MEDIUM|LOW"}}], "edges": [{{"source": "<node id>", "target": "<node id>", "type": "<edge type from schema>", "detail": str, "properties": {{}}, "evidence": str, "source_section": str, "confidence": "HIGH|MEDIUM|LOW"}}]}}"""


# Adapted from docs/ontology/ontology_validation_prompt.md (the team's
# ontology-validation agent spec) into a single-call JSON-output prompt, the
# same shape every other prompt in this module uses. Keeps that doc's
# validation dimensions (semantic/structural/provenance/rule/extraction/
# consistency), severity scale, and "flag, don't fix" rule; folds its
# competency-question check in by having the LLM derive a handful of
# competency questions from the schema itself, since this app has no
# separately-authored competency-question list to validate against.
VALIDATION_PROMPT = """You are a senior Ontology Validator and Knowledge Graph Quality Engineer. Validate the ontology schema and the extracted graph against the source document. Identify errors, omissions, contradictions, and ontology weaknesses -- do NOT silently fix anything; only report what should change and why.

Check:
- Semantic: is every node/edge type well-defined? Similar concepts wrongly split, or different concepts wrongly merged? Correct edge directions/meanings?
- Structural: for each edge instance, do its source/target node types match what the schema's edge_type declares?
- Provenance: is a node/edge's "detail" actually supported by the document text, or invented/overstated? Flag MISSING_EVIDENCE, INCORRECT_EVIDENCE, WEAK_EVIDENCE.
- Rule/figures: has any numerical or qualifying detail (condition, exception, threshold, date, amount) been lost or altered from the source?
- Completeness: does the document have important entities/relationships/attributes/events/rules the schema or extraction missed? Flag MISSING_ENTITY, MISSING_RELATIONSHIP, MISSING_ATTRIBUTE, MISSING_EVENT, MISSING_RULE.
- Consistency: flag CONTRADICTION, DUPLICATE_ENTITY, DUPLICATE_RELATION, AMBIGUOUS_ENTITY, AMBIGUOUS_RELATION.

Derive 3-5 realistic competency questions a user of this document would ask (grounded in what the schema's own types/relationships suggest), then say whether the current schema+graph can answer each, and if not, what node/edge types or instances are missing.

Assign each issue a severity: CRITICAL (materially incorrect knowledge), HIGH (important business meaning lost/incorrect), MEDIUM (significant modeling/extraction weakness), LOW (minor inconsistency), INFO (non-critical observation).

Write every text field in the document's own language.

Output a single JSON object, no other text, with exactly these keys:
{"validation_summary": {"ontology_valid": bool, "extraction_valid": bool, "provenance_valid": bool, "competency_questions_answerable": bool, "overall_quality": str}, "issues": [{"severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO", "category": str, "description": str, "affected_element": str, "evidence": str, "recommended_action": str}], "missing_elements": {"classes": [str], "relationships": [str], "attributes": [str], "events": [str], "rules": [str]}, "contradictions": [str], "ambiguities": [str], "competency_questions": [{"question": str, "answerable": bool, "missing_elements": [str], "evidence": str}], "recommended_changes": [str]}"""


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
DISCOVERY_PROMPT = """You are a senior Ontology Architect and Knowledge Engineer. Discover a candidate domain ontology from the document -- this is NOT final entity extraction. Identify the conceptual structure needed to represent the document's meaning, suitable for knowledge graph construction, GraphRAG, and question answering. The result is a candidate for a person to review and refine, not a final one.

Principles:
- Model meaning, not vocabulary -- a class needs independent semantic meaning, not just a frequently-appearing noun.
- Separate taxonomy relationships (isA/subClassOf/partOf) from business relationships (covers/requires/pays/excludes/appliesTo/...); never mix them in one relationship.
- Prefer meaningful directional relationship names (covers, requires, triggers, causes, belongsTo, definedBy, derivedFrom) over vague ones (relatedTo, associatedWith, hasInformation) unless nothing better fits.
- Note temporal semantics (effective/expiration dates, versions, event sequence) and provenance wherever the document supports it.
- Minimize unnecessary complexity -- a minimal, expressive ontology beats a maximal one.
- Don't invent classes/relationships/concepts the document doesn't support; don't silently merge concepts on lexical similarity alone -- mark confidence UNKNOWN or add a warning instead.

Classify each class as CONCEPT, ENTITY, EVENT, VALUE_OBJECT, ATTRIBUTE, DOCUMENT, RULE, or RELATIONSHIP; each relationship as TAXONOMY or BUSINESS. Generate 10-20 competency questions the eventual ontology should be able to answer.

Write every definition/description/rationale value in the document's own language.

Output a single JSON object, no other text, with exactly these keys:
{"domain_model": {"domain": str, "subdomains": [str], "document_types": [str], "business_processes": [str], "major_actors": [str]}, "classes": [{"name": str, "definition": str, "category": "CONCEPT|ENTITY|EVENT|VALUE_OBJECT|ATTRIBUTE|DOCUMENT|RULE|RELATIONSHIP", "parent": str, "rationale": str, "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}], "relationships": [{"name": str, "definition": str, "source": str, "target": str, "category": "TAXONOMY|BUSINESS", "rationale": str, "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}], "attributes": [{"name": str, "defined_on": str, "definition": str, "datatype": str, "unit": str, "required": bool, "rationale": str}], "events": [{"name": str, "definition": str, "trigger": str, "affected_entities": [str]}], "rules": [{"name": str, "description": str, "conditions": [str], "consequences": [str], "exceptions": [str]}], "terminology": [{"canonical_term": str, "synonyms": [str], "abbreviations": [str], "source_terms": [str]}], "competency_questions": [str], "warnings": [str]}"""


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
EVOLUTION_PROMPT = """You are a senior Ontology Governance and Evolution Architect. The existing ontology schema is authoritative -- don't propose changing it just because a new term appears in the document. Given the document, its current schema, its current extracted graph, and a validation report that already found problems, propose a minimal, disciplined set of changes that fix flagged issues and fill genuinely missing pieces -- not a redo from scratch.

For every schema-level candidate (a node_type/edge_type to add, fix, or retire) and every instance-level candidate (a node/edge to add, grounded in the document, filling a MISSING_ENTITY/MISSING_RELATIONSHIP/MISSING_ATTRIBUTE/MISSING_EVENT/MISSING_RULE the validation report flagged), choose exactly one decision:
- ADD: independent meaning, not representable by an existing element, occurs in meaningful context, supported by evidence.
- MODIFY: an existing element's definition is demonstrably incomplete/incorrect -- state what it was and what it becomes.
- MERGE: two elements are semantically identical (not just lexically similar) -- name the merge target.
- DEPRECATE: an existing element is obsolete -- never propose deleting it outright.
- REJECT: a document-specific one-off phrase, an already-covered synonym, insufficient evidence, or added complexity without benefit.
- NEEDS_HUMAN_REVIEW: the change would materially affect business semantics, regulatory/contractual interpretation, class hierarchy, relationship semantics, or cardinality -- never apply these automatically.

Only propose ADD for a graph instance when you can point to the specific supporting text (put it in "evidence"). Only propose MERGE/DEPRECATE for a graph instance if the validation report specifically flagged a duplicate/contradiction; prefer schema-level changes over expanding the graph indiscriminately.

Write every reason/evidence value in the document's own language.

Output a single JSON object, no other text, with exactly these keys:
{"evolution_summary": {"changes_proposed": int, "human_review_required": bool}, "changes": [{"change_id": str, "decision": "ADD|MODIFY|MERGE|DEPRECATE|REJECT|NEEDS_HUMAN_REVIEW", "element_type": "node_type|edge_type|node|edge", "element": {}, "reason": str, "evidence": str, "confidence": "HIGH|MEDIUM|LOW"}]}

element shape by element_type:
- node_type: {"name": str, "description": str}
- edge_type: {"name": str, "description": str, "source": "<node type name>", "target": "<node type name>"}
- node: {"id": str, "label": str, "type": "<node type name, existing or newly proposed>", "detail": str}
- edge: {"source": "<node id, existing or newly added in this response>", "target": "<node id>", "type": str, "detail": str}"""


# The consolidation call only ever sees name/definition/category(/source/
# target) -- never full group text or instance data -- so its input size
# stays flat regardless of how many groups or how long the document is, and
# it can't accidentally re-derive a class/relationship from raw text a
# per-group pass already read once.
CONSOLIDATION_PROMPT = """You are a senior Ontology Architect. Multiple independent ontology-discovery passes ran over different sections of the SAME document, each seeing only its own section. Their candidate classes/relationships are listed below by group. Merge into ONE unified set:

- Merge classes naming the same real concept even if wording differs across groups (pick/adapt the clearest name+definition); keep genuinely distinct concepts separate rather than merging on lexical similarity alone.
- After merging classes, rewrite every relationship's source/target to the FINAL merged class names -- never leave one pointing at a class that no longer exists.
- Merge relationships describing the same connection between the same (merged) class pair, even if named differently; keep genuinely distinct relationships separate.
- Don't invent a class/relationship not grounded in at least one group below.

Write every definition/rationale in the same language the input values are already in.

Output a single JSON object, no other text, with exactly these keys:
{"classes": [{"name": str, "definition": str, "category": "CONCEPT|ENTITY|EVENT|VALUE_OBJECT|ATTRIBUTE|DOCUMENT|RULE|RELATIONSHIP", "parent": str, "rationale": str, "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}], "relationships": [{"name": str, "definition": str, "source": str, "target": str, "category": "TAXONOMY|BUSINESS", "rationale": str, "confidence": "HIGH|MEDIUM|LOW|UNKNOWN"}]}"""


# Same map/reduce shape as CONSOLIDATION_PROMPT above, but for
# generate_schema()'s node_types/edge_types output instead of
# discover_ontology()'s classes/relationships -- the two prompts read almost
# identically because the underlying problem (merge same-concept types found
# independently by different groups, then re-point every edge/relationship at
# the merged names) is the same one either way.
SCHEMA_CONSOLIDATION_PROMPT = """You are a senior Ontology Architect. Multiple independent ontology-schema-generation passes ran over different sections of the SAME document, each seeing only its own section. Their candidate node_types/edge_types are listed below by group. Merge into ONE unified schema:

- Merge node_types naming the same real concept even if wording differs across groups (pick/adapt the clearest name+description); keep genuinely distinct types separate rather than merging on lexical similarity alone.
- After merging node_types, rewrite every edge_type's source/target to the FINAL merged node_type names -- never leave one pointing at a node_type that no longer exists.
- Merge edge_types describing the same connection between the same (merged) node_type pair, even if named differently; keep genuinely distinct edge_types separate.
- Don't invent a node_type/edge_type not grounded in at least one group below.
- Every "name" must stay a valid identifier (letters/digits/underscores, starting with a letter or underscore); if merging forces a rename, keep it valid and disambiguate any collision.

Write every "description" in the same language the input values are already in.

Output a single JSON object, no other text, with exactly these keys:
{"node_types": [{"name": str, "description": str}], "edge_types": [{"name": str, "description": str, "source": "<node type name>", "target": "<node type name>"}]}"""


# Ported from scripts/prepare_goldenset/prompts.py (the standalone golden-QA
# generator's prompts) so app.preprocess.goldenset can run the same document-grounded
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
