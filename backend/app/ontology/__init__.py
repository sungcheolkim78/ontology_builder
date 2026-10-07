"""Ontology pipeline: propose a schema for a document, then extract nodes/
edges conforming to it. Split into submodules by concern -- see each
submodule's own docstring/comments for what it owns:

- persistence: per-document file CRUD (versions.json, schema_v{N}.json,
  discovery.json, summary.json, manifest.json) plus the graph-DB-backed
  save/load/embed functions. A dependency-free leaf every other submodule
  can import from.
- generate_schema: the LLM-driven schema-generation stage -- discover_ontology/
  generate_schema (each run once per chunk group by run_chunk_groups), their
  reduce functions, summarize_document, and the discover_for_document/
  schema_for_document seams main.py's routes call. Depends on utils.
- schema_quality: standalone schema diagnostics (find_redundant_type_pairs,
  measure_schema_stability), independent of the pipeline stages. Depends on
  generate_schema.
- extract_graph: the LLM-driven instance-extraction stage -- extract_graph,
  its chunk-grouped map-reduce variant, and the extract_for_document seam
  main.py's /extract route calls. Depends on persistence and utils.
- evolve_graph: validating/evolving what extract_graph produced --
  validate_ontology/propose_evolution/apply_evolution, plus domain-schema
  convergence (a pipeline concern: it composes extract_graph/
  validate_ontology/propose_evolution across a sequence of documents).
  Depends on extract_graph, persistence, and utils.
- chunk_groups: the one module behind which discover, schema generation and
  graph extraction run their per-chunk-group map step and reduce step
  (run_chunk_groups), including grouping by MAX_CHUNK_GROUP_CHARS, concurrency,
  the resume cache, and the progress tracker (ChunkProgress/
  load_progress) that main.py's GET /progress route polls. A leaf.
- utils: dependency-light helpers shared by two or more of the stages above
  (MAX_DOCUMENT_CHARS, the document-loading helpers). A leaf, independent of
  every other submodule here.
- domain_schema: persistence for a *domain*'s (not a document's) converged
  schema -- storage, calibration history, pending-review queue. Depends on
  evolve_graph (converge_domain_schema), generate_schema (generate_schema),
  and persistence (create_schema_version).

Every model call, chat or embedding, goes through app.llm.calls (call_json,
call_text, embed), whose own `get_chat_model`/`get_embedding_model` are the
patch points tests use.
"""

from app.preprocess.embeddings import node_embedding_text  # noqa: F401

from .persistence import (
    DEFAULT_SCHEMA,
    _apply_schema_type_changes,
    _apply_type_change,
    _load_versions_manifest,
    _save_versions_manifest,
    activate_version,
    create_schema_version,
    delete_document,
    delete_version,
    discovery_path_for,
    embed_graph,
    embed_nodes,
    get_active_version,
    list_schema_stems,
    list_versions,
    load_discovery,
    load_document_manifest,
    load_document_summary,
    load_graph,
    load_schema,
    save_discovery,
    save_document_manifest,
    save_document_summary,
    save_graph,
    save_schema,
    schema_path_for_version,
    summary_path_for,
    update_document_manifest,
    versions_path,
)
from .chunk_groups import (
    MAX_CHUNK_GROUP_CHARS,
    ChunkProgress,
    _group_document_text,
    group_chunks_by_budget,
    load_progress,
    map_concurrently,
    run_chunk_groups,
)
from .utils import (
    MAX_DOCUMENT_CHARS,
    _check_document_length,
    _dedupe_by_key,
    _load_chunk_items,
    _require_document_text,
)
from .schema_quality import (
    _cosine_similarity,
    find_redundant_type_pairs,
    measure_schema_stability,
)
from .generate_schema import (
    _consolidate_schema_types,
    _consolidate_types,
    _merge_domain_models,
    _reduce_discovery_reports,
    discover_for_document,
    discover_ontology,
    generate_schema,
    schema_for_document,
    summarize_document,
)
from .extract_graph import (
    _CONFIDENCE_LEVELS,
    _SECTION_LABEL_RE,
    _find_evidence_span,
    _merge_group_graphs,
    _normalize_extracted_item,
    _normalize_extracted_properties,
    _properties_by_type,
    _section_labels_in,
    extract_for_document,
    extract_graph,
)
from .evolve_graph import (
    apply_evolution,
    converge_domain_schema,
    evaluate_domain_schema,
    propose_evolution,
    validate_ontology,
)
from .domain_schema import (
    _domain_manifest_path,
    _domain_pending_review_path,
    _load_domain_manifest,
    _save_domain_manifest,
    _save_domain_pending_review,
    apply_domain_schema_changes,
    domain_calibration_stems,
    domain_convergence_history,
    domain_dir_for,
    domain_schema_path,
    list_domains,
    load_domain_pending_review,
    load_domain_schema,
    run_domain_convergence,
    save_domain_schema,
    use_domain_schema,
)
