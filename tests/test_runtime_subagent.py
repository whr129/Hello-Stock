from datetime import UTC, datetime
from types import SimpleNamespace

from news_agent.domains.runtime.subagent import (
    _extract_error_query,
    _extract_run_id_from_text,
    _format_trace,
    _is_generic_error_query,
    _wants_trace_detail,
)


def test_extract_error_query_strips_generic_followup_words() -> None:
    query = _extract_error_query("what was the error?")

    assert query == ""


def test_single_word_followup_is_treated_as_generic() -> None:
    assert _is_generic_error_query("reuters") is True
    assert _is_generic_error_query("") is True


def test_specific_error_query_is_not_generic() -> None:
    assert _is_generic_error_query("reuters forbidden") is False


def test_extract_run_id_from_natural_trace_request() -> None:
    assert _extract_run_id_from_text("trace the run id 76 for detail") == 76
    assert _wants_trace_detail("trace the run id 76 for detail") is True


def test_format_trace_includes_full_recorded_flow_metadata_and_errors() -> None:
    started_at = datetime(2026, 5, 3, 0, 33, 41, tzinfo=UTC)
    run = SimpleNamespace(
        id=76,
        workflow="chat",
        status="completed_with_errors",
        started_at=started_at,
        completed_at=started_at,
        summary="chat completed",
    )
    steps = [
        SimpleNamespace(
            id=679,
            parent_step_id=None,
            step_name="load_user_context",
            step_type="node",
            status="completed",
            duration_ms=347,
            step_metadata={"route_capabilities": []},
            error_message=None,
        ),
        SimpleNamespace(
            id=680,
            parent_step_id=679,
            step_name="semantic_memory_search",
            step_type="tool",
            status="failed",
            duration_ms=12,
            step_metadata={"user_id": 1},
            error_message="boom",
        ),
    ]
    errors = [
        SimpleNamespace(
            step_id=680,
            step_name="semantic_memory_search",
            error_message="boom",
        )
    ]

    trace = _format_trace(run, steps, errors)

    assert "Trace for run 76" in trace
    assert "1. #679 load_user_context [node] completed 347ms" in trace
    assert "- #680 semantic_memory_search [tool] failed 12ms" in trace
    assert 'metadata: {"user_id":1}' in trace
    assert "Errors:" in trace
