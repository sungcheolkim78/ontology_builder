import copy

from app.schema_validation import normalize_schema, validate_graph, validate_schema

LEGACY_SCHEMA = {
    "node_types": [
        {"name": "Entity", "description": "A generic named entity."}
    ],
    "edge_types": [
        {
            "name": "RELATED_TO",
            "description": "A generic relationship.",
            "source": "Entity",
            "target": "Entity",
        }
    ],
}

TYPED_SCHEMA = {
    "schema_version": 2,
    "domain": "insurance_policy",
    "node_types": [
        {
            "name": "Norm",
            "description": "A rule stated by the policy.",
            "category": "RULE",
            "properties": {
                "modality": {"datatype": "string", "required": True},
                "operator": {"datatype": "string", "required": False, "unit": None},
            },
        },
        {"name": "Condition", "description": "A condition that must hold."},
    ],
    "edge_types": [
        {
            "name": "HAS_CONDITION",
            "description": "Norm points to a condition that must hold.",
            "source": "Norm",
            "target": "Condition",
            "properties": {},
        }
    ],
    "validation": {"required_provenance": True, "closed_world_types": False},
}


def test_normalize_legacy_schema_adds_additive_defaults():
    normalized = normalize_schema(LEGACY_SCHEMA)

    for node_type in normalized["node_types"]:
        assert node_type["properties"] == {}
        assert node_type["category"] is None
    for edge_type in normalized["edge_types"]:
        assert edge_type["properties"] == {}

    assert normalized["validation"] == {
        "required_provenance": False,
        "closed_world_types": False,
    }
    # legacy fields are untouched
    assert normalized["node_types"][0]["name"] == "Entity"
    assert normalized["edge_types"][0]["source"] == "Entity"


def test_normalize_does_not_mutate_input():
    original = copy.deepcopy(LEGACY_SCHEMA)
    normalize_schema(LEGACY_SCHEMA)
    assert LEGACY_SCHEMA == original


def test_normalize_preserves_declared_typed_properties():
    normalized = normalize_schema(TYPED_SCHEMA)

    norm_type = next(t for t in normalized["node_types"] if t["name"] == "Norm")
    assert norm_type["properties"]["modality"] == {
        "datatype": "string",
        "required": True,
    }
    assert norm_type["category"] == "RULE"
    assert normalized["validation"] == TYPED_SCHEMA["validation"]


def test_normalize_is_idempotent():
    once = normalize_schema(LEGACY_SCHEMA)
    twice = normalize_schema(once)
    assert once == twice

    once_typed = normalize_schema(TYPED_SCHEMA)
    twice_typed = normalize_schema(once_typed)
    assert once_typed == twice_typed


def test_validate_legacy_schema_has_no_issues():
    assert validate_schema(LEGACY_SCHEMA) == []


def test_validate_valid_typed_schema_has_no_issues():
    assert validate_schema(TYPED_SCHEMA) == []


def test_validate_rejects_invalid_identifier():
    schema = copy.deepcopy(LEGACY_SCHEMA)
    schema["node_types"][0]["name"] = "Bad Name!"

    issues = validate_schema(schema)

    assert any(
        issue["code"] == "invalid_identifier" and issue["type_name"] == "Bad Name!"
        for issue in issues
    )


def test_validate_rejects_invalid_property_identifier():
    schema = copy.deepcopy(TYPED_SCHEMA)
    schema["node_types"][0]["properties"]["bad property"] = {"datatype": "string"}

    issues = validate_schema(schema)

    assert any(
        issue["code"] == "invalid_identifier" and issue["property"] == "bad property"
        for issue in issues
    )


def test_validate_rejects_unknown_endpoint_type():
    schema = copy.deepcopy(LEGACY_SCHEMA)
    schema["edge_types"][0]["target"] = "Nonexistent"

    issues = validate_schema(schema)

    assert any(
        issue["code"] == "unknown_endpoint_type" and issue["type_name"] == "RELATED_TO"
        for issue in issues
    )


def test_validate_rejects_unsupported_datatype():
    schema = copy.deepcopy(TYPED_SCHEMA)
    schema["node_types"][0]["properties"]["modality"]["datatype"] = "object"

    issues = validate_schema(schema)

    assert any(
        issue["code"] == "unsupported_datatype" and issue["property"] == "modality"
        for issue in issues
    )


def test_validate_rejects_case_insensitive_duplicate_property():
    schema = copy.deepcopy(TYPED_SCHEMA)
    schema["node_types"][0]["properties"]["Modality"] = {"datatype": "string"}

    issues = validate_schema(schema)

    assert any(issue["code"] == "duplicate_property" for issue in issues)


def test_validate_rejects_duplicate_type_name():
    schema = copy.deepcopy(LEGACY_SCHEMA)
    schema["node_types"].append({"name": "entity", "description": "case-insensitive dupe"})

    issues = validate_schema(schema)

    assert any(issue["code"] == "duplicate_type" for issue in issues)


# --- validate_graph (Task 6: graph-shape and evidence validation) ---

GRAPH_SCHEMA = {
    "node_types": [
        {
            "name": "Coverage",
            "description": "d",
            "properties": {"amount": {"datatype": "number", "required": True}},
        },
        {"name": "Condition", "description": "d"},
        {"name": "Norm", "description": "d"},
    ],
    "edge_types": [
        {"name": "REQUIRES", "description": "d", "source": "Coverage", "target": "Condition"}
    ],
}


def _graph(nodes, edges=None):
    return {"nodes": nodes, "edges": edges or []}


def test_validate_graph_accepts_well_formed_graph():
    graph = _graph(
        [
            {"id": "c1", "type": "Coverage", "label": "암보장", "properties": {"amount": "50"}},
            {"id": "cond1", "type": "Condition", "label": "암 진단", "evidence_text": "암 진단 확정"},
        ],
        [{"source": "c1", "target": "cond1", "type": "REQUIRES"}],
    )
    assert validate_graph(GRAPH_SCHEMA, graph) == []


def test_validate_graph_flags_wrong_edge_endpoint_type():
    graph = _graph(
        [
            {"id": "c1", "type": "Coverage", "label": "암보장", "properties": {"amount": "50"}},
            {"id": "c2", "type": "Coverage", "label": "다른보장", "properties": {"amount": "10"}},
        ],
        [{"source": "c1", "target": "c2", "type": "REQUIRES"}],
    )
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert any(i["code"] == "wrong_endpoint_type" for i in issues)


def test_validate_graph_flags_missing_required_property():
    graph = _graph([{"id": "c1", "type": "Coverage", "label": "암보장"}])
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert any(i["code"] == "missing_required_property" and i["property"] == "amount" for i in issues)


def test_validate_graph_flags_invalid_numeric_value():
    graph = _graph([{"id": "c1", "type": "Coverage", "label": "암보장", "properties": {"amount": "fifty"}}])
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert any(i["code"] == "invalid_numeric_value" for i in issues)


def test_validate_graph_flags_duplicate_canonical_node():
    graph = _graph(
        [
            {"id": "c1", "type": "Coverage", "label": "암보장", "properties": {"amount": "50"}},
            {"id": "c2", "type": "Coverage", "label": "암보장", "properties": {"amount": "50"}},
        ]
    )
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert any(i["code"] == "duplicate_canonical_node" for i in issues)


def test_validate_graph_flags_missing_evidence_for_legal_types():
    graph = _graph([{"id": "n1", "type": "Norm", "label": "규정"}])
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert any(i["code"] == "missing_evidence" and i["type_name"] == "Norm" for i in issues)


def test_validate_graph_accepts_norm_with_evidence():
    graph = _graph([{"id": "n1", "type": "Norm", "label": "규정", "evidence_text": "규정 원문"}])
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert not any(i["code"] == "missing_evidence" for i in issues)


def test_validate_graph_does_not_require_evidence_for_non_legal_types():
    graph = _graph([{"id": "c1", "type": "Coverage", "label": "암보장", "properties": {"amount": "50"}}])
    issues = validate_graph(GRAPH_SCHEMA, graph)
    assert not any(i["code"] == "missing_evidence" for i in issues)
