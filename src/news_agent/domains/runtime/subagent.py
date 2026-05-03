from __future__ import annotations

import json
import re

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.app.state import AgentResult, SupervisorState
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    RuntimeAlertRepository,
    RuntimeErrorRepository,
    RuntimeRunRepository,
    RuntimeStepRepository,
)


class RuntimeSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def run(self, state: SupervisorState) -> AgentResult:
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if "runtime_alerts" in capabilities:
            return await self._recent_alerts()
        return await self._inspect_runtime(state)

    async def _inspect_runtime(self, state: SupervisorState) -> AgentResult:
        command = state.get("command", "")
        args = state.get("args", [])
        message_text = state.get("message_text", "")
        current_run_id = state.get("runtime_run_id")

        async with self.session_factory() as session:
            run_repo = RuntimeRunRepository(session)
            step_repo = RuntimeStepRepository(session)
            error_repo = RuntimeErrorRepository(session)

            if command == "/job":
                run_id = _parse_run_id(args[:1])
                if run_id is None:
                    return _response("Usage: /job <run-id>")
                run = await run_repo.get(run_id)
                if run is None:
                    return _response("Run not found.")
                steps = await step_repo.list_for_run(run_id)
                errors = await error_repo.list_for_run(run_id)
                return _response(_format_run(run, steps, errors))

            if command == "/trace":
                run_id = _parse_run_id(args[:1])
                if run_id is None:
                    return _response("Usage: /trace <run-id>")
                run = await run_repo.get(run_id)
                if run is None:
                    return _response("Run not found.")
                steps = await step_repo.list_for_run(run_id)
                errors = await error_repo.list_for_run(run_id)
                return _response(_format_trace(run, steps, errors))

            if command == "/step":
                if len(args) < 2:
                    return _response("Usage: /step <run-id> <step-name>")
                run_id = _parse_run_id(args[:1])
                if run_id is None:
                    return _response("Usage: /step <run-id> <step-name>")
                step = await step_repo.get_for_run(run_id, " ".join(args[1:]))
                if step is None:
                    return _response("Step not found for that run.")
                return _response(_format_step(run_id, step))

            if "last refresh" in message_text.lower():
                runs = await run_repo.list_recent(
                    limit=1,
                    workflow="manual_refresh",
                    exclude_run_id=current_run_id,
                )
                if not runs:
                    runs = await run_repo.list_recent(
                        limit=1,
                        workflow="news_refresh",
                        exclude_run_id=current_run_id,
                    )
                if not runs:
                    return _response("No refresh history found yet.")
                steps = await step_repo.list_for_run(runs[0].id)
                errors = await error_repo.list_for_run(runs[0].id)
                return _response(_format_run(runs[0], steps, errors))

            requested_run_id = _extract_run_id_from_text(message_text)
            if requested_run_id is not None and _wants_trace_detail(message_text):
                run = await run_repo.get(requested_run_id)
                if run is None:
                    return _response("Run not found.")
                steps = await step_repo.list_for_run(requested_run_id)
                errors = await error_repo.list_for_run(requested_run_id)
                return _response(_format_trace(run, steps, errors))

            if "fail" in message_text.lower() or "error" in message_text.lower():
                query = _extract_error_query(message_text)
                if not query or _is_generic_error_query(query):
                    errors = await _latest_refresh_errors(run_repo, error_repo, current_run_id)
                    if not errors:
                        errors = await error_repo.list_recent(limit=5)
                else:
                    errors = await error_repo.search_recent(query, limit=5)
                    if not errors:
                        errors = await _latest_refresh_errors(run_repo, error_repo, current_run_id)
                    if not errors:
                        errors = await error_repo.list_recent(limit=5)
                if not errors:
                    return _response("No matching runtime errors found.")
                return _response(_format_errors(errors))

            runs = await run_repo.list_recent(limit=5, exclude_run_id=current_run_id)
            return _response(_format_runs(runs))

    async def _recent_alerts(self) -> AgentResult:
        async with self.session_factory() as session:
            alerts = await RuntimeAlertRepository(session).list_recent(limit=10)
        if not alerts:
            return {
                "response": "No runtime alerts recorded yet.",
                "metadata": {"capability": "runtime_alerts"},
            }
        lines = ["Recent runtime alerts:"]
        for alert in alerts:
            lines.append(
                f"- run {alert.run_id}: {alert.status} via {alert.channel}"
            )
        return {"response": "\n".join(lines), "metadata": {"capability": "runtime_alerts"}}


def _parse_run_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


def _extract_run_id_from_text(message_text: str) -> int | None:
    match = re.search(r"\brun(?:\s+id)?\s*#?:?\s*(\d+)\b", message_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\btrace\s+(\d+)\b", message_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _wants_trace_detail(message_text: str) -> bool:
    lowered = message_text.lower()
    return any(token in lowered for token in ("trace", "flow", "detail", "steps", "call"))


def _response(text: str) -> AgentResult:
    return {"response": text, "metadata": {"capability": "runtime_inspection"}}


def _format_runs(runs: list) -> str:
    if not runs:
        return "No runtime history found yet."
    lines = ["Recent runs:"]
    for run in runs:
        lines.append(
            f"- {run.id} [{run.workflow}] {run.status} at {run.started_at:%Y-%m-%d %H:%M:%S}"
        )
    return "\n".join(lines)


def _format_run(run, steps: list, errors: list) -> str:
    lines = [
        f"Run {run.id}\n"
        f"- Workflow: {run.workflow}\n"
        f"- Status: {run.status}\n"
        f"- Started: {run.started_at:%Y-%m-%d %H:%M:%S}\n"
        f"- Steps: {len(steps)}\n"
        f"- Summary: {run.summary or 'n/a'}"
    ]
    if errors:
        lines.append(f"- First error: {errors[0].step_name}: {errors[0].error_message}")
        lines.append(f"- Debug: /trace {run.id} or /step {run.id} {errors[0].step_name}")
    return "\n".join(lines)


def _format_trace(run, steps: list, errors: list | None = None) -> str:
    if not steps:
        return f"No step trace found for run {run.id}."

    errors = errors or []
    children_by_parent: dict[int | None, list] = {}
    for step in steps:
        children_by_parent.setdefault(step.parent_step_id, []).append(step)

    lines = [
        f"Trace for run {run.id}",
        f"- Workflow: {run.workflow}",
        f"- Status: {run.status}",
        f"- Started: {run.started_at:%Y-%m-%d %H:%M:%S}",
        (
            f"- Completed: {run.completed_at:%Y-%m-%d %H:%M:%S}"
            if run.completed_at
            else "- Completed: n/a"
        ),
        f"- Summary: {run.summary or 'n/a'}",
        "Flow:",
    ]

    rendered_ids: set[int] = set()

    def render_step(step, *, depth: int, index: int) -> None:
        rendered_ids.add(step.id)
        duration = f"{step.duration_ms}ms" if step.duration_ms is not None else "n/a"
        indent = "  " * depth
        prefix = f"{index}." if depth == 0 else "-"
        lines.append(
            f"{indent}{prefix} #{step.id} {step.step_name} "
            f"[{step.step_type}] {step.status} {duration}"
        )
        metadata = _compact_metadata(step.step_metadata)
        if metadata:
            lines.append(f"{indent}   metadata: {metadata}")
        if step.error_message:
            lines.append(f"{indent}   error: {step.error_message}")
        for child_index, child in enumerate(children_by_parent.get(step.id, []), start=1):
            render_step(child, depth=depth + 1, index=child_index)

    for index, step in enumerate(children_by_parent.get(None, []), start=1):
        render_step(step, depth=0, index=index)

    for step in steps:
        if step.id not in rendered_ids:
            render_step(step, depth=0, index=len(rendered_ids) + 1)

    if errors:
        lines.append("Errors:")
        for item in errors:
            lines.append(f"- #{item.step_id or 'n/a'} {item.step_name}: {item.error_message}")
    return "\n".join(lines)


def _format_step(run_id: int, step) -> str:
    return (
        f"Step {step.id} for run {run_id}\n"
        f"- Name: {step.step_name}\n"
        f"- Type: {step.step_type}\n"
        f"- Status: {step.status}\n"
        f"- Duration: {step.duration_ms or 0}ms\n"
        f"- Error: {step.error_message or 'none'}"
    )


def _format_errors(errors: list) -> str:
    lines = ["Recent matching runtime errors:"]
    for item in errors:
        lines.append(
            f"- run {item.run_id} / {item.step_name}: {item.error_message}"
        )
    return "\n".join(lines)


def _compact_metadata(metadata: dict | None) -> str:
    payload = dict(metadata or {})
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))[:500]


def _extract_error_query(message_text: str) -> str:
    lowered = message_text.lower()
    for token in (
        "why did",
        "failed",
        "fail",
        "error",
        "refresh",
        "source",
        "step",
        "what was",
        "what is",
        "what's",
        "tell me",
        "show me",
    ):
        lowered = lowered.replace(token, " ")
    return " ".join(
        part
        for part in lowered.split()
        if len(part) > 2 and part not in {"the", "for", "and", "that", "this", "latest"}
    )


def _is_generic_error_query(query: str) -> bool:
    return query in {"", "what", "was", "happened", "issue", "problem"} or len(query.split()) <= 1


async def _latest_refresh_errors(run_repo, error_repo, current_run_id: int | None):
    runs = await run_repo.list_recent(
        limit=1,
        workflow="manual_refresh",
        exclude_run_id=current_run_id,
    )
    if not runs:
        runs = await run_repo.list_recent(
            limit=1,
            workflow="news_refresh",
            exclude_run_id=current_run_id,
        )
    if not runs:
        return []
    return await error_repo.list_for_run(runs[0].id)
