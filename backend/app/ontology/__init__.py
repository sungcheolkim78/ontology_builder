"""Ontology pipeline: propose a schema for a document, then extract nodes/
edges conforming to it. Split into submodules by concern -- see each
submodule's own docstring/comments for what it owns:

- persistence: per-document file CRUD (versions.json, schema_v{N}.json,
  discovery.json, summary.json, manifest.json) plus the graph-DB-backed
  save/load/embed functions. A dependency-free leaf every other submodule
  can import from.
- extraction: the LLM-driven pipeline itself -- discover/generate_schema/
  extract_graph, their chunk-grouped map-reduce variants, domain-schema
  convergence (a pipeline concern: it composes extract_graph/
  validate_ontology/propose_evolution), and the discover_for_document/
  schema_for_document/extract_for_document seams main.py's routes call.
  Depends on persistence.
- legal_guards: this app's own legal-reification structural checks
  (flag_structural_catchall_nodes, validate_legal_edge_shapes) plus
  run_graph_validation, which combines them with app.schema_validation's
  generic checks. A leaf, independent of every other submodule here.
- domain_schema: persistence for a *domain*'s (not a document's) converged
  schema -- storage, calibration history, pending-review queue. Depends on
  extraction (converge_domain_schema/generate_schema) and persistence
  (create_schema_version).

get_chat_model/get_embedding_model are imported here, not directly from
app.chat/app.embeddings in each submodule, and every submodule reaches them
via `from app import ontology` + `ontology.get_chat_model(...)` at call
time (never `from . import get_chat_model`, which would bind a private copy
of the name at import time). This is what keeps every existing
`monkeypatch.setattr("app.ontology.get_chat_model", fake)` in the test
suite working unchanged after this split: it patches the attribute on this
module object, and a live attribute lookup at call time sees the patch --
a name bound once at import time would not."""

from app.chat import get_chat_model  # noqa: F401 -- re-exported; see module docstring
from app.embeddings import get_embedding_model, node_embedding_text  # noqa: F401

from .persistence import (
    DEFAULT_SCHEMA,
    DOCUMENTS_DIR,
    _apply_schema_type_changes,
    _apply_type_change,
    _load_versions_manifest,
    _save_versions_manifest,
    activate_version,
    create_schema_version,
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
from .extraction import (
    MAX_CHUNK_GROUP_CHARS,
    MAX_DOCUMENT_CHARS,
    _CONFIDENCE_LEVELS,
    _SECTION_LABEL_RE,
    _check_document_length,
    _clear_extraction_progress,
    _consolidate_schema_types,
    _consolidate_types,
    _cosine_similarity,
    _dedupe_by_key,
    _extraction_progress_dir,
    _find_evidence_span,
    _group_document_text,
    _load_chunk_items,
    _merge_domain_models,
    _merge_group_graphs,
    _normalize_extracted_item,
    _normalize_extracted_properties,
    _properties_by_type,
    _require_document_text,
    _section_labels_in,
    _write_extraction_progress,
    apply_evolution,
    converge_domain_schema,
    discover_for_document,
    discover_ontology,
    discover_ontology_from_chunks,
    evaluate_domain_schema,
    extract_for_document,
    extract_graph,
    extract_graph_from_chunks,
    find_redundant_type_pairs,
    generate_schema,
    generate_schema_from_chunks,
    group_chunks_by_budget,
    measure_schema_stability,
    parse_json_response,
    propose_evolution,
    schema_for_document,
    summarize_document,
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
