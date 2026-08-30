from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "scripts" / "deploy-production.sh"
BACKUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "backup-database.sh"
IMAGE = "ghcr.io/example/news-agent@sha256:" + ("a" * 64)


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    deploy_dir = tmp_path / "news-agent"
    scripts_dir = deploy_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (deploy_dir / "docker-compose.yml").write_text("services: {}\n")
    (scripts_dir / "backup-database.sh").write_text(BACKUP_SCRIPT.read_text())
    env_file = deploy_dir / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "OPENAI_API_KEY=test-key\n"
        "POSTGRES_PASSWORD=test-password\n"
    )
    env_file.chmod(0o600)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"

if [[ "$*" == "compose version" ]]; then
  exit 0
fi
if [[ "$*" == *" run --rm --no-deps migrate"* ]] &&
  [[ "${FAKE_DOCKER_MODE:-}" == "migration-failure" ]]; then
  exit 42
fi
if [[ "$*" == *" up "* ]] && [[ "$*" == *" bot scheduler"* ]]; then
  touch "$FAKE_DOCKER_STATE/started"
  exit 0
fi
if [[ "$*" == *" ps -q "* ]]; then
  service="${*: -1}"
  if [[ "$service" == "postgres" ]]; then
    printf 'postgres-id\\n'
  elif [[ "${FAKE_EXISTING_APPS:-0}" == "1" ]] || [[ -f "$FAKE_DOCKER_STATE/started" ]]; then
    printf '%s-id\\n' "$service"
  fi
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  id="${*: -1}"
  if [[ "$*" == *".Config.Image"* ]]; then
    printf '%s\\n' "$NEWS_AGENT_IMAGE"
  elif [[ "$*" == *".State.Health"* ]]; then
    printf 'healthy\\n'
  elif [[ "${FAKE_DOCKER_MODE:-}" == "startup-failure" ]] && [[ "$id" == "scheduler-id" ]]; then
    printf 'exited\\n'
  else
    printf 'running\\n'
  fi
  exit 0
fi
if [[ "$*" == *" exec -T postgres "* ]]; then
  printf 'non-empty-backup'
  exit 0
fi
exit 0
"""
    )
    fake_docker.chmod(0o755)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "HOME": str(tmp_path),
            "DEPLOY_DIR": str(deploy_dir),
            "NEWS_AGENT_IMAGE": IMAGE,
            "DEPLOY_MIN_TOTAL_MEMORY_KIB": "0",
            "DEPLOY_MIN_AVAILABLE_MEMORY_KIB": "0",
            "DEPLOY_MIN_FREE_DISK_KIB": "0",
            "DEPLOY_STARTUP_WAIT_SECONDS": "0",
            "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
            "FAKE_DOCKER_STATE": str(state_dir),
        }
    )
    return deploy_dir, environment


def run_deployment(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("existing_apps", ["0", "1"])
def test_first_and_normal_deployment_succeed(
    deployment: tuple[Path, dict[str, str]], existing_apps: str
) -> None:
    deploy_dir, environment = deployment
    environment["FAKE_EXISTING_APPS"] = existing_apps

    result = run_deployment(environment)

    assert result.returncode == 0, result.stderr
    backups = list((deploy_dir / "backups").glob("news_agent_*.dump"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"non-empty-backup"
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text()
    assert "run --rm --no-deps migrate" in docker_log
    assert "up -d --no-build --no-deps --force-recreate bot scheduler" in docker_log
    assert ("stop --timeout 30 bot" in docker_log) is (existing_apps == "1")


def test_missing_environment_file_fails(deployment: tuple[Path, dict[str, str]]) -> None:
    deploy_dir, environment = deployment
    (deploy_dir / ".env").unlink()

    result = run_deployment(environment)

    assert result.returncode != 0
    assert ".env is missing" in result.stderr


def test_insufficient_disk_fails(deployment: tuple[Path, dict[str, str]]) -> None:
    _, environment = deployment
    environment["DEPLOY_MIN_FREE_DISK_KIB"] = "999999999999"

    result = run_deployment(environment)

    assert result.returncode != 0
    assert "at least" in result.stderr
    assert "GiB is required" in result.stderr


def test_failed_migration_keeps_app_stopped(deployment: tuple[Path, dict[str, str]]) -> None:
    _, environment = deployment
    environment["FAKE_EXISTING_APPS"] = "1"
    environment["FAKE_DOCKER_MODE"] = "migration-failure"

    result = run_deployment(environment)

    assert result.returncode == 42
    docker_log = Path(environment["FAKE_DOCKER_LOG"]).read_text()
    assert "stop --timeout 30 bot" in docker_log
    assert "force-recreate bot scheduler" not in docker_log


def test_failed_container_startup_fails_rollout(
    deployment: tuple[Path, dict[str, str]],
) -> None:
    _, environment = deployment
    environment["FAKE_DOCKER_MODE"] = "startup-failure"

    result = run_deployment(environment)

    assert result.returncode != 0
    assert "scheduler is exited" in result.stderr
