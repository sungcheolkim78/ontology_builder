"""Ontology pipeline: propose a schema for a document, then extract nodes/
edges conforming to it. Split into submodules by concern -- see each
submodule's own docstring/comments for what it owns:

- persistence: per-document file CRUD (versions.json, schema_v{N}.json,
  discovery.json, summary.json, manifest.json) plus the graph-DB-backed
  save/load/embed functions. A dependency-free leaf every other submodule
  can import from.
- generate_schema: the LLM-driven schema-generation stage -- discover_ontology/
  generate_schema, their chunk-grouped map-reduce variants, summarize_document,
  and the discover_for_document/schema_for_document seams main.py's routes
  call, plus the schema-quality checks (find_redundant_type_pairs,
  measure_schema_stability). Depends on utils.
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
  the resume cache, and the progress tracker (ChunkProgress/start_progress/
  load_progress) that main.py's GET /progress route polls. A leaf.
- utils: dependency-light helpers shared by two or more of the stages above
  (MAX_DOCUMENT_CHARS, the document-loading helpers). A leaf, independent of
  every other submodule here.
- legal_guards: this app's own legal-reification structural checks
  (flag_structural_catchall_nodes, validate_legal_edge_shapes) plus
  run_graph_validation, which combines them with app.ontology.schema_validation's
  generic checks. A leaf, independent of every other submodule here.
- domain_schema: persistence for a *domain*'s (not a document's) converged
  schema -- storage, calibration history, pending-review queue. Depends on
  evolve_graph (converge_domain_schema), generate_schema (generate_schema),
  and persistence (create_schema_version).

get_chat_model/get_embedding_model are still imported here for the two
callers that need a live, patchable lookup: summarize_document (a prose call,
not a JSON operation) reaches get_chat_model via `from app import ontology` +
`ontology.get_chat_model(...)` at call time, and embed_nodes/embed_query-style
callers do the same for get_embedding_model (never `from . import
get_chat_model`, which would bind a private copy of the name at import time).
Every JSON-returning LLM call goes through app.llm.json_call.call_json
instead, whose own `get_chat_model` is the one patch point tests use for it.
"""

from app.llm.chat import get_chat_model  # noqa: F401 -- re-exported; see module docstring
from app.preprocess.embeddings import get_embedding_model, node_embedding_text  # noqa: F401

from .persistence import (
    DEFAULT_SCHEMA,
    DOCUMENTS_DIR,
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
from .legal_guards import (
    STRUCTURAL_TYPE_NAMES,
    _LEGAL_EDGE_ENDPOINT_HINTS,
    _is_structural_type,
    flag_structural_catchall_nodes,
    run_graph_validation,
    validate_legal_edge_shapes,
)
from .chunk_groups import (
    MAX_CHUNK_GROUP_CHARS,
    ChunkProgress,
    _group_document_text,
    group_chunks_by_budget,
    load_progress,
    map_concurrently,
    run_chunk_groups,
    start_progress,
)
from .utils import (
    MAX_DOCUMENT_CHARS,
    _check_document_length,
    _dedupe_by_key,
    _load_chunk_items,
    _require_document_text,
)
from .generate_schema import (
    _consolidate_schema_types,
    _consolidate_types,
    _cosine_similarity,
    _merge_domain_models,
    _reduce_discovery_reports,
    discover_for_document,
    discover_ontology,
    discover_ontology_from_chunks,
    find_redundant_type_pairs,
    generate_schema,
    generate_schema_from_chunks,
    measure_schema_stability,
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
    extract_graph_from_chunks,
)
from .evolve_graph import (
    apply_evolution,
    converge_domain_schema,
    evaluate_domain_schema,
    propose_evolution,
    validate_ontology,
)
from .domain_schema import (
    DOMAIN_SCHEMA_DIR,
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
