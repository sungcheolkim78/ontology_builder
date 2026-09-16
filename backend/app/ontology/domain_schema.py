import json
from datetime import datetime
from pathlib import Path

from app.utils.paths import data_dir
from .schema_validation import SCHEMA_CONTRACT_VERSION, summarize_validation_issues, validate_schema

from .extraction import converge_domain_schema, generate_schema
from .persistence import _apply_schema_type_changes, create_schema_version

# Domain schema storage/reuse -----------------------------------------------
#
# converge_domain_schema() (app.ontology.extraction) is a pure function -- it
# takes a schema in and returns one out, with no notion of "the schema for
# domain X" persisting between calls. This module adds that persistence,
# separate from the per-document schema_v{N}.json layout in
# app.ontology.persistence: a domain schema belongs to a domain (e.g.
# "insurance_policy"), not to any one document, and is meant to be reused
# across every document in that domain via use_domain_schema() rather than
# regenerated per document. See
# docs/ontology/domain_schema_convergence.md section 4.
DOMAIN_SCHEMA_DIR = data_dir() / "domain_schemas"


def domain_dir_for(domain: str) -> Path:
    return DOMAIN_SCHEMA_DIR / domain


def domain_schema_path(domain: str) -> Path:
    return domain_dir_for(domain) / "schema.json"


def save_domain_schema(domain: str, schema: dict) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    domain_schema_path(domain).write_text(json.dumps(schema, ensure_ascii=False))


def load_domain_schema(domain: str) -> dict | None:
    path = domain_schema_path(domain)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def list_domains() -> list[str]:
    if not DOMAIN_SCHEMA_DIR.is_dir():
        return []
    return sorted(
        p.name for p in DOMAIN_SCHEMA_DIR.iterdir() if p.is_dir() and (p / "schema.json").is_file()
    )


def _domain_manifest_path(domain: str) -> Path:
    return domain_dir_for(domain) / "manifest.json"


def _load_domain_manifest(domain: str) -> dict:
    path = _domain_manifest_path(domain)
    if not path.is_file():
        return {"calibration_stems": [], "history": []}
    return json.loads(path.read_text())


def _save_domain_manifest(domain: str, manifest: dict) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    _domain_manifest_path(domain).write_text(json.dumps(manifest, ensure_ascii=False))


def domain_calibration_stems(domain: str) -> list[str]:
    return _load_domain_manifest(domain)["calibration_stems"]


def domain_convergence_history(domain: str) -> list[dict]:
    return _load_domain_manifest(domain)["history"]


def _domain_pending_review_path(domain: str) -> Path:
    return domain_dir_for(domain) / "pending_review.json"


def load_domain_pending_review(domain: str) -> list[dict]:
    path = _domain_pending_review_path(domain)
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def _save_domain_pending_review(domain: str, items: list[dict]) -> None:
    d = domain_dir_for(domain)
    d.mkdir(parents=True, exist_ok=True)
    _domain_pending_review_path(domain).write_text(json.dumps(items, ensure_ascii=False))


def run_domain_convergence(domain: str, documents: list[dict], max_chars: int | None = None) -> dict:
    """Runs converge_domain_schema() over `documents` and persists the
    result under backend/data/domain_schemas/{domain}/. If `domain` already
    has a stored schema, that schema is the seed and every document in
    `documents` is folded in -- calling this again later with newly
    calibrated documents keeps refining the same domain schema rather than
    starting over. If `domain` has no stored schema yet, `documents[0]`
    seeds it (via generate_schema) and the rest are folded in, exactly like
    a fresh converge_domain_schema() call.

    NEEDS_HUMAN_REVIEW changes accumulate in the domain's pending_review
    store across calls (not just this one) until apply_domain_schema_changes
    resolves them, since they were never applied to the schema."""
    existing_schema = load_domain_schema(domain)
    if existing_schema is not None:
        seed_schema = existing_schema
        remaining = documents
    else:
        if not documents:
            raise ValueError(f"no domain schema stored for {domain!r} and no documents to seed one from")
        seed_schema = generate_schema(documents[0]["text"], max_chars=max_chars)
        remaining = documents[1:]

    result = converge_domain_schema(remaining, seed_schema, max_chars=max_chars)
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

    return {**result, "domain": domain, "seed_schema": seed_schema}


def apply_domain_schema_changes(domain: str, changes: list) -> dict:
    """Applies a human-reviewed subset of a domain's accumulated
    pending_review changes (same contract as apply_evolution: the caller is
    expected to have already filtered `changes` down to what a person
    accepted) and removes exactly those change_ids from the pending queue."""
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
