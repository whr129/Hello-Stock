# CI/CD

## Continuous integration

`.github/workflows/ci.yml` runs on pull requests, `main`, `codex/**` branches, and manual
dispatches. It performs:

- Ruff syntax and correctness checks on Python 3.12. Existing unused-import and
  formatting debt is intentionally non-blocking until it is cleaned up separately.
- The full pytest suite on Python 3.11 and 3.12.
- Alembic migrations against PostgreSQL 16 with pgvector.
- A BuildKit container build without publishing the image.

Configure branch protection for `main` to require these checks:

- `Ruff`
- `Tests (Python 3.11)`
- `Tests (Python 3.12)`
- `Container build`

## Container publishing

`.github/workflows/publish-container.yml` reruns CI and publishes to GitHub Container
Registry only after validation succeeds. It runs for `main`, `v*` tags, and manual
dispatches. Images are published as:

```text
ghcr.io/<owner>/<repository>:latest
ghcr.io/<owner>/<repository>:main
ghcr.io/<owner>/<repository>:sha-<commit>
ghcr.io/<owner>/<repository>:<version-tag>
```

The workflow uses the repository-provided `GITHUB_TOKEN`; no registry password needs to be
added. Repository Actions settings must allow workflow write access to packages.

## Single-server production deployment

The production topology runs PostgreSQL with pgvector, migrations, the bot, and the scheduler
on one Docker host. PostgreSQL is attached only to an internal Docker network and has no
public host port. Its data survives container replacement in the `postgres-data` volume.

Prepare `~/news-agent/.env` directly on the server; the deployment workflow never uploads or
overwrites this file:

```dotenv
POSTGRES_DB=news_agent
POSTGRES_USER=news_agent
POSTGRES_PASSWORD=<output from: openssl rand -hex 32>
TELEGRAM_BOT_TOKEN=<rotated bot token>
OPENAI_API_KEY=<key>
NEWS_AGENT_IMAGE=ghcr.io/whr129/hello-stock:latest
```

Protect it and start the stack:

```bash
cd ~/news-agent
chmod 600 .env
docker compose --profile app up -d --no-build
docker compose ps
```

`POSTGRES_PASSWORD` initializes the database only when the volume is first created. Changing
the value later does not rotate an existing database role; perform a PostgreSQL password
rotation separately and take a backup first.

If the GHCR package is private, authenticate Docker on the server once with a dedicated
classic personal access token that has only `read:packages`:

```bash
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

Do not place the GHCR token in the project `.env`; Docker stores registry authentication in
the deploying user's Docker configuration. Prefer making the package public if the image
does not need to be private.

## Automatic production deployment

The publish workflow includes an optional production job. It copies only Compose and the
backup helper over SSH, then deploys the exact image digest produced by the publish job.
It cannot replace or print the server's `.env`.

Create a protected GitHub Environment named `production` and add:

- `DEPLOY_HOST`: server hostname or public IP.
- `DEPLOY_PORT`: SSH port, normally `22`.
- `DEPLOY_USER`: an unprivileged deployment user with Docker access.
- `DEPLOY_SSH_PRIVATE_KEY`: a dedicated deployment key, not a personal primary key.
- `DEPLOY_SSH_KNOWN_HOSTS`: a verified known-hosts entry for the server.

Verify the host-key fingerprint through the server provider before saving the known-hosts
entry. Add required reviewers to the `production` environment if deployments need approval.
Finally create the repository Actions variable `ENABLE_PRODUCTION_DEPLOY=true`. Until that
variable is set, merges publish the image but skip deployment.

The application secrets (`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, provider keys, and database
password) stay only in the server's `.env`; they are not required by the deployment job.

## Database backups

Run an initial backup and verify that a non-empty custom-format dump is created:

```bash
cd ~/news-agent
./scripts/backup-database.sh
ls -lh backups/
```

The helper retains 14 days by default. Schedule it with the deployment user's cron, using
absolute paths, for example:

```cron
0 3 * * * /home/<deploy-user>/news-agent/scripts/backup-database.sh >> /home/<deploy-user>/news-agent/backup.log 2>&1
```

Local dumps protect against container or volume mistakes, but not total server loss. Copy
encrypted backups to a separate provider or object store, restrict access to the deployment
user, and test restoration regularly. Monitor free disk space and backup job failures.

Before every database upgrade, take a fresh dump. Restore into a separate test database first
instead of testing restoration against production.
