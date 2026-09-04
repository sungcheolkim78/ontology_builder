"""Normalization and validation for domain schema declarations
(`node_types`/`edge_types` plus optional typed `properties`/`category`/
`validation`) -- see docs/superpowers/specs/2026-09-04-flexible-ontology-graph-schema-design.md
section 4.2/8. Kept separate from app.ontology so the schema *contract*
(what shape a schema must have) can be read/edited independently of how
ontology.py loads, generates, or evolves schemas.

`normalize_schema` only fills in additive defaults; it never invents or
changes a value a caller already supplied, so a legacy schema (names/
descriptions only) round-trips into the same shape every time, and calling
it twice is a no-op the second time (idempotent).

`validate_schema` reports structural problems as a list of issue records
rather than raising, so a caller (e.g. the domain schema evolution/review
workflow) can decide whether an issue blocks applying a schema or only
needs a human's attention -- see ontology.py's pending_review handling.
"""

import copy
import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

# Matches the datatypes schema.json's `properties` block is allowed to
# declare. A MAP(STRING, STRING) column stores every value as a string
# regardless of this declaration (see graphdb.py / spec section 4.2's note
# on physical representation) -- `datatype` is what the extraction/query
# layers cast to and from, not the raw storage type.
SUPPORTED_DATATYPES = {"string", "number", "integer", "boolean", "date"}

_DEFAULT_VALIDATION = {"required_provenance": False, "closed_world_types": False}


def _normalize_type_entry(entry: dict) -> dict:
    normalized = dict(entry)
    normalized.setdefault("properties", {})
    normalized.setdefault("category", None)
    return normalized


def normalize_schema(schema: dict) -> dict:
    """Returns a new schema dict with additive defaults filled in: every
    node_type/edge_type gets a `properties` dict (default {}) and a
    `category` (default None), and the schema gets a top-level `validation`
    block (default all-False) if it doesn't already have one. Never
    mutates `schema` or overwrites a value the caller already supplied."""
    normalized = copy.deepcopy(schema)
    normalized["node_types"] = [
        _normalize_type_entry(t) for t in normalized.get("node_types", [])
    ]
    normalized["edge_types"] = [
        _normalize_type_entry(t) for t in normalized.get("edge_types", [])
    ]
    normalized.setdefault("validation", dict(_DEFAULT_VALIDATION))
    return normalized


def _issue(code: str, message: str, type_kind: str, type_name: str, property: str | None = None) -> dict:
    issue = {
        "severity": "error",
        "code": code,
        "message": message,
        "type_kind": type_kind,
        "type_name": type_name,
    }
    if property is not None:
        issue["property"] = property
    return issue


def _validate_properties(type_kind: str, type_name: str, properties: dict) -> list[dict]:
    issues = []
    seen_lower = {}
    for prop_name, prop_decl in properties.items():
        if not _IDENTIFIER_RE.match(prop_name):
            issues.append(
                _issue(
                    "invalid_identifier",
                    f"property name {prop_name!r} on {type_kind} type {type_name!r} "
                    "is not a safe identifier",
                    type_kind,
                    type_name,
                    property=prop_name,
                )
            )

        lower = prop_name.lower()
        if lower in seen_lower:
            issues.append(
                _issue(
                    "duplicate_property",
                    f"{type_kind} type {type_name!r} declares property "
                    f"{prop_name!r}, which collides case-insensitively with "
                    f"already-declared property {seen_lower[lower]!r}",
                    type_kind,
                    type_name,
                    property=prop_name,
                )
            )
        else:
            seen_lower[lower] = prop_name

        datatype = (prop_decl or {}).get("datatype")
        if datatype is not None and datatype not in SUPPORTED_DATATYPES:
            issues.append(
                _issue(
                    "unsupported_datatype",
                    f"property {prop_name!r} on {type_kind} type {type_name!r} "
                    f"declares unsupported datatype {datatype!r}",
                    type_kind,
                    type_name,
                    property=prop_name,
                )
            )
    return issues


def validate_schema(schema: dict) -> list[dict]:
    """Validates a schema's structural shape: safe identifiers for every
    type/property name, edge_type endpoints that reference a declared
    node_type, supported property datatypes, and no duplicate type/property
    declarations (case-insensitive, matching graphdb.py's own
    case-insensitive table-name resolution). Returns [] when the schema is
    valid; never raises on a malformed-but-well-typed schema dict."""
    normalized = normalize_schema(schema)
    issues: list[dict] = []

    node_type_names_lower = {}
    for node_type in normalized["node_types"]:
        name = node_type["name"]
        if not _IDENTIFIER_RE.match(name):
            issues.append(_issue("invalid_identifier", f"node_type name {name!r} is not a safe identifier", "node", name))

        lower = name.lower()
        if lower in node_type_names_lower:
            issues.append(
                _issue(
                    "duplicate_type",
                    f"node_type {name!r} collides case-insensitively with "
                    f"already-declared node_type {node_type_names_lower[lower]!r}",
                    "node",
                    name,
                )
            )
        else:
            node_type_names_lower[lower] = name

        issues.extend(_validate_properties("node", name, node_type["properties"]))

    for edge_type in normalized["edge_types"]:
        name = edge_type["name"]
        if not _IDENTIFIER_RE.match(name):
            issues.append(_issue("invalid_identifier", f"edge_type name {name!r} is not a safe identifier", "edge", name))

        for endpoint_role in ("source", "target"):
            endpoint = edge_type.get(endpoint_role)
            if endpoint is not None and endpoint.lower() not in node_type_names_lower:
                issues.append(
                    _issue(
                        "unknown_endpoint_type",
                        f"edge_type {name!r} references unknown {endpoint_role} "
                        f"node_type {endpoint!r}",
                        "edge",
                        name,
                    )
                )

        issues.extend(_validate_properties("edge", name, edge_type["properties"]))

    return issues


# Legal node types whose instances always need source evidence to ground a
# generated answer in the source document (design spec section 5.3/6),
# checked case-insensitively -- same convention as app.ontology's own legal
# guard functions (flag_structural_catchall_nodes/validate_legal_edge_shapes,
# which cover the companion structural checks this function doesn't
# duplicate: this is about instance-level evidence/property completeness,
# not edge direction or structural-node catch-alls).
LEGAL_EVIDENCE_REQUIRED_TYPES = {"norm", "rule", "condition", "exclusion", "benefit"}


def _graph_issue(code: str, message: str, **extra) -> dict:
    issue = {"severity": "error", "code": code, "message": message}
    issue.update(extra)
    return issue


def summarize_validation_issues(issues: list[dict]) -> dict:
    """Collapses a validate_schema()/validate_graph() issue list into
    {"error_count", "warning_count"} -- the compact form recorded in domain
    convergence history (app.ontology.run_domain_convergence) alongside
    schema_contract_version, since a full issue list would bloat manifest.json
    for what's meant to be a quick "did this converge cleanly" glance."""
    return {
        "error_count": sum(1 for i in issues if i.get("severity") == "error"),
        "warning_count": sum(1 for i in issues if i.get("severity") == "warning"),
    }


def validate_graph(schema: dict, graph: dict) -> list[dict]:
    """Validates an extracted graph's shape and evidence against `schema`
    before persistence: edge endpoints matching the schema's declared
    source/target types, required properties present, numeric-datatype
    properties that actually parse as numbers, duplicate (type, label)
    canonical nodes that should have been merged into one, and missing
    evidence on the app's own legal reification types. Never raises on a
    malformed-but-well-typed graph -- callers decide what an issue blocks,
    same as validate_schema."""
    normalized_schema = normalize_schema(schema)
    node_types_by_name = {t["name"]: t for t in normalized_schema.get("node_types", [])}
    edge_types_by_name = {t["name"]: t for t in normalized_schema.get("edge_types", [])}
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}

    issues = []

    seen_canonical = {}
    for node in graph.get("nodes", []):
        node_type = node_types_by_name.get(node.get("type"))
        properties = node.get("properties") or {}

        key = (node.get("type"), node.get("label"))
        if key in seen_canonical and seen_canonical[key] != node["id"]:
            issues.append(
                _graph_issue(
                    "duplicate_canonical_node",
                    f"node {node.get('label')!r} (type {node.get('type')!r}) appears as "
                    f"both {seen_canonical[key]!r} and {node['id']!r} -- should have been "
                    "merged into one canonical node",
                    node_id=node["id"],
                    type_name=node.get("type"),
                )
            )
        else:
            seen_canonical[key] = node["id"]

        if node_type is not None:
            for prop_name, prop_decl in node_type.get("properties", {}).items():
                if prop_decl.get("required") and prop_name not in properties:
                    issues.append(
                        _graph_issue(
                            "missing_required_property",
                            f"node {node.get('label')!r} (type {node.get('type')!r}) is "
                            f"missing required property {prop_name!r}",
                            node_id=node["id"],
                            type_name=node.get("type"),
                            property=prop_name,
                        )
                    )
                if prop_name in properties and prop_decl.get("datatype") in ("number", "integer"):
                    try:
                        float(properties[prop_name])
                    except (TypeError, ValueError):
                        issues.append(
                            _graph_issue(
                                "invalid_numeric_value",
                                f"node {node.get('label')!r} property {prop_name!r} "
                                f"({properties[prop_name]!r}) is not a valid number",
                                node_id=node["id"],
                                type_name=node.get("type"),
                                property=prop_name,
                            )
                        )

        if (node.get("type") or "").lower() in LEGAL_EVIDENCE_REQUIRED_TYPES and not node.get(
            "evidence_text"
        ):
            issues.append(
                _graph_issue(
                    "missing_evidence",
                    f"node {node.get('label')!r} (type {node.get('type')!r}) has no "
                    "evidence_text -- every production legal Norm/Condition/Exclusion/"
                    "Benefit relevant to an answer must be grounded in source evidence",
                    node_id=node["id"],
                    type_name=node.get("type"),
                )
            )

    for edge in graph.get("edges", []):
        edge_type = edge_types_by_name.get(edge.get("type"))
        if edge_type is None:
            continue
        source_node = nodes_by_id.get(edge.get("source"))
        target_node = nodes_by_id.get(edge.get("target"))
        declared_source = edge_type.get("source")
        declared_target = edge_type.get("target")
        edge_ref = (edge.get("source"), edge.get("target"), edge.get("type"))

        if (
            declared_source
            and source_node is not None
            and (source_node.get("type") or "").lower() != declared_source.lower()
        ):
            issues.append(
                _graph_issue(
                    "wrong_endpoint_type",
                    f"edge {edge.get('type')!r} source {source_node.get('label')!r} has "
                    f"type {source_node.get('type')!r}, schema declares source type "
                    f"{declared_source!r}",
                    type_name=edge.get("type"),
                    edge=edge_ref,
                )
            )
        if (
            declared_target
            and target_node is not None
            and (target_node.get("type") or "").lower() != declared_target.lower()
        ):
            issues.append(
                _graph_issue(
                    "wrong_endpoint_type",
                    f"edge {edge.get('type')!r} target {target_node.get('label')!r} has "
                    f"type {target_node.get('type')!r}, schema declares target type "
                    f"{declared_target!r}",
                    type_name=edge.get("type"),
                    edge=edge_ref,
                )
            )

    return issues
