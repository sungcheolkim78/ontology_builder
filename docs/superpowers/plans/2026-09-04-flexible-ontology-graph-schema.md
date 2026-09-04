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
- Modify: `docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md` only if fixture terminology exposes a contradiction

**Interfaces:**
- Produces: a fixed sample document and assertions for coverage, payment amount, waiting period, exclusion, article location, and evidence traceability.

- [x] Write a short Korean insurance-policy fixture containing a defined term, article/paragraph structure, one payment rule, one condition, one exception, one amount, one duration, and one cross-reference.
- [x] Define 5–7 competency questions in the expected fixture metadata, including “어떤 조건에서 얼마를 지급하는가?”, “어떤 기간 동안 지급하지 않는가?”, and “근거 조항은 무엇인가?”.
- [x] Record which answers must be graph-structured and which may remain source-text retrieval. (`answer_shape` per competency question; cq6 is `source_text`, the rest are `graph`)
- [x] Review that the fixture does not imply legal authority beyond extraction and traceability. (see the fixture's top-level `note`)
- [x] Run the existing focused tests before changing implementation to record the baseline. (279 passed on `tests/` before Task 3+ changes)

**Commit:** `git commit -m "Add legal graph schema acceptance fixture"`

### Task 2: Add schema normalization and typed-property validation

**Files:**
- Create: `backend/app/schema_validation.py`
- Create: `backend/tests/test_schema_validation.py`
- Modify: `backend/app/ontology.py` — schema loading/normalization call sites
- Modify: `backend/app/prompts.py` — `SCHEMA_PROMPT`/`LEGAL_SCHEMA_PROMPT`/`SCHEMA_CONSOLIDATION_PROMPT` so the LLM proposes typed `properties` (name, datatype, required, unit) per node/edge type, not just names/descriptions; `normalize_schema`/`validate_schema` only govern properties the model is actually asked to produce

**Interfaces:**
- Consumes: existing `node_types`/`edge_types` JSON, optional `properties`, `category`, and `validation` declarations.
- Produces: `normalize_schema(schema) -> dict`, `validate_schema(schema) -> list[dict]`, and a normalized schema contract that preserves legacy inputs.

- [x] Write failing tests for a legacy schema with only names/descriptions.
- [x] Write failing tests for valid typed properties: datatype, required flag, unit, and description.
- [x] Write failing tests for invalid identifiers, unknown endpoint types, unsupported datatypes, and duplicate property declarations.
- [x] Implement the smallest normalizer that fills additive defaults without changing legacy semantics.
- [x] Implement validation errors as structured, human-readable records rather than raw exceptions where possible.
- [x] Add tests proving normalization is deterministic and idempotent.
- [x] Run `OPENROUTER_API_KEY=dummy .venv/bin/python -m pytest tests/test_schema_validation.py tests/test_ontology.py -q`. (12 new + existing ontology tests all pass; full suite 279 passed)
- [ ] ~~Modify `backend/app/ontology.py` schema loading/normalization call sites~~ deferred to Task 3: `generate_schema`/`load_schema` currently have exact-equality round-trip tests (`test_generate_schema_from_chunks_single_group_skips_consolidation`, `test_use_domain_schema_creates_new_version_for_document`, etc.) that normalization would break for no benefit yet — the first real consumer of `normalize_schema` is `extract_graph`'s prompt construction in Task 3, so wiring happens there instead of forcing every existing call site to change shape first.

**Commit:** `git commit -m "Add governed typed-property schema validation"`

### Task 3: Extend extraction output with structured metadata and evidence

**Files:**
- Modify: `backend/app/prompts.py` — shared extraction output contract and legal prompt
- Modify: `backend/app/ontology.py` — parse/normalize extracted nodes and edges
- Modify: `backend/tests/test_ontology.py`

**Interfaces:**
- Consumes: normalized domain schema and source chunk metadata.
- Produces: backward-compatible nodes/edges with optional `properties`, `evidence`, `source_section`, offsets, confidence, and temporal fields.

- [x] Add failing parser tests for missing optional metadata, malformed property maps, invalid evidence offsets, and unknown typed properties.
- [x] Extend the extraction prompt so exact figures and qualifying wording remain available as structured properties plus quoted evidence.
- [x] Keep `detail` as a compatibility/fallback field; do not remove or silently reinterpret it.
- [x] Normalize evidence into a stable shape with offsets, quote, and confidence. (`document_id`/`document_version`/`chunk_id` are not part of this shape -- see next item for why, and note the same document/version identity is already available from the caller's own context, not from the LLM.)
- [x] `source_section` is only populated when the document text actually contains bracketed labels (only true for the `_group_document_text()`-labeled group text `extract_graph_from_chunks` builds for chunked documents); a plain `anydoc` document has no such labels, so the field is simply absent for it, not fabricated.
- [x] Resolved the group-vs-chunk granularity gap differently than anticipated, and more simply: `_group_document_text()` already prints each chunk's own `[path]` label inline in the text handed to the LLM, so `EXTRACT_PROMPT` just asks the model to copy the nearest visible label into `source_section` — no new per-node chunk-id output field or `_merge_group_graphs` change was needed. `_normalize_extracted_item` verifies the returned label against the real labels found in that call's `document_text` (`_section_labels_in`) and drops it if it doesn't match one exactly, so a hallucinated section reference is dropped rather than trusted.
- [x] Reject or mark for review any extracted property that is not declared by the active domain schema. (`_normalize_extracted_properties` drops any key not declared for that exact type; "mark for review" for a declared-but-unverifiable property is deferred to Task 6's validation pass.)
- [x] Preserve deterministic ordering of nodes, edges, properties, and evidence arrays. (list comprehensions preserve input order; property dict insertion order follows the LLM's own JSON key order)
- [x] Run focused ontology tests and prompt JSON-contract tests. (10 new/updated `extract_graph*` tests pass; full suite 287 passed)

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

- [x] Write failing tests proving an article can state multiple independent semantic nodes. (`reference_graph` fixture: `article17` STATES a single `Norm` which fans out to bearer/action/condition/amount/exception/evidence — the multiple-independent-nodes shape lives one hop below the article, matching the reification guidance below rather than direct `Article -> N` edges)
- [x] Write failing tests proving a payment rule can connect to bearer, action/benefit, condition, amount, exception, and evidence. (`reference_graph` in `legal_policy_expected.json`)
- [x] Add explicit direction and endpoint checks for `STATES`, `HAS_CONDITION`, `HAS_EXCEPTION`, and `SUPPORTED_BY`. (`validate_legal_edge_shapes`)
- [x] Use a first-class `Norm`/`Rule` node when a relation has multiple qualifiers instead of packing all qualifiers into one edge property. (`LEGAL_SCHEMA_PROMPT` reification paragraph)
- [x] Keep simple intrinsic values as typed properties and simple relation qualifiers as edge properties. (already supported by Task 2/3's `properties` handling; no new code needed)
- [x] Add a guard that prevents structural nodes from receiving semantic catch-all `detail` content as their only representation. (`flag_structural_catchall_nodes`)
- [x] Run the representative fixture assertions without requiring a live LLM by testing normalized model payloads. (`_load_legal_fixture()` + `reference_graph`/`reference_graph_with_catchall_violation`/`reference_graph_with_bad_edge_shape` in the fixture; 6 tests, no LLM call)

**Commit:** `git commit -m "Model legal norms conditions exceptions and evidence"`

### Task 5: Extend LadybugDB persistence without breaking existing graph queries

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: normalized node/edge dictionaries with optional structured metadata.
- Produces: persisted common envelope and structured metadata; existing `load_graph`, type search, edge search, and hop expansion remain callable with current signatures/defaults.

- [x] Write failing round-trip tests for common provenance fields, typed property payloads, and temporal fields. (`valid_from`/`valid_to` are persisted as plain ISO-date strings — nothing in the pipeline generates or compares them as real dates yet, so a string column is the simplest thing that can be widened later without another migration)
- [x] Write failing tests proving old graph rows with only `detail` still load.
- [x] Write failing tests proving document/version deletion removes all new metadata together with graph rows.
- [x] Verified experimentally against the pinned `ladybug==0.19.1` engine: `ALTER TABLE ... ADD <column> <type>` on an existing table, and `MAP(STRING, STRING)`/`STRUCT(...)` column types, all work. Defaulted to `ALTER TABLE ADD` on each existing per-type NODE/REL table (`_ensure_envelope_columns`, called for every table `write_graph` touches, so it's a no-op on a freshly-created table and a real migration on a pre-existing one) to add the common envelope columns, plus one open `properties MAP(STRING, STRING)` column per table for schema-declared typed properties. Did **not** add `extraction_run_id`: nothing in the pipeline generates one yet, unlike `valid_from`/`valid_to`/`confidence`/evidence fields which Tasks 3–4 already produce — adding an always-NULL column with zero producers was deferred rather than spent here.
- [x] Found and fixed one more real engine quirk beyond the two already verified: an UNWIND row batch where every row's `start_offset`/`end_offset` is `None` binds fine as a plain node `CREATE`, but the identical all-NULL field in a `MATCH ... CREATE (a)-[:TYPE {...}]->(b)` relationship-creation query raises `Binder exception: STRUCT_EXTRACT(row,start_offset) has data type STRING but expected INT64` — the engine infers an untyped NULL as STRING in that query shape. Fixed with an explicit `CAST(row.start_offset AS INT64)` in the edge (and, for consistency, node) CREATE clause.
- [x] Add explicit serialization/deserialization helpers so database representation is not coupled to API response formatting. (`_envelope_extra_row_values`/`_apply_envelope_extras` — write-side and read-side halves of the same additive-only contract used by `_node_from_row`/`_edge_from_row`)
- [x] Keep internal primary-key handling separate from application-level `original_id` lookup. (already true pre-Task-5; unchanged)
- [x] Preserve transactionality and document/version scoping for writes, re-extraction, embedding updates, and deletion. (no changes to the transaction/scoping logic itself — only the columns and CREATE/RETURN clauses inside the existing transaction changed)
- [x] Run all graphdb tests, including deterministic ordering and fresh-database cases. (62 passed; full suite 297 passed)

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

