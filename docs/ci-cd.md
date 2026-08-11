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

## Local or self-hosted deployment

The Compose application profile runs migrations once, then starts the bot and scheduler as
separate services:

```bash
docker compose --profile app up -d --build
docker compose ps
```

To deploy a published image instead of building locally:

```bash
NEWS_AGENT_IMAGE=ghcr.io/<owner>/<repository>:latest \
  docker compose --profile app up -d --no-build
```

The deployment host should provide `.env` with `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and
any optional provider keys. Compose treats the file as optional so configuration validation
and CI do not need production secrets. Never store production secrets in GitHub workflow
files or the repository.

## Automatic production deployment

No production host is selected yet, so publishing an image is the current delivery boundary.
To add automatic deployment, choose the target platform and configure its environment and
credentials. A deployment job should then consume the immutable `sha-<commit>` image only
after the publish job succeeds.
