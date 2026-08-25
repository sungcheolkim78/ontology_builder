#!/usr/bin/env bash
# Snapshots backend/data (parsed markdown, schema.json files, graph.ladybugdb)
# into a single timestamped tarball under backups/, so past extraction work
# survives accidents (this exists because a test run once wiped the real
# backend/data directory -- see CLAUDE.md).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="$repo_root/backend/data"
backup_dir="$repo_root/backups"

if [ ! -d "$data_dir" ]; then
  echo "No backend/data directory found at $data_dir -- nothing to back up." >&2
  exit 1
fi

mkdir -p "$backup_dir"
timestamp="$(date +%Y%m%d_%H%M%S)"
archive="$backup_dir/backend-data_${timestamp}.tar.gz"

tar -czf "$archive" -C "$repo_root/backend" data

echo "Backed up $data_dir -> $archive"
ls -lh "$archive"
