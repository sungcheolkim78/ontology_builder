import copy

from app.schema_validation import normalize_schema, validate_schema

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
