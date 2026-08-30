#!/usr/bin/env bash
set -Eeuo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_dir/backups"}
retention_days=${BACKUP_RETENTION_DAYS:-14}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
umask 077

cd "$project_dir"
backup_path="$backup_dir/news_agent_$timestamp.dump"
backup_partial="$backup_path.partial"
if ! docker compose exec -T postgres sh -ec \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom' \
  > "$backup_partial"; then
  rm -f "$backup_partial"
  exit 1
fi

test -s "$backup_partial"
mv "$backup_partial" "$backup_path"

find "$backup_dir" -type f -name 'news_agent_*.dump' -mtime "+$retention_days" -delete
printf '%s\n' "$backup_path"
