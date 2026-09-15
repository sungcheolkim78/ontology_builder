"""Document preprocessing, upstream of the ontology pipeline (app.ontology)
and chat retrieval (app.graph.graphrag) -- turning an uploaded file into the
markdown/chunks/embeddings/golden-QA artifacts those consume:

- parser: pdf/doc -> markdown, the first ingestion stage (`anydoc` for most
  formats, a table-aware pdfplumber path for `.pdf`).
- chunking: markdown -> per-article JSON chunks, the second stage.
- embeddings: the OpenRouter embedding client and the node-text convention
  (label + detail) shared by extraction (app.ontology) and retrieval
  (app.graph.graphrag) so both sides of a similarity comparison agree.
- goldenset: per-document golden QA generation, used to validate the
  ontology pipeline's own output.

Each submodule is otherwise independent -- this package groups them by
pipeline stage, not by shared code.
"""
