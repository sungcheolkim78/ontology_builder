"""One-time migration: moves backend/data from the old flat layout
({stem}_raw.md at the data/ root, per-document metadata under
graph/{stem}/, chunks under chunks/{stem}.json) into the new
documents/{stem}/ layout (raw.md, manifest.json, summary.json,
discovery.json, chunks.json, versions.json, schema_v{N}.json all in one
folder per document). See docs/data-layout-proposal.md for the full
rationale. graph/graph.ladybugdb and domain_schemas/ are untouched --
both are cross-document/global data, not per-document.

Run once, with podman-compose stopped (this script and the backend
container must never have graph.ladybugdb open at the same time -- see
CLAUDE.md's virtiofs/WAL notes; this script doesn't touch that file, but
the backend container must not be serving requests against the old
layout while this script moves files out from under it). Run
./scripts/backup_data.sh first.

Usage:
    cd backend && source .venv/bin/activate
    python migrate_data_layout.py --dry-run   # print the move plan only
    python migrate_data_layout.py             # actually move files

Safe to re-run -- any document whose documents/{stem}/raw.md already
exists is skipped.
"""
import argparse
import shutil
from pathlib import Path

from app.paths import data_dir, document_dir_for, documents_dir


def _old_graph_dir() -> Path:
    return data_dir() / "graph"


def _old_chunks_dir() -> Path:
    return data_dir() / "chunks"


def plan_moves() -> list[tuple[str, Path, Path]]:
    """Returns (description, source, destination) triples, in the order
    they should be applied. Only includes moves whose destination doesn't
    already exist (idempotent re-run)."""
    moves = []

    for md_file in sorted(data_dir().glob("*.md")):
        stem = md_file.stem
        dest = document_dir_for(stem) / "raw.md"
        if dest.exists():
            continue
        moves.append((f"{stem}: raw.md", md_file, dest))

    old_graph_dir = _old_graph_dir()
    if old_graph_dir.is_dir():
        for stem_dir in sorted(old_graph_dir.iterdir()):
            if not stem_dir.is_dir():
                continue  # skips graph.ladybugdb itself (a file, not a dir)
            for artifact in sorted(stem_dir.iterdir()):
                dest = document_dir_for(stem_dir.name) / artifact.name
                if dest.exists():
                    continue
                moves.append((f"{stem_dir.name}: {artifact.name}", artifact, dest))

    old_chunks_dir = _old_chunks_dir()
    if old_chunks_dir.is_dir():
        for chunk_file in sorted(old_chunks_dir.glob("*.json")):
            stem = chunk_file.stem
            dest = document_dir_for(stem) / "chunks.json"
            if dest.exists():
                continue
            moves.append((f"{stem}: chunks.json", chunk_file, dest))

    return moves


def apply_moves(moves: list[tuple[str, Path, Path]]) -> None:
    for description, source, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        print(f"{description}: {source} -> {dest}")


def cleanup_empty_old_dirs() -> None:
    for old_dir in (_old_graph_dir(), _old_chunks_dir()):
        if not old_dir.is_dir():
            continue
        for stem_dir in list(old_dir.iterdir()):
            if stem_dir.is_dir() and not any(stem_dir.iterdir()):
                stem_dir.rmdir()
                print(f"removed now-empty {stem_dir}")
        if old_dir.name == "chunks" and not any(old_dir.iterdir()):
            old_dir.rmdir()
            print(f"removed now-empty {old_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    moves = plan_moves()
    if not moves:
        print("Nothing to migrate -- already on the new layout.")
        return

    print(f"{len(moves)} file(s) to move:")
    for description, source, dest in moves:
        print(f"  {description}: {source} -> {dest}")

    if args.dry_run:
        print("\n--dry-run: no files were moved.")
        return

    apply_moves(moves)
    cleanup_empty_old_dirs()
    print(f"\nMigration complete. {len(moves)} file(s) moved into {documents_dir()}.")


if __name__ == "__main__":
    main()
