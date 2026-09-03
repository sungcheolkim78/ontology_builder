#!/usr/bin/env bash
# Unpacks samples/data_raw_md.tar.gz, samples/data_chunks.tar.gz, and
# samples/goldenset.tar.gz into backend/data/documents/{stem}_raw/, the
# per-document layout app.paths.document_dir_for expects (raw.md,
# chunks.json, goldenset.json alongside each other) -- see CLAUDE.md's
# backend module notes on document_dir_for. Each archive's top-level
# manifest.json/goldenset.jsonl/prepare_goldenset.log describe the whole
# collection and aren't per-document artifacts, so they're left where they
# extract and never copied into backend/data/documents.
#
# {stem}_raw is app.parser's own convention (parse_to_markdown_file's
# out_stem): a document uploaded as "{stem}.pdf" ends up in
# documents/{stem}_raw/. The sample archives' filenames already match that
# stem (e.g. md/보장성/삼성정기보험_2403_약관.md), so this script only needs
# to append "_raw" to each file's basename to find (or create) its folder.
#
# Safe to re-run -- existing backend/data/documents/{stem}_raw folders are
# reused, and files are overwritten with the archives' copies.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
samples_dir="$repo_root/samples"
documents_dir="$repo_root/backend/data/documents"

mkdir -p "$documents_dir"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

unpack_kind() {
  # $3 (extension) is a literal trailing-string match, not a glob suffix --
  # ".golden.json" for the goldenset archive matches only its *.golden.json
  # files (never the collection-level manifest.json/goldenset.jsonl
  # alongside them) and basename strips the whole thing in one go, so no
  # separate suffix-stripping step is needed.
  local archive="$1" top_dir="$2" extension="$3" dest_filename="$4"
  if [ ! -f "$archive" ]; then
    echo "skip: $archive not found" >&2
    return
  fi

  tar -xzf "$archive" -C "$work_dir"

  local count=0
  while IFS= read -r -d '' file; do
    local base="$(basename "$file" "$extension")"
    local dest_dir="$documents_dir/${base}_raw"
    mkdir -p "$dest_dir"
    cp "$file" "$dest_dir/$dest_filename"
    count=$((count + 1))
  done < <(find "$work_dir/$top_dir" -mindepth 2 -name "*$extension" -print0)

  echo "$(basename "$archive"): $count file(s) -> documents/{stem}_raw/$dest_filename"
}

unpack_kind "$samples_dir/data_raw_md.tar.gz" "md" ".md" "raw.md"
unpack_kind "$samples_dir/data_chunks.tar.gz" "chunks" ".json" "chunks.json"
unpack_kind "$samples_dir/goldenset.tar.gz" "goldenset" ".golden.json" "goldenset.json"

echo "Done. $documents_dir now has $(ls "$documents_dir" | wc -l | tr -d ' ') document folder(s)."
