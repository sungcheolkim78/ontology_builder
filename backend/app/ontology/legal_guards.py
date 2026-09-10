from app.schema_validation import validate_graph

# Structural node_type names named explicitly in the design spec (section
# 3.3/5.1): these identify *where* text occurs, never *what* it says. Domain
# schemas are free to name their own structural types differently, but a
# schema using one of these exact names (case-insensitively, matching
# graphdb.py's own case-insensitive type-name resolution) gets this guard's
# protection against becoming a semantic catch-all.
STRUCTURAL_TYPE_NAMES = {
    "document", "policyversion", "chapter", "article", "paragraph", "item",
    "section", "clause", "schedule", "appendix",
}


def _is_structural_type(type_name: str) -> bool:
    return (type_name or "").lower() in STRUCTURAL_TYPE_NAMES


def flag_structural_catchall_nodes(graph: dict) -> list[dict]:
    """Flags a structural node (Article/Paragraph/...) that carries
    substantive `detail` but has no outgoing edge to any non-structural
    (semantic) node -- i.e. it's being used as a catch-all for what a
    provision says rather than only where it's written (spec section
    3.3/5.1). Does not flag a structural node with no `detail` at all (pure
    navigation, e.g. an empty Article shell), only one where the substance
    is packed onto the structural node itself instead of pulled out into its
    own concept node."""
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    has_semantic_outgoing = set()
    for edge in graph.get("edges", []):
        source_node = nodes_by_id.get(edge.get("source"))
        target_node = nodes_by_id.get(edge.get("target"))
        if source_node is None or target_node is None:
            continue
        if _is_structural_type(source_node.get("type")) and not _is_structural_type(
            target_node.get("type")
        ):
            has_semantic_outgoing.add(source_node["id"])

    issues = []
    for node in graph.get("nodes", []):
        if not _is_structural_type(node.get("type")):
            continue
        if node.get("detail") and node["id"] not in has_semantic_outgoing:
            issues.append(
                {
                    "severity": "warning",
                    "code": "structural_catchall",
                    "message": (
                        f"structural node {node.get('label')!r} (type "
                        f"{node.get('type')!r}) carries substantive detail but "
                        "has no edge to any semantic node -- pull the actual "
                        "content out into its own node instead"
                    ),
                    "node_id": node["id"],
                }
            )
    return issues


# Canonical legal edge shapes this app's own LEGAL_SCHEMA_PROMPT and the
# design spec (section 5) name explicitly. Checked only when a domain schema
# actually uses one of these exact edge_type names (case-insensitively) --
# node/edge type names are otherwise entirely schema-declared, not
# engine-fixed, so this is a targeted sanity check for the app's own
# recommended pattern, not a general schema constraint.
_LEGAL_EDGE_ENDPOINT_HINTS = {
    "states": {"source_structural": True, "target_structural": False},
    "has_condition": {"target_type_hint": "condition"},
    "has_exception": {"target_type_hint": "exclusion"},
    "supported_by": {"target_type_hint": "evidencespan"},
}


def validate_legal_edge_shapes(graph: dict) -> list[dict]:
    """Validates direction/endpoint expectations for STATES/HAS_CONDITION/
    HAS_EXCEPTION/SUPPORTED_BY edges when a graph uses those exact names.
    Silently ignores every other edge_type -- this is not a general schema
    validator (see app.schema_validation for that), just a guard against the
    one reification pattern this app's own legal prompt asks for."""
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    issues = []
    for edge in graph.get("edges", []):
        hint = _LEGAL_EDGE_ENDPOINT_HINTS.get((edge.get("type") or "").lower())
        if hint is None:
            continue
        source_node = nodes_by_id.get(edge.get("source"))
        target_node = nodes_by_id.get(edge.get("target"))
        if source_node is None or target_node is None:
            continue

        if "source_structural" in hint and _is_structural_type(
            source_node.get("type")
        ) != hint["source_structural"]:
            issues.append(
                {
                    "severity": "warning",
                    "code": "unexpected_endpoint_type",
                    "message": (
                        f"{edge['type']} edge's source {source_node.get('label')!r} "
                        f"(type {source_node.get('type')!r}) does not match the "
                        "expected structural/semantic role for this edge_type"
                    ),
                    "edge": (edge["source"], edge["target"], edge["type"]),
                }
            )
        if "target_structural" in hint and _is_structural_type(
            target_node.get("type")
        ) != hint["target_structural"]:
            issues.append(
                {
                    "severity": "warning",
                    "code": "unexpected_endpoint_type",
                    "message": (
                        f"{edge['type']} edge's target {target_node.get('label')!r} "
                        f"(type {target_node.get('type')!r}) does not match the "
                        "expected structural/semantic role for this edge_type"
                    ),
                    "edge": (edge["source"], edge["target"], edge["type"]),
                }
            )
        target_hint = hint.get("target_type_hint")
        if target_hint and target_hint not in (target_node.get("type") or "").lower():
            issues.append(
                {
                    "severity": "warning",
                    "code": "unexpected_endpoint_type",
                    "message": (
                        f"{edge['type']} edge's target {target_node.get('label')!r} "
                        f"has type {target_node.get('type')!r}, expected something "
                        f"matching {target_hint!r}"
                    ),
                    "edge": (edge["source"], edge["target"], edge["type"]),
                }
            )
    return issues


def run_graph_validation(schema: dict, graph: dict) -> list[dict]:
    """Single entry point for graph-shape and evidence validation before
    persistence (design spec section 7): combines app.schema_validation's
    generic instance checks (wrong endpoints, missing required properties,
    invalid numeric values, duplicate canonical nodes, missing legal
    evidence) with this module's own legal-reification guards
    (flag_structural_catchall_nodes, validate_legal_edge_shapes). Each
    category is independent and additive -- a caller that only cares about
    one can filter the combined list by `code`, and adding a new category
    to either side never requires touching this function.

    Not currently called by any main.py route -- /validate uses the
    LLM-driven validate_ontology instead. Left in place, unwired, rather
    than removed or wired in, pending a decision on which of the two this
    app should actually use for /validate."""
    return [
        *validate_graph(schema, graph),
        *flag_structural_catchall_nodes(graph),
        *validate_legal_edge_shapes(graph),
    ]
