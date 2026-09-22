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
- utils: dependency-light helpers shared by two or more of the three
  submodules above (MAX_DOCUMENT_CHARS/MAX_CHUNK_GROUP_CHARS,
  parse_json_response, the chunk-grouping/document-loading helpers, and
  ChunkProgress/start_progress/load_progress -- the in-flight progress
  tracker all three chunk-grouped operations report to, polled by main.py's
  GET /progress route). A leaf, independent of every other submodule here.
- legal_guards: this app's own legal-reification structural checks
  (flag_structural_catchall_nodes, validate_legal_edge_shapes) plus
  run_graph_validation, which combines them with app.ontology.schema_validation's
  generic checks. A leaf, independent of every other submodule here.
- domain_schema: persistence for a *domain*'s (not a document's) converged
  schema -- storage, calibration history, pending-review queue. Depends on
  evolve_graph (converge_domain_schema), generate_schema (generate_schema),
  and persistence (create_schema_version).

get_chat_model/get_embedding_model are imported here, not directly from
app.llm.chat/app.preprocess.embeddings in each submodule, and every submodule reaches them
via `from app import ontology` + `ontology.get_chat_model(...)` at call
time (never `from . import get_chat_model`, which would bind a private copy
of the name at import time). This is what keeps every existing
`monkeypatch.setattr("app.ontology.get_chat_model", fake)` in the test
suite working unchanged after this split: it patches the attribute on this
module object, and a live attribute lookup at call time sees the patch --
a name bound once at import time would not."""

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
from .utils import (
    MAX_CHUNK_GROUP_CHARS,
    MAX_DOCUMENT_CHARS,
    ChunkProgress,
    _check_document_length,
    _dedupe_by_key,
    _group_document_text,
    _load_chunk_items,
    _require_document_text,
    group_chunks_by_budget,
    load_progress,
    parse_json_response,
    start_progress,
)
from .generate_schema import (
    _DISCOVER_GROUP_FIELDS,
    _clear_stale_group_candidates,
    _consolidate_schema_types,
    _consolidate_types,
    _cosine_similarity,
    _discover_field_default,
    _load_group_candidates,
    _merge_domain_models,
    _write_group_candidates,
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
    _clear_extraction_progress,
    _extraction_progress_dir,
    _find_evidence_span,
    _merge_group_graphs,
    _normalize_extracted_item,
    _normalize_extracted_properties,
    _properties_by_type,
    _section_labels_in,
    _write_extraction_progress,
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
