# Flexible Ontology Graph Schema Design

**Date:** 2026-09-04  
**Status:** Approved for implementation planning

## 1. Goal

Evolve the ontology graph from a graph whose nodes and edges share only a
small fixed set of fields into a governed hybrid model:

> Keep a stable common envelope for every graph object, manage semantic types
> by domain, and promote complex legal meaning into first-class nodes.

The design must support insurance terms, contracts, statutes, and other
structured documents without turning every document-specific observation into
a new database table or an unsearchable `detail` string.

## 2. Context and current state

The application currently follows this flow:

```text
document upload
  -> Markdown conversion and structure-aware chunks
  -> LLM schema generation
  -> node/edge extraction
  -> LadybugDB shared property graph
  -> type-filtered GraphRAG search and hop expansion
```

The current graph storage uses per-type NODE/REL tables in one shared
LadybugDB database. The physical node envelope contains `id`, `original_id`,
`label`, `detail`, `source_document`, `version`, and `embedding`; the physical
edge envelope contains endpoints plus `type`, `detail`, `source_document`, and
`version`. This stable envelope is useful for common retrieval and document
isolation, but it is insufficient as the only representation for legal
conditions, exceptions, rules, amounts, and evidence.

The repository already has domain schema files, schema versions, chunk
metadata, provenance-oriented prompts, schema convergence, and human review
queues. This design extends those boundaries instead of replacing them.

## 3. Design principles

### 3.1 Separate storage shape from semantic vocabulary

The database should enforce a small, stable physical envelope. A domain schema
defines which node and edge types are allowed and what they mean. A type may
define additional semantic fields without changing the physical envelope of
all other types.

The following are deliberately different concerns:

```text
physical envelope  -> common identifiers, provenance, version, embedding
domain vocabulary   -> Coverage, Norm, Exclusion, PAYS, HAS_EXCEPTION
validation shape    -> required fields, datatypes, cardinalities, endpoints
instance evidence   -> exact text, offsets, confidence, extraction run
```

### 3.2 Prefer canonical reusable types over document-specific types

An extracted type is eligible for the domain schema only when:

- existing types cannot express its meaning;
- it appears in more than one relevant document or improves a defined
  competency question;
- its boundary and relation semantics can be stated clearly; and
- a human can review its effect on existing extractions.

Synonyms and surface variations should be represented as aliases or
`surface_forms`, not as separate types.

### 3.3 Keep structural and substantive layers separate

`Document`, `PolicyVersion`, `Chapter`, `Article`, `Paragraph`, and `Item`
describe where text occurs. They must not become a catch-all container for
the meaning of that text.

Substantive concepts such as `Coverage`, `Benefit`, `Condition`, `Exclusion`,
`DefinedTerm`, `Party`, and `Norm` are separate nodes connected back to the
structural unit that states them.

### 3.4 Preserve source traceability as a first-class requirement

Every extracted node and edge that can affect an answer must be traceable to a
document version and a source span. Exact numbers, dates, durations,
qualifiers, and exceptions must be retained in evidence rather than only in a
free-form summary.

### 3.5 Model relations as verbs, but reify complex relations

Simple, direct relations use specific directional edge types such as `PAYS`,
`REQUIRES`, `EXCLUDES`, `DEFINES`, and `REFERS_TO`.

When a relation has multiple conditions, exceptions, temporal qualifiers,
interpretations, or evidence links, represent the relation as a `Norm`,
`Rule`, or other event/value node and connect that node to its participants.

## 4. Target model

### 4.1 Common graph envelope

The first implementation should make these fields available to every node and
edge where the LadybugDB physical schema allows it.

#### Required or operational fields

```text
id                  internal globally unique graph id
original_id         document-local extraction id
type                semantic type or relation type
label               canonical display label
source_document     document stem
document_version    schema/extraction version
extraction_run_id   extraction execution identifier
confidence          extraction confidence
```

#### Provenance fields

```text
source_section      Article/Paragraph/Item identifier
source_page         page when available
start_offset        character or token start offset
end_offset          character or token end offset
evidence_text       exact supporting text
```

#### Temporal and retrieval fields

```text
valid_from
valid_to
embedding
```

`document_version` is the existing versioning dimension and must remain
separate from legal validity dates. A schema/extraction version answers “which
representation was produced”; `valid_from`/`valid_to` answer “when does the
provision apply.”

Verified experimentally against the pinned `ladybug==0.19.1` engine:
`ALTER TABLE ... ADD <column> <type>` on an existing NODE/REL table, and
`MAP(STRING, STRING)`/`STRUCT(...)` column types, all work. So the common
envelope fields above can be added to every existing per-type table with
`ALTER TABLE ADD` rather than requiring a parallel "minimal envelope +
separate metadata node" fallback path. The implementation should default to
this direct approach; a dedicated `EvidenceSpan`/`TemporalExtent`/
`ExtractionRun` node is still the right shape for evidence that must be
independently cited or that has its own lifecycle (per §4.3's node-vs-property
rule), not a fallback forced by a physical-schema limitation that turned out
not to exist.

Two provenance fields need an explicit caveat because of how extraction
actually runs today:

- `source_section`/other chunk-derived provenance is only obtainable for
  documents ingested through the PDF `table_aware` path
  (`app.chunking.chunk_markdown_file`), which produces article-level chunk
  ids. A document parsed only through the generic `anydoc` path (`parser.py`)
  has no chunk structure at all, so these fields must be optional and absent
  (not fabricated) for such documents.
- Even for chunked documents, `extract_graph_from_chunks`'s reduce step
  (`_merge_group_graphs`) currently namespaces and merges nodes at the
  granularity of a whole *budget group* (several consecutive chunks packed
  together under `MAX_CHUNK_GROUP_CHARS`), not at the granularity of a single
  chunk/article. Attributing `source_section`/evidence offsets to the exact
  originating article therefore requires the extraction prompt and its parsed
  output to carry a chunk identifier per extracted node/edge from within a
  group, not just a group index — this is additional prompt/output-contract
  work beyond what today's group-level merge already tracks.

### 4.2 Domain schema contract

Each domain schema continues to define `node_types` and `edge_types`, and is
extended with optional type-specific declarations:

```json
{
  "schema_version": 2,
  "domain": "insurance_policy",
  "node_types": [
    {
      "name": "Norm",
      "description": "A rule stated by the policy.",
      "category": "RULE",
      "properties": {
        "modality": {"datatype": "string", "required": true},
        "operator": {"datatype": "string", "required": false}
      }
    }
  ],
  "edge_types": [
    {
      "name": "HAS_CONDITION",
      "description": "Norm points to a condition that must hold.",
      "source": "Norm",
      "target": "Condition",
      "properties": {}
    }
  ],
  "validation": {
    "required_provenance": true,
    "closed_world_types": false
  }
}
```

The existing minimal schema remains valid. New fields are additive and must
not make current general-document schemas unusable.

Note on physical representation: a node/edge type's table is one shared table
per type *name*, reused across every document and every domain schema (see
`graphdb.py`). If two domain schemas declare the same type name (e.g.
`Condition`) with different typed-property sets, per-type fixed columns would
conflict across schemas. Since `MAP(STRING, STRING)` columns are supported
(see §4.1), the implementation should store declared typed properties in one
open `properties MAP(STRING, STRING)` column per table rather than adding one
physical column per declared property — this sidesteps the conflict and keeps
schema-declared properties additive regardless of which domain schema wrote
them.

### 4.3 Property versus node decision rule

Use a property when the value is intrinsic to one node, has one simple value,
and is frequently used for filtering or display.

Use an edge property when the value qualifies one simple relation and does not
need independent links or provenance.

Use a node when the value or relation:

- has multiple conditions or exceptions;
- has its own lifecycle, temporal interval, or authority;
- must be cited independently;
- participates in more than one relation; or
- must be searched, validated, or explained as a distinct concept.

Examples:

```text
Benefit.amount = 50%
Party.role = Insured

(Party)-[:PAYS {basis: "가입금액"}]->(Benefit)

Norm -[:HAS_CONDITION]-> Condition
Norm -[:HAS_EXCEPTION]-> Exclusion
Norm -[:SUPPORTED_BY]-> EvidenceSpan
```

The last form is preferred once the payment rule has conditions, exceptions,
and source evidence.

## 5. Legal and insurance document model

### 5.1 Structural layer

```text
Document
  - HAS_VERSION -> PolicyVersion
  - CONTAINS -> Chapter
  - CONTAINS -> Article
  - CONTAINS -> Paragraph
  - CONTAINS -> Item
```

The exact structural types are domain-configurable. They should be retained
when they support navigation, cross-reference resolution, or provenance.

### 5.2 Semantic layer

```text
InsuranceProduct
  - HAS_COVERAGE -> Coverage

Coverage
  - COVERS -> CoveredEvent or CoveredObject
  - PAYS -> Benefit
  - REQUIRES -> Condition
  - EXCLUDES -> Exclusion

Party
  - HAS_ROLE -> PartyRole

Article
  - STATES -> Coverage, Norm, DefinedTerm, Exclusion
  - REFERS_TO -> Article
```

### 5.3 Norm/rule layer

For a sentence such as “암 진단 확정 시 가입금액의 50%를 지급하되, 계약일로부터
90일 이내에는 지급하지 않는다,” use:

```text
Article17 - STATES -> PaymentNorm
PaymentNorm
  - HAS_BEARER -> Insurer
  - HAS_ACTION -> BenefitPayment
  - HAS_CONDITION -> CancerDiagnosis
  - HAS_AMOUNT -> PaymentAmount
  - HAS_EXCEPTION -> WaitingPeriod
  - SUPPORTED_BY -> EvidenceSpan

WaitingPeriod.duration = 90일
PaymentAmount.expression = 가입금액의 50%
PaymentNorm.modality = OBLIGATION
```

This retains the rule structure without claiming that an LLM-derived graph is
itself a legally authoritative interpretation. Formal legal reasoning is a
separate later layer.

### 5.4 Defined terms and mentions

Do not collapse every appearance of a term into a global entity. Keep these
levels distinct when the use case requires traceability:

```text
DefinedTerm
  - DEFINED_BY -> Article
  - HAS_ALIAS -> TermAlias

Mention or EvidenceSpan
  - REFERS_TO -> DefinedTerm or Entity
```

Canonicalization across documents must be explicit and confidence-bearing.

## 6. Provenance and evidence contract

The extraction result must preserve at least:

```text
document_id
document_version
section_id
chunk_id
source offsets
quoted evidence
confidence
extraction_run_id
```

Evidence may be represented as common fields for simple objects or as an
`EvidenceSpan` node for multiple citations, annotations, or independent
extraction results. A generated summary must never replace the exact evidence
needed to verify a number, condition, exception, or date.

The design is compatible with W3C PROV-style provenance concepts, but this
phase does not require full PROV-O serialization.

## 7. Validation and schema governance

Validation has two layers:

1. **Structural validation**: valid identifiers, allowed types, endpoint
   compatibility, required fields, datatypes, and cardinalities.
2. **Evidence/semantic validation**: every important rule is grounded in the
   source, quantities and qualifiers are preserved, duplicate concepts are
   detected, and competency questions remain answerable.

The project may implement a JSON validation contract first and later export
or mirror it as SHACL shapes if RDF interoperability becomes a requirement.

Domain schema changes follow this lifecycle:

```text
candidate -> validate on calibration documents -> human review
          -> apply -> increment schema version -> re-extract affected data
```

Automatic addition of a type, property, or relation is not approval for
production use. Changes involving legal interpretation, obligation modality,
exceptions, or canonical identity require human review.

## 8. Compatibility and migration

- Existing `nodes`/`edges` API responses remain compatible during the first
  implementation phase.
- Existing `detail` values remain readable and may be used as fallback evidence
  until structured evidence is available.
- Existing document/schema versioning remains the isolation boundary for
  re-extraction and GraphRAG.
- Existing general and legal schemas must continue to load if they do not yet
  declare typed properties.
- Migration must be additive and idempotent. It must not silently reinterpret
  an old `detail` string as a structured legal fact without evidence review.
- Re-extraction of a document under the new schema is preferred to destructive
  in-place transformation of uncertain old facts.

## 9. Non-goals

This design does not include:

- a complete legal reasoning engine;
- automatic legal validity or legal advice;
- a universal ontology for every legal domain;
- unrestricted arbitrary JSON properties with no validation;
- full RDF/OWL export in the first implementation;
- deletion of old graph tables as part of schema evolution;
- a new cross-document legal question-answering UI.

## 10. Acceptance criteria for implementation

The implementation plan is complete when:

- common provenance and temporal metadata can be retained without breaking
  existing graph retrieval;
- domain schemas can declare optional typed properties and validation rules;
- a legal extraction can represent a rule with condition, exception, amount,
  and evidence without putting all meaning into `detail`;
- structural `Article`/`Paragraph` nodes remain navigable and are not semantic
  catch-all nodes;
- schema evolution is reviewable, versioned, and re-extractable;
- existing tests and current general-document workflows remain green; and
- at least one representative insurance-policy fixture answers predefined
  competency questions with source evidence.

