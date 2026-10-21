# Extraction has its own chunk-group budget, 3,000 characters

Discover, schema generation and extraction all pack chunks into groups of `MAX_CHUNK_GROUP_CHARS` (12,000). Extraction loses facts when it is handed a lot of text at once, while a schema or a discovery report gains little from smaller groups and pays a call, and a consolidation, for each, so extraction alone reads `EXTRACT_GROUP_CHARS`, default 3,000. On two policy documents this took the share of golden questions answerable from the graph from 39% to 74% and from 42% to 69%; 1,500 characters added nothing beyond run-to-run noise (`docs/ontology/extraction_quality_findings.md`, 3.2).

## Consequences

- A document extracts in about 3.7 times the LLM calls (twice the wall time) and its graph is about twice the size. `EXTRACT_GROUP_CHARS` restores the old behaviour.
- A budget cannot split a chunk: one longer than the budget is its own group, so a document with a very large chunk does not benefit (issue #2).
- Do not "unify" the two budgets back into one: they were separated on purpose.
