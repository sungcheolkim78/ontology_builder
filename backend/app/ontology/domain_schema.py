from datetime import datetime

from app.utils.paths import (  # noqa: F401 -- re-exported: callers import these from here
    domain_dir_for,
    domain_manifest_path as _domain_manifest_path,
    domain_pending_review_path as _domain_pending_review_path,
    domain_schema_path,
    domain_schemas_dir,
)
from app.utils.store import locked, read_json, write_json
from .schema_validation import SCHEMA_CONTRACT_VERSION, summarize_validation_issues, validate_schema

from .evolve_graph import converge_domain_schema
from .persistence import _apply_schema_type_changes, create_schema_version

# Domain schema storage/reuse -----------------------------------------------
#
# converge_domain_schema() (app.ontology.evolve_graph) is a pure function -- it
# takes a schema in and returns one out, with no notion of "the schema for
# domain X" persisting between calls. This module adds that persistence,
# separate from the per-document schema_v{N}.json layout in
# app.ontology.persistence: a domain schema belongs to a domain (e.g.
# "insurance_policy"), not to any one document, and is meant to be reused
# across every document in that domain via use_domain_schema() rather than
# regenerated per document. See
# docs/ontology/domain_schema_convergence.md section 4.
def save_domain_schema(domain: str, schema: dict) -> None:
    write_json(domain_schema_path(domain), schema)


def load_domain_schema(domain: str) -> dict | None:
    return read_json(domain_schema_path(domain))


def list_domains() -> list[str]:
    if not domain_schemas_dir().is_dir():
        return []
    return sorted(
        p.name for p in domain_schemas_dir().iterdir() if p.is_dir() and (p / "schema.json").is_file()
    )


def _load_domain_manifest(domain: str) -> dict:
    # A fresh default on every call: callers append to its lists.
    return read_json(_domain_manifest_path(domain), {"calibration_stems": [], "history": []})


def _save_domain_manifest(domain: str, manifest: dict) -> None:
    write_json(_domain_manifest_path(domain), manifest)


def domain_calibration_stems(domain: str) -> list[str]:
    return _load_domain_manifest(domain)["calibration_stems"]


def domain_convergence_history(domain: str) -> list[dict]:
    return _load_domain_manifest(domain)["history"]


def load_domain_pending_review(domain: str) -> list[dict]:
    return read_json(_domain_pending_review_path(domain), [])


def _save_domain_pending_review(domain: str, items: list[dict]) -> None:
    write_json(_domain_pending_review_path(domain), items)


def _domain_lock(domain: str):
    """Serializes whatever reads and rewrites a domain's schema, manifest or
    pending-review queue -- the key is separate from any document's stem."""
    return locked(f"domain:{domain}")


def run_domain_convergence(
    domain: str,
    documents: list[dict],
    document_type: str = "general",
    max_chars: int | None = None,
) -> dict:
    """Runs converge_domain_schema() over `documents` and persists the
    result under backend/data/domain_schemas/{domain}/. If `domain` already
    has a stored schema, that schema is the seed and every document in
    `documents` is folded in -- calling this again later with newly
    calibrated documents keeps refining the same domain schema rather than
    starting over. If `domain` has no stored schema yet, `documents[0]`
    seeds it (via generate_schema, with `document_type`'s prompt -- which
    plays no part once a domain has a stored schema) and the rest are folded
    in, exactly like a fresh converge_domain_schema() call.

    NEEDS_HUMAN_REVIEW changes accumulate in the domain's pending_review
    store across calls (not just this one) until apply_domain_schema_changes
    resolves them, since they were never applied to the schema."""
    with _domain_lock(domain):
        result = converge_domain_schema(
            documents,
            seed_schema=load_domain_schema(domain),
            document_type=document_type,
            max_chars=max_chars,
        )
        save_domain_schema(domain, result["schema"])

        manifest = _load_domain_manifest(domain)
        stems = [doc["stem"] for doc in documents]
        manifest["calibration_stems"] = sorted(set(manifest["calibration_stems"]) | set(stems))
        manifest["history"].append(
            {
                "stems": stems,
                "changes_applied_count": sum(len(it["changes_applied"]) for it in result["iterations"]),
                "changes_pending_review_count": len(result["pending_review"]),
                "converged_at": datetime.now().isoformat(),
                "schema_contract_version": SCHEMA_CONTRACT_VERSION,
                "schema_validation_summary": summarize_validation_issues(
                    validate_schema(result["schema"])
                ),
            }
        )
        _save_domain_manifest(domain, manifest)

        if result["pending_review"]:
            pending = load_domain_pending_review(domain)
            pending.extend(result["pending_review"])
            _save_domain_pending_review(domain, pending)

        return {**result, "domain": domain}


def apply_domain_schema_changes(domain: str, changes: list) -> dict:
    """Applies a human-reviewed subset of a domain's accumulated
    pending_review changes (same contract as apply_evolution: the caller is
    expected to have already filtered `changes` down to what a person
    accepted) and removes exactly those change_ids from the pending queue."""
    with _domain_lock(domain):
        schema = load_domain_schema(domain)
        if schema is None:
            raise ValueError(f"no domain schema stored for {domain!r}")
        new_schema = _apply_schema_type_changes(schema, changes)
        save_domain_schema(domain, new_schema)

        applied_ids = {c.get("change_id") for c in changes}
        remaining = [c for c in load_domain_pending_review(domain) if c.get("change_id") not in applied_ids]
        _save_domain_pending_review(domain, remaining)
        return {"schema": new_schema, "pending_review": remaining}


def use_domain_schema(stem: str, domain: str, document_type: str = "general") -> int:
    """Copies domain `domain`'s current schema onto document `stem` as a new
    schema version -- the reuse half of this feature, mirroring how
    main.py's existing /schema/use endpoint copies one document's schema
    onto another, except the source is a domain schema rather than another
    document's."""
    schema = load_domain_schema(domain)
    if schema is None:
        raise ValueError(f"no domain schema stored for {domain!r}")
    return create_schema_version(stem, schema, document_type=document_type)
