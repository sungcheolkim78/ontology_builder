# Ontology Builder

Turns a document into a custom ontology (schema plus extracted nodes and edges) that a chatbot uses for GraphRAG question answering.

## Language

**Chunk group**:
A budgeted run of consecutive chunks sent to the LLM in a single call.
_Avoid_: batch, candidate group

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
