#!/usr/bin/env bash
# Restores a backend/data snapshot created by backup_data.sh. Refuses to run
# if backend/data already exists, so a restore can't silently clobber
# whatever's currently there -- move or remove it first if you really mean to
# overwrite it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="$repo_root/backups"

archive="${1:-}"
if [ -z "$archive" ]; then
  echo "Usage: $0 <path-to-backup.tar.gz>" >&2
  echo "Available backups in $backup_dir:" >&2
  ls -1t "$backup_dir"/backend-data_*.tar.gz 2>/dev/null >&2 || echo "  (none found)" >&2
  exit 1
fi

if [ ! -f "$archive" ]; then
  echo "Backup file not found: $archive" >&2
  exit 1
fi

data_dir="$repo_root/backend/data"
if [ -d "$data_dir" ]; then
  echo "backend/data already exists -- refusing to overwrite. Move or remove it first." >&2
  exit 1
fi

tar -xzf "$archive" -C "$repo_root/backend"
echo "Restored $archive -> $data_dir"
