#!/usr/bin/env bash
set -euo pipefail

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_dir/backups"}
retention_days=${BACKUP_RETENTION_DAYS:-14}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

cd "$project_dir"
docker compose exec -T postgres sh -c \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom' \
  > "$backup_dir/news_agent_$timestamp.dump"

find "$backup_dir" -type f -name 'news_agent_*.dump' -mtime "+$retention_days" -delete
