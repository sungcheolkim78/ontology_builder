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
