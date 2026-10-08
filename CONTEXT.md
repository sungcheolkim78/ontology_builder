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
