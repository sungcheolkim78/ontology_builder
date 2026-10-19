"""Applying a merge mapping to a schema and to the graph extracted under it: the
mechanics of a refinement. See CONTEXT.md ("Refinement", "Merge mapping",
"Candidate schema").

A merge mapping is `{"node_types": {old: new}, "edge_types": {old: new_or_None}}`:
each old node type is replaced by `new` (which stays in the schema), an old edge type
by another edge type, or, when mapped to None, simply removed along with its edges.
Nothing here decides *what* to merge or scores the result; it only makes the
candidate schema and the graph that goes with it, the same way for both, so the two
can never disagree. Applying a mapping never touches its input: `refine_version`
writes a new schema version and a new graph beside the old ones."""

import copy

from app.graph import graphdb

from .persistence import (
    SchemaNotFound,
    active_schema,
    create_schema_version,
    get_active_version,
    list_versions,
    load_schema,
    save_graph,
)


def validate_mapping(schema: dict, mapping: dict) -> None:
    """Raises ValueError, naming the problem, if `mapping` cannot be applied to
    `schema`: a type it does not have, a target that does not exist or is itself
    merged away (chains are refused, so what a mapping means never depends on the
    order it is applied in), a type merged into itself, or two edge types merged
    although their endpoints (after the node merges) differ."""
    nodes = {t["name"] for t in schema["node_types"]}
    edges = {t["name"]: t for t in schema["edge_types"]}
    node_map = mapping.get("node_types") or {}
    edge_map = mapping.get("edge_types") or {}

    for old, new in node_map.items():
        if old not in nodes:
            raise ValueError(f"node type {old!r} is not in the schema")
        if new not in nodes:
            raise ValueError(f"node type {old!r} is merged into {new!r}, which is not in the schema")
        if old == new:
            raise ValueError(f"node type {old!r} is merged into itself")
        if new in node_map:
            raise ValueError(f"node type {old!r} is merged into {new!r}, which is itself merged away (a chain)")

    def ends(name):
        edge = edges[name]
        return node_map.get(edge["source"], edge["source"]), node_map.get(edge["target"], edge["target"])

    for old, new in edge_map.items():
        if old not in edges:
            raise ValueError(f"edge type {old!r} is not in the schema")
        if new is None:
            continue
        if new not in edges:
            raise ValueError(f"edge type {old!r} is merged into {new!r}, which is not in the schema")
        if old == new:
            raise ValueError(f"edge type {old!r} is merged into itself")
        if new in edge_map:
            raise ValueError(f"edge type {old!r} is merged into {new!r}, which is itself merged away (a chain)")
        if ends(old) != ends(new):
            raise ValueError(
                f"edge type {old!r} cannot be merged into {new!r}: their endpoints differ ({ends(old)} vs {ends(new)})"
            )


def apply_mapping_to_schema(schema: dict, mapping: dict) -> dict:
    """The candidate schema: `schema` with the mapping applied. A merged node type's
    description is added to its target's, and its properties unioned into the target's
    (the target's own declaration wins on a clash); edge types follow their endpoints
    to the surviving node types."""
    validate_mapping(schema, mapping)
    node_map = mapping.get("node_types") or {}
    edge_map = mapping.get("edge_types") or {}
    result = copy.deepcopy(schema)

    merged_in: dict[str, list[dict]] = {}
    for node_type in result["node_types"]:
        if node_type["name"] in node_map:
            merged_in.setdefault(node_map[node_type["name"]], []).append(node_type)
    kept = []
    for node_type in result["node_types"]:
        if node_type["name"] in node_map:
            continue
        for other in merged_in.get(node_type["name"], []):
            node_type["description"] = f"{node_type.get('description', '')} / {other.get('description', '')}".strip(" /")
            node_type["properties"] = {**(other.get("properties") or {}), **(node_type.get("properties") or {})}
        kept.append(node_type)
    result["node_types"] = kept

    edges = []
    for edge_type in result["edge_types"]:
        if edge_type["name"] in edge_map:
            continue
        edge_type["source"] = node_map.get(edge_type["source"], edge_type["source"])
        edge_type["target"] = node_map.get(edge_type["target"], edge_type["target"])
        edges.append(edge_type)
    result["edge_types"] = edges
    return result


def apply_mapping_to_graph(graph: dict, mapping: dict) -> dict:
    """`graph` with the mapping applied. Nodes take their new type; nodes that thereby
    become the same node (same type and label) are one, keeping whatever either knew
    (the first's value wins, a gap is filled from the other). Edges follow to the
    surviving node and take their new type; those of a dropped type, duplicates, and
    edges that the merge turned into a self-loop are removed."""
    node_map = mapping.get("node_types") or {}
    edge_map = mapping.get("edge_types") or {}

    canonical: dict[tuple, dict] = {}
    id_map: dict[str, str] = {}
    nodes = []
    for node in graph["nodes"]:
        merged = {**node, "type": node_map.get(node["type"], node["type"])}
        key = (merged["type"], merged["label"])
        if key in canonical:
            keeper = canonical[key]
            for field, value in merged.items():
                if field != "id" and value and not keeper.get(field):
                    keeper[field] = value
            id_map[node["id"]] = keeper["id"]
            continue
        canonical[key] = merged
        id_map[node["id"]] = merged["id"]
        nodes.append(merged)

    seen = set()
    edges = []
    for edge in graph["edges"]:
        new_type = edge_map.get(edge["type"], edge["type"])
        if new_type is None:
            continue
        source, target = id_map.get(edge["source"]), id_map.get(edge["target"])
        if source is None or target is None:
            continue
        if source == target and edge["source"] != edge["target"]:
            continue  # two nodes became one, and the edge between them with them
        key = (source, target, new_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append({**edge, "type": new_type, "source": source, "target": target})
    return {"nodes": nodes, "edges": edges}


def refine_version(stem: str, mapping: dict, version: int | None = None) -> dict:
    """Applies `mapping` to a document's schema version (default: the active one) and
    to its graph if it has one, saving both as a new schema version, which becomes the
    active one; the old version and its graph are left as they were. Returns the new
    `version`, the `from_version`, and before/after counts of node types, edge types
    and (None without a graph) nodes and edges. Raises ValueError for a mapping that
    cannot be applied, SchemaNotFound if the version does not exist."""
    if version is None:
        version, schema = active_schema(stem)
    else:
        schema = load_schema(stem, version)
        if schema is None:
            raise SchemaNotFound(stem)
    candidate = apply_mapping_to_schema(schema, mapping)
    graph = graphdb.load_graph(stem, version=version)
    document_type = next((v["document_type"] for v in list_versions(stem) if v["version"] == version), "general")

    new_version = create_schema_version(stem, candidate, document_type=document_type)
    refined = None
    if graph is not None:
        refined = apply_mapping_to_graph(graph, mapping)
        save_graph(stem, refined, version=new_version)

    def counts(before, after):
        return {"before": len(before), "after": len(after)}

    return {
        "from_version": version,
        "version": new_version,
        "node_types": counts(schema["node_types"], candidate["node_types"]),
        "edge_types": counts(schema["edge_types"], candidate["edge_types"]),
        "nodes": counts(graph["nodes"], refined["nodes"]) if refined else None,
        "edges": counts(graph["edges"], refined["edges"]) if refined else None,
    }


def unfinished_copy(stem: str, source_version: int | None = None) -> tuple[int, int] | None:
    """`(active, source)` if the active version is a copy of another version's schema
    that never got a graph -- what an extraction that stopped partway (a provider error in
    one chunk group) leaves behind -- else None. `source` is the newest other version
    with the identical schema, or only `source_version` when one is given. Extracting
    again under the active version, not a fresh copy, picks up the chunk groups already
    finished (the resume cache is keyed by the schema's content, not its version) and
    leaves no empty version behind."""
    active = get_active_version(stem)
    if active is None or graphdb.load_graph(stem, version=active) is not None:
        return None
    schema = load_schema(stem, active)
    twins = [v["version"] for v in list_versions(stem) if v["version"] != active and load_schema(stem, v["version"]) == schema]
    if source_version is not None:
        twins = [v for v in twins if v == source_version]
    return (active, max(twins)) if twins else None
