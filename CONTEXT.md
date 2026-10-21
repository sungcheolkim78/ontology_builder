# Ontology Builder

Turns a document into a custom ontology (schema plus extracted nodes and edges) that a chatbot uses for GraphRAG question answering.

## Language

**Document folder**:
The one directory under `documents/` holding everything about one document: its Markdown, source PDF, chunks, schema versions, graph-extraction progress and so on. A domain has a folder of its own under `domain_schemas/`.
_Avoid_: document directory, document store

**Artifact**:
One kind of file inside a document folder (or a domain folder): `raw.md`, `chunks.json`, `versions.json`, a `schema_v{N}.json`, and so on. Where each one lives is decided in one place, `app.utils.paths`.
_Avoid_: output, cache file (a resume cache entry is an artifact too)

**Golden set**:
Questions about one document, each with its answer and the verbatim passages that prove it, generated from the whole document rather than from its chunks. It is the ground truth a schema is measured against.
_Avoid_: test set, benchmark

**Retrieval outcome**:
How a golden question fares against an extracted graph: a *hit* (the retrieved nodes and edges cite the passage that answers it), a *retrieval miss* (the graph has a node citing that passage, but the search did not return it) or an *extraction miss* (no node in the graph cites that passage at all). Only the last two say where to look: a schema can often repair a retrieval miss or an extraction miss caused by a missing type, but not an extraction that is simply too sparse.
_Avoid_: pass/fail, accuracy

**Trial stem**:
A temporary document name under which a candidate schema's extracted graph is written in the shared graph database so it can be scored, and deleted when the run ends. It has no document folder, so it never appears as a document.
_Avoid_: scratch document, test document

**Refinement**:
Merging the types of a finished schema to make it smaller, then scoring the result against the golden set to show what the merge cost or gained. Distinct from generating a schema (which proposes types from the document) and from evolving it (which changes types in response to extracted instances). A person approves every refinement before it becomes a schema version.
_Avoid_: simplification, cleanup

**Merge mapping**:
The table from each old type name to the type that replaces it, applied identically to a schema and to the graph extracted under it.
_Avoid_: merge decision (that is evolution's term for a proposed change)

**Candidate schema**:
The schema a refinement produces by applying a merge mapping. It is not a schema version until a person approves it.
_Avoid_: draft schema, trial schema

**Refinement report**:
One candidate schema scored against the schema it would replace: the change in type counts and, per golden question, the change in Retrieval outcome (questions gained, questions lost).
_Avoid_: evaluation result

**Reading**:
One way of scoring a golden question against the search results: *strict* (a retrieved node quotes the answering passage), *semantic* (a node's text is close to it), *answer* (the retrieved text states the facts of the golden answer) or *judge* (an LLM says the context can answer). Reported side by side, never merged; the answer reading is the one to decide with.
_Avoid_: metric, score type

**Chunk group**:
A budgeted run of consecutive chunks sent to the LLM in a single call. A document with no chunks is a single chunk group holding its whole text.
_Avoid_: batch, candidate group

**Fingerprint**:
What identifies a group result as reusable: the stage, the model and output limit of its operation, the stage's own inputs (schema, document type, prompt) and the chunk group's text. A resume cache entry is reused only when its fingerprint matches.
_Avoid_: cache key, hash

**Group result**:
What one chunk group produced for one pipeline stage.
_Avoid_: group candidates, extraction progress, partial result

**Resume cache**:
The stored group results that let a retried run skip chunk groups that already finished.
_Avoid_: progress cache, checkpoint

**Operation**:
A named kind of LLM call with its own model choice, output limits, telemetry name and expected JSON shape. A pipeline stage can use several (`generate_schema` and `consolidate_schema` are both operations of the schema stage).
_Avoid_: task, step

**Reduce**:
The step that folds every group result of a stage into that stage's single output.
_Avoid_: merge step, consolidation (consolidation is one kind of reduce)

**Rider** (특약):
A contract attached to a policy's main contract (주계약), with its own numbering of articles. It starts at its `OOO특약 약관` title line and first article; the chunker records it as `rider_type`/`rider_name` on every chunk (`rider_type` is also `main`, `general` for the common 제1편 every rider refers to, or `appendix`). A policy can hold more than one main contract.
_Avoid_: section (a chunk's `section_index` numbers riders, but "section" alone is ambiguous with 편/관 headings), 부가계약

**Appendix**:
The laws a policy cites, printed at its end. Each law is its own section (`rider_type: appendix`), named from its `- 법령명` line, because it numbers its own articles from 제1조.
_Avoid_: 부록 chunk, law chunk

**Plan** (`variant`):
A rider sold in several plans repeats its articles once per plan, each run opened by a `《종합통원형》`-style line. The plan is recorded as the chunk's `variant`.
_Avoid_: type, version

**Table of contents reference**:
The policy's own "상품 목차" (main contract, riders, appendix), read by `app.preprocess.chunking_eval` to check what the chunker found. Never an input to the chunker.
_Avoid_: ground truth (it can be wrong or unreadable; a policy without one is reported `no-toc`)
