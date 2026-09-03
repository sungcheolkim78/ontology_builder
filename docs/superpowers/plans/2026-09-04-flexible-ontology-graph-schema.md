# Flexible Ontology Graph Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a governed hybrid graph schema with a stable common envelope, domain-specific typed properties, and first-class rule/evidence nodes for legal and insurance documents.

**Architecture:** Preserve the current shared LadybugDB graph and version boundary. Extend the schema contract and extraction output additively, store stable operational/provenance metadata in a common envelope, and represent complex legal semantics as typed nodes and edges. Keep existing API shapes and general-document behavior compatible while introducing explicit validation and a representative legal fixture.

**Tech Stack:** FastAPI, Python, LadybugDB/Cypher, JSON schema contracts, pytest, existing Vue/API contracts, existing chunking and GraphRAG pipeline.

**Spec:** `docs/superpowers/specs/2026-09-04-flexible-ontology-graph-schema-design.md`

## Global Constraints

- Keep the current `nodes`/`edges` API response shape backward-compatible in the first implementation phase.
- Keep `source_document` and schema/extraction `version` separate from legal `valid_from` and `valid_to`.
- Preserve exact source evidence for quantities, dates, durations, conditions, exceptions, and cross-references.
- Do not automatically approve domain schema changes involving legal interpretation, obligations, exceptions, or canonical identity.
- Do not treat `Article`, `Paragraph`, or another structural node as a semantic catch-all.
- Existing schemas without typed property declarations must continue to load and extract.
- Use additive, idempotent migration; prefer re-extraction for uncertain legacy facts.
- Run backend tests from `backend/` with `OPENROUTER_API_KEY=dummy .venv/bin/python -m pytest tests -q`.
- Preserve unrelated worktree changes and stage only files belonging to this plan.

## File map

- Modify: `backend/app/graphdb.py` — common graph envelope, typed metadata persistence, and compatibility reads.
- Modify: `backend/app/ontology.py` — schema normalization, extraction normalization, evidence metadata, and version-aware persistence.
- Modify: `backend/app/prompts.py` — additive legal extraction contract for structured properties and evidence.
- Modify: `backend/app/main.py` — only if API serialization or validation endpoints need explicit exposure.
- Create: `backend/app/schema_validation.py` — isolated validation of domain schema declarations and extracted graph shape.
- Modify: `backend/tests/test_graphdb.py` — persistence and backward-compatibility coverage.
- Modify: `backend/tests/test_ontology.py` — schema normalization and extraction output coverage.
- Create: `backend/tests/test_schema_validation.py` — validation contract tests.
- Create: `backend/tests/fixtures/legal_policy_sample.md` — small representative insurance-policy fixture.
- Create: `backend/tests/fixtures/legal_policy_expected.json` — expected rule, condition, exception, and evidence assertions.
- Modify: `docs/ontology/entity_relation_extract_prompt.md` and related ontology docs only when implementation changes the documented extraction contract.

---

### Task 1: Freeze the compatibility contract and representative competency questions

**Files:**
- Create: `backend/tests/fixtures/legal_policy_sample.md`
- Create: `backend/tests/fixtures/legal_policy_expected.json`
- Create: `docs/superpowers/plans/2026-09-04-flexible-ontology-graph-schema.md` (this plan is the design reference)
- Modify: `docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md` only if fixture terminology exposes a contradiction

**Interfaces:**
- Produces: a fixed sample document and assertions for coverage, payment amount, waiting period, exclusion, article location, and evidence traceability.

- [ ] Write a short Korean insurance-policy fixture containing a defined term, article/paragraph structure, one payment rule, one condition, one exception, one amount, one duration, and one cross-reference.
- [ ] Define 5–7 competency questions in the expected fixture metadata, including “어떤 조건에서 얼마를 지급하는가?”, “어떤 기간 동안 지급하지 않는가?”, and “근거 조항은 무엇인가?”.
- [ ] Record which answers must be graph-structured and which may remain source-text retrieval.
- [ ] Review that the fixture does not imply legal authority beyond extraction and traceability.
- [ ] Run the existing focused tests before changing implementation to record the baseline.

**Commit:** `git commit -m "Add legal graph schema acceptance fixture"`

### Task 2: Add schema normalization and typed-property validation

**Files:**
- Create: `backend/app/schema_validation.py`
- Create: `backend/tests/test_schema_validation.py`
- Modify: `backend/app/ontology.py` — schema loading/normalization call sites

**Interfaces:**
- Consumes: existing `node_types`/`edge_types` JSON, optional `properties`, `category`, and `validation` declarations.
- Produces: `normalize_schema(schema) -> dict`, `validate_schema(schema) -> list[dict]`, and a normalized schema contract that preserves legacy inputs.

- [ ] Write failing tests for a legacy schema with only names/descriptions.
- [ ] Write failing tests for valid typed properties: datatype, required flag, unit, and description.
- [ ] Write failing tests for invalid identifiers, unknown endpoint types, unsupported datatypes, and duplicate property declarations.
- [ ] Implement the smallest normalizer that fills additive defaults without changing legacy semantics.
- [ ] Implement validation errors as structured, human-readable records rather than raw exceptions where possible.
- [ ] Add tests proving normalization is deterministic and idempotent.
- [ ] Run `OPENROUTER_API_KEY=dummy .venv/bin/python -m pytest tests/test_schema_validation.py tests/test_ontology.py -q`.

**Commit:** `git commit -m "Add governed typed-property schema validation"`

### Task 3: Extend extraction output with structured metadata and evidence

**Files:**
- Modify: `backend/app/prompts.py` — shared extraction output contract and legal prompt
- Modify: `backend/app/ontology.py` — parse/normalize extracted nodes and edges
- Modify: `backend/tests/test_ontology.py`

**Interfaces:**
- Consumes: normalized domain schema and source chunk metadata.
- Produces: backward-compatible nodes/edges with optional `properties`, `evidence`, `source_section`, offsets, confidence, and temporal fields.

- [ ] Add failing parser tests for missing optional metadata, malformed property maps, invalid evidence offsets, and unknown typed properties.
- [ ] Extend the extraction prompt so exact figures and qualifying wording remain available as structured properties plus quoted evidence.
- [ ] Keep `detail` as a compatibility/fallback field; do not remove or silently reinterpret it.
- [ ] Normalize evidence from chunk/section context into a stable shape with `document_id`, `document_version`, `chunk_id`, offsets, quote, and confidence.
- [ ] Reject or mark for review any extracted property that is not declared by the active domain schema.
- [ ] Preserve deterministic ordering of nodes, edges, properties, and evidence arrays.
- [ ] Run focused ontology tests and prompt JSON-contract tests.

**Commit:** `git commit -m "Preserve structured legal properties and source evidence"`

### Task 4: Implement legal rule, condition, exception, and evidence modeling

**Files:**
- Modify: `backend/app/ontology.py` — legal graph normalization/extraction helpers
- Modify: `backend/app/prompts.py` — legal node/edge guidance
- Modify: `backend/tests/test_ontology.py`
- Modify: `backend/tests/fixtures/legal_policy_expected.json`

**Interfaces:**
- Consumes: legal schema declarations and structured extraction output.
- Produces: `Article`/`Paragraph` structural nodes; `Norm`, `Condition`, `Exclusion`, `Benefit`, `DefinedTerm`, and `EvidenceSpan` semantic nodes when supported by the text.

- [ ] Write failing tests proving an article can state multiple independent semantic nodes.
- [ ] Write failing tests proving a payment rule can connect to bearer, action/benefit, condition, amount, exception, and evidence.
- [ ] Add explicit direction and endpoint checks for `STATES`, `HAS_CONDITION`, `HAS_EXCEPTION`, and `SUPPORTED_BY`.
- [ ] Use a first-class `Norm`/`Rule` node when a relation has multiple qualifiers instead of packing all qualifiers into one edge property.
- [ ] Keep simple intrinsic values as typed properties and simple relation qualifiers as edge properties.
- [ ] Add a guard that prevents structural nodes from receiving semantic catch-all `detail` content as their only representation.
- [ ] Run the representative fixture assertions without requiring a live LLM by testing normalized model payloads.

**Commit:** `git commit -m "Model legal norms conditions exceptions and evidence"`

### Task 5: Extend LadybugDB persistence without breaking existing graph queries

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: normalized node/edge dictionaries with optional structured metadata.
- Produces: persisted common envelope and structured metadata; existing `load_graph`, type search, edge search, and hop expansion remain callable with current signatures/defaults.

- [ ] Write failing round-trip tests for common provenance fields, typed property payloads, and temporal fields.
- [ ] Write failing tests proving old graph rows with only `detail` still load.
- [ ] Write failing tests proving document/version deletion removes all new metadata together with graph rows.
- [ ] Choose the least disruptive physical representation supported by LadybugDB: common scalar columns first; dedicated metadata/evidence nodes where heterogeneous columns are unsafe.
- [ ] Add explicit serialization/deserialization helpers so database representation is not coupled to API response formatting.
- [ ] Keep internal primary-key handling separate from application-level `original_id` lookup.
- [ ] Preserve transactionality and document/version scoping for writes, re-extraction, embedding updates, and deletion.
- [ ] Run all graphdb tests, including deterministic ordering and fresh-database cases.

**Commit:** `git commit -m "Persist governed graph metadata in LadybugDB"`

### Task 6: Add graph-shape and evidence validation to the extraction pipeline

**Files:**
- Modify: `backend/app/schema_validation.py`
- Modify: `backend/app/ontology.py`
- Create: `backend/tests/test_schema_validation.py` additions

**Interfaces:**
- Consumes: normalized schema, graph payload, and source document/chunk metadata.
- Produces: validation report with structural issues, evidence issues, missing elements, and competency-question readiness.

- [ ] Write failing tests for wrong edge endpoints, missing required properties, missing evidence, invalid numeric units, and duplicate canonical nodes.
- [ ] Implement structural validation before database persistence.
- [ ] Implement evidence validation that flags missing/weak/incorrect evidence without inventing corrections.
- [ ] Add a rule that every production legal `Norm`, `Condition`, `Exclusion`, and `Benefit` relevant to an answer has source evidence.
- [ ] Preserve existing validation categories and add new categories additively.
- [ ] Return actionable review records suitable for the existing pending-review workflow.
- [ ] Run focused validation and ontology tests.

**Commit:** `git commit -m "Validate graph shape and legal evidence"`

### Task 7: Integrate domain schema versions and review workflow

**Files:**
- Modify: `backend/app/ontology.py`
- Modify: `backend/app/main.py` only where existing schema/domain endpoints serialize new fields
- Modify: `backend/tests/test_main.py`
- Modify: `backend/tests/test_ontology.py`
- Modify: `docs/ontology/domain_schema_convergence.md`

**Interfaces:**
- Consumes: normalized schema, validation report, pending-review records, and existing schema version APIs.
- Produces: versioned domain schemas with typed properties/validation metadata and explicit human-review changes.

- [ ] Write failing endpoint tests for loading legacy domain schemas and returning normalized additive fields.
- [ ] Write failing tests proving a candidate type/property change is queued rather than silently applied.
- [ ] Extend manifest/history records with schema contract version, affected documents, validation summary, and review status.
- [ ] Ensure applying an approved schema change increments the relevant schema version and leaves old versions readable.
- [ ] Ensure document extraction records the schema version used for the graph.
- [ ] Update documentation to distinguish domain schema version from document validity dates.
- [ ] Run backend API and ontology test suites.

**Commit:** `git commit -m "Version and review domain graph schema changes"`

### Task 8: Re-extract and verify the representative legal document

**Files:**
- Modify: `backend/tests/fixtures/legal_policy_expected.json` as needed for verified output
- Modify: `docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md`
- Modify: `docs/ontology/entity_relation_extract_prompt.md`

**Interfaces:**
- Consumes: final domain schema, legal fixture, extraction pipeline, and validation report.
- Produces: verified example output and documentation of the supported legal graph pattern.

- [ ] Run the extraction path against the fixture with the configured test/dummy model strategy.
- [ ] Verify that structural nodes, semantic nodes, rule qualifiers, and evidence spans are all present.
- [ ] Verify all competency questions return a graph path plus source evidence, or are explicitly marked unavailable.
- [ ] Verify re-extraction is idempotent for the same document/version.
- [ ] Verify old general-document fixtures still pass without legal-only nodes.
- [ ] Record any unsupported interpretation as a validation warning rather than forcing a type.
- [ ] Run the complete backend suite and `git diff --check`.

**Commit:** `git commit -m "Verify legal graph schema with representative policy"`

## Final verification checklist

- [ ] `OPENROUTER_API_KEY=dummy .venv/bin/python -m pytest tests -q` passes from `backend/`.
- [ ] Legacy schemas and legacy graph rows remain readable.
- [ ] New graph metadata is scoped by document and version.
- [ ] A legal rule with amount, condition, exception, temporal qualifier, and evidence round-trips through LadybugDB.
- [ ] Structural article nodes do not act as semantic catch-alls.
- [ ] Domain schema changes remain versioned and human-reviewable.
- [ ] `git diff --check` passes.
- [ ] Only requested documentation and implementation files are staged in each commit.

