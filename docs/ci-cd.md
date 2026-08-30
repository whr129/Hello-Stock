# CI/CD and production deployment

## Continuous integration

`.github/workflows/ci.yml` runs for pull requests, manual dispatches, and direct pushes to
`agent/**` or `codex/**`. A push to `main` does not start duplicate CI; the publishing workflow
calls CI before building an image.

CI runs Ruff on Python 3.12, pytest on Python 3.11 and 3.12, Alembic against PostgreSQL 16 with
pgvector, and a `linux/amd64` container build. The publishing caller skips the preliminary image
build and performs one authoritative build after validation. Protect `main` with the `Ruff`, both
`Tests (Python ...)`, and `Container build` pull-request checks.

## Publishing and automatic deployment

`.github/workflows/publish-container.yml` runs for `main`, `v*` tags, and manual dispatches. It
publishes one `linux/amd64` image to GHCR with branch/tag, SHA, and (on `main`) `latest` tags.
Compilation, tests, SBOM generation, and building happen on GitHub-hosted runners.

A successful `main` publish deploys the exact
`ghcr.io/<owner>/<repository>@sha256:<digest>` through the GitHub `production` Environment. Tags
publish but never deploy. Package-write permission belongs only to the publish job.

Configure these `production` Environment secrets:

- `DEPLOY_HOST`: VPS hostname or address.
- `DEPLOY_PORT`: SSH port, normally `22`.
- `DEPLOY_USER`: unprivileged Docker-capable account.
- `DEPLOY_SSH_PRIVATE_KEY`: its dedicated private key.
- `DEPLOY_SSH_KNOWN_HOSTS`: an out-of-band-verified host-key line.

The workflow uploads only Compose and the deployment scripts to `~/news-agent`. Application and
database secrets stay exclusively in `~/news-agent/.env`. The GHCR package must be public, or the
server must already be authenticated using a read-only package token:

```bash
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

Do not put that token in the project `.env`; Docker stores it in the deployment user's Docker
configuration.

## Server setup

Install Docker Engine and the Compose plugin on a 64-bit x86 Linux server. Add the deployment
user to Docker's group, then create a private environment file:

```bash
install -d -m 700 ~/news-agent
install -m 600 /dev/null ~/news-agent/.env
editor ~/news-agent/.env
```

At minimum it needs non-empty `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and `POSTGRES_PASSWORD`,
plus enabled provider keys:

```dotenv
TELEGRAM_BOT_TOKEN=<secret>
OPENAI_API_KEY=<secret>
POSTGRES_DB=news_agent
POSTGRES_USER=news_agent
POSTGRES_PASSWORD=<output from: openssl rand -hex 32>
```

Use a hexadecimal database password because Compose inserts it into the internal database URL.
`POSTGRES_PASSWORD` initializes the role only when the volume is new; changing the file does not
rotate an existing role.

Do not copy a local-development `.env` to production. The deployment rejects group/world-accessible
`.env` files. It validates Compose, an approximately 4 GB host, 768 MiB currently available
memory, and 10 GiB free disk. Override thresholds only when necessary with
`DEPLOY_MIN_TOTAL_MEMORY_KIB`, `DEPLOY_MIN_AVAILABLE_MEMORY_KIB`, or
`DEPLOY_MIN_FREE_DISK_KIB` in the SSH user's environment.

PostgreSQL has no host port and is reachable only through the internal Docker network. The bot
and scheduler also join Docker's default network for outbound provider and Telegram access.

## Rollout and downtime

For each successful `main` image, `scripts/deploy-production.sh`:

1. Validates the immutable digest, server, `.env`, and Compose configuration.
2. Pulls that digest and starts or verifies PostgreSQL.
3. Creates a PostgreSQL custom-format backup in `~/news-agent/backups`.
4. Stops bot and scheduler so old and new application containers do not overlap.
5. Runs `alembic upgrade head` once in a disposable migration container.
6. Recreates bot and scheduler and verifies PostgreSQL health, process state, and running digest.

Brief downtime is expected. A failed backup or migration leaves the application stopped and
returns a failure to GitHub. Repair the issue and rerun the workflow or the same digest.

## Four-gigabyte resource envelope

| Service | Memory limit | PID limit |
| --- | ---: | ---: |
| PostgreSQL | 768 MiB | 200 |
| Bot | 640 MiB | 256 |
| Scheduler | 1024 MiB | 384 |
| One-shot migration | 512 MiB | 128 |

The steady stack is capped at 2432 MiB (2.375 GiB), leaving about 1.625 GiB for Linux, Docker,
SSH, and light services. During deployment, PostgreSQL plus migration is capped at 1280 MiB.
Every service rotates JSON logs at 10 MiB times three files. PostgreSQL uses
`shared_buffers=128MB`, `work_mem=4MB`, `maintenance_work_mem=64MB`, and `max_connections=40`.

Expected usage is 150–350 MiB for PostgreSQL, 180–350 MiB for bot, and 220–400 MiB for an idle
scheduler (450–800 MiB during refresh). The stack should typically use 550 MiB–1.5 GiB and the
whole server 1.3–2.8 GiB. Idle CPU is low and scheduler work can approach one core; 2 vCPU is
adequate. An unpacked runtime image should use about 0.8–1.5 GB. A 120 GB disk is sufficient with
log rotation, 14-day backup retention, and periodic unused-image pruning.

## Monitoring, backup, and restore

```bash
cd ~/news-agent
docker compose --profile app ps
docker compose --profile app logs --tail=200 bot scheduler
docker stats --no-stream
docker inspect --format '{{.Config.Image}}' "$(docker compose --profile app ps -q bot)"
ls -lh backups/
df -h .
```

Backups use PostgreSQL custom format. A partial file is promoted only when non-empty; completed
backups older than `BACKUP_RETENTION_DAYS` (14 by default) are deleted after a successful new
backup. Copy backups off-host if the data cannot be recreated. The helper can also be scheduled:

```cron
0 3 * * * /home/<deploy-user>/news-agent/scripts/backup-database.sh >> /home/<deploy-user>/news-agent/backup.log 2>&1
```

Test restoration on a copy first. Stop application containers, recreate or empty the target
database as appropriate, then restore:

```bash
cd ~/news-agent
docker compose --profile app stop bot scheduler
docker compose exec -T postgres pg_restore \
  --username news_agent --dbname news_agent --clean --if-exists \
  < backups/news_agent_YYYYMMDDTHHMMSSZ.dump
```

After the first live rollout, confirm the digest and a fresh non-empty backup, send a Telegram
smoke-test request, and record `docker stats --no-stream` during a scheduler refresh.
