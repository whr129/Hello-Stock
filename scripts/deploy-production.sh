#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEPLOY_DIR="${DEPLOY_DIR:-$HOME/news-agent}"
readonly ENV_FILE="$DEPLOY_DIR/.env"
readonly MIN_DISK_KIB="${DEPLOY_MIN_FREE_DISK_KIB:-10485760}"
readonly MIN_TOTAL_MEMORY_KIB="${DEPLOY_MIN_TOTAL_MEMORY_KIB:-3584000}"
readonly MIN_AVAILABLE_MEMORY_KIB="${DEPLOY_MIN_AVAILABLE_MEMORY_KIB:-786432}"
readonly STARTUP_WAIT_SECONDS="${DEPLOY_STARTUP_WAIT_SECONDS:-20}"

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_env_value() {
  local key="$1"
  awk -v key="$key" '
    $0 !~ /^[[:space:]]*#/ {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line ~ "^" key "[[:space:]]*=") {
        sub("^" key "[[:space:]]*=[[:space:]]*", "", line)
        if (length(line) > 0 && line != "\"\"" && line != "\047\047") found = 1
      }
    }
    END { exit(found ? 0 : 1) }
  ' "$ENV_FILE" || fail "$key must have a non-empty value in $ENV_FILE"
}

container_id() {
  docker compose --profile app ps -q "$1"
}

assert_running() {
  local service="$1"
  local id state
  id="$(container_id "$service")"
  [[ -n "$id" ]] || fail "$service has no container after rollout"
  state="$(docker inspect --format '{{.State.Status}}' "$id")"
  [[ "$state" == "running" ]] || fail "$service is $state after rollout"
}

assert_expected_image() {
  local service="$1"
  local id configured_image
  id="$(container_id "$service")"
  configured_image="$(docker inspect --format '{{.Config.Image}}' "$id")"
  [[ "$configured_image" == "$NEWS_AGENT_IMAGE" ]] || \
    fail "$service is using $configured_image instead of $NEWS_AGENT_IMAGE"
}

[[ -n "${NEWS_AGENT_IMAGE:-}" ]] || fail "NEWS_AGENT_IMAGE is required"
[[ "$NEWS_AGENT_IMAGE" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || \
  fail "NEWS_AGENT_IMAGE must be an immutable ghcr.io image digest"
command -v docker >/dev/null || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "the Docker Compose plugin is not installed"
[[ -d "$DEPLOY_DIR" ]] || fail "deployment directory does not exist: $DEPLOY_DIR"
cd "$DEPLOY_DIR"
[[ -f docker-compose.yml ]] || fail "$DEPLOY_DIR/docker-compose.yml is missing"
[[ -f "$ENV_FILE" ]] || fail "$ENV_FILE is missing"
[[ ! -L "$ENV_FILE" ]] || fail "$ENV_FILE must not be a symbolic link"
if env_mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null)"; then
  :
else
  env_mode="$(stat -f '%Lp' "$ENV_FILE")"
fi
(( (8#$env_mode & 077) == 0 )) || fail "$ENV_FILE must not be accessible by group or others"
require_env_value TELEGRAM_BOT_TOKEN
require_env_value OPENAI_API_KEY
require_env_value POSTGRES_PASSWORD

if (( MIN_TOTAL_MEMORY_KIB > 0 || MIN_AVAILABLE_MEMORY_KIB > 0 )); then
  [[ -r /proc/meminfo ]] || fail "/proc/meminfo is required to validate host memory"
  mem_total_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  mem_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  [[ "$mem_total_kib" -ge "$MIN_TOTAL_MEMORY_KIB" ]] || \
    fail "host has $((mem_total_kib / 1024)) MiB RAM; at least $((MIN_TOTAL_MEMORY_KIB / 1024)) MiB is required"
  [[ "$mem_available_kib" -ge "$MIN_AVAILABLE_MEMORY_KIB" ]] || \
    fail "host has $((mem_available_kib / 1024)) MiB available; at least $((MIN_AVAILABLE_MEMORY_KIB / 1024)) MiB is required"
fi

free_disk_kib="$(df -Pk "$DEPLOY_DIR" | awk 'NR == 2 {print $4}')"
[[ "$free_disk_kib" -ge "$MIN_DISK_KIB" ]] || \
  fail "only $((free_disk_kib / 1024 / 1024)) GiB is free; at least $((MIN_DISK_KIB / 1024 / 1024)) GiB is required"

export NEWS_AGENT_IMAGE
docker compose --profile app config --quiet || fail "Docker Compose or .env validation failed"

log "Pulling $NEWS_AGENT_IMAGE"
docker compose --profile app pull postgres migrate bot scheduler

log "Starting PostgreSQL and waiting for its health check"
docker compose up -d --no-build postgres
postgres_id="$(container_id postgres)"
[[ -n "$postgres_id" ]] || fail "PostgreSQL container was not created"
for _ in $(seq 1 30); do
  postgres_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$postgres_id")"
  [[ "$postgres_health" == "healthy" ]] && break
  sleep 2
done
[[ "${postgres_health:-}" == "healthy" ]] || fail "PostgreSQL did not become healthy"

log "Backing up PostgreSQL"
bash scripts/backup-database.sh

log "Stopping application containers before migration"
for service in bot scheduler; do
  [[ -z "$(container_id "$service")" ]] || docker compose --profile app stop --timeout 30 "$service"
done

if (( MIN_AVAILABLE_MEMORY_KIB > 0 )); then
  mem_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  [[ "$mem_available_kib" -ge "$MIN_AVAILABLE_MEMORY_KIB" ]] || \
    fail "available memory remained below $((MIN_AVAILABLE_MEMORY_KIB / 1024)) MiB after stopping the application"
fi

log "Running database migration once"
docker compose --profile app run --rm --no-deps migrate

log "Starting bot and scheduler"
docker compose --profile app up -d --no-build --no-deps --force-recreate bot scheduler
sleep "$STARTUP_WAIT_SECONDS"

assert_running postgres
assert_running bot
assert_running scheduler
postgres_health="$(docker inspect --format '{{.State.Health.Status}}' "$(container_id postgres)")"
[[ "$postgres_health" == "healthy" ]] || fail "PostgreSQL is not healthy after rollout"
assert_expected_image bot
assert_expected_image scheduler

log "Deployment succeeded: $NEWS_AGENT_IMAGE"
docker compose --profile app ps
