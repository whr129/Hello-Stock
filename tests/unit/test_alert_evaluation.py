import copy
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from news_agent.alerts.decision import JevDecision, JevError, JevUsage
from news_agent.evaluation.alerts import (
    AlertCase,
    evaluate_cases,
    load_cases,
    reservation_usd,
    summarize,
)


def decision(delivery="immediate_alert", confidence=0.95, label="negative", cost=0.00001):
    return JevDecision(
        delivery=delivery,
        confidence=confidence,
        materiality=2.0,
        event_type="guidance",
        reason_code="material_new_event",
        sentiments={"ALDRX": label},
        model="typesafe/jev-1.14",
        usage=JevUsage(input_tokens=100, output_tokens=0, cost=cost),
        probabilities={},
        attempts=1,
    )


def row(expected, actual, *, expected_label="negative", actual_label="negative"):
    return {
        "expected_delivery": expected,
        "expected_sentiments": {"ALDRX": expected_label},
        "decision": decision(actual, label=actual_label).model_dump(mode="json"),
        "host_guard": {"allowed": actual == "immediate_alert"},
        "reported_cost_usd": 0.00001,
        "budget_consumed_usd": 0.00001,
        "input_tokens": 100,
    }


def test_golden_corpus_has_diverse_readable_synthetic_labels():
    cases = load_cases()
    assert 30 <= len(cases) <= 50
    assert len({case.id for case in cases}) == len(cases)
    assert set(Counter(case.expected_delivery for case in cases)) == {
        "immediate_alert",
        "daily_report",
        "ignore",
    }
    assert {
        "bankruptcy",
        "m_and_a",
        "guidance",
        "macro",
        "regulation",
        "rumor",
        "stale",
        "duplicate",
        "correction",
        "prompt_injection",
        "multi_company",
    } <= {case.category for case in cases}
    assert {label for case in cases for label in case.expected_sentiments.values()} == {
        "positive",
        "negative",
        "neutral",
        "mixed",
        "unknown",
    }
    for case in cases:
        assert case.rationale
        assert case.state["as_of"] and case.state["event_revision"] >= 1
        for evidence in case.state["evidence"]:
            assert evidence["url"].startswith("https://synthetic.example/")
        assert "expected_delivery" not in case.state


def test_corpus_validation_rejects_real_or_unattributed_examples():
    data = load_cases()[0].model_dump()
    with pytest.raises(ValidationError):
        AlertCase.model_validate(data | {"synthetic": False})
    with pytest.raises(ValidationError):
        AlertCase.model_validate(data | {"expected_sentiments": {"WRONG": "positive"}})
    with pytest.raises(ValueError):
        load_cases(max_cases=100)


def test_metrics_measure_false_urgent_and_per_label_errors():
    rows = [
        row("immediate_alert", "immediate_alert"),
        row("daily_report", "immediate_alert", expected_label="neutral", actual_label="negative"),
        row("ignore", "ignore", expected_label="unknown", actual_label="unknown"),
    ]
    metrics = summarize(rows, expected_cases=3)
    assert metrics["urgent_precision"] == 0.5
    assert metrics["urgent_recall"] == 1
    assert metrics["route_accuracy"] == pytest.approx(2 / 3)
    assert metrics["sentiment_by_label"]["negative"]["precision"] == 0.5
    assert metrics["sentiment_by_label"]["neutral"]["recall"] == 0
    assert not metrics["quality_gate_passed"]


def test_zero_predicted_alerts_cannot_pass_quality_gate():
    metrics = summarize([row("ignore", "ignore")], expected_cases=1)
    assert metrics["route_accuracy"] == 1
    assert metrics["urgent_precision"] is None
    assert not metrics["quality_gate_passed"]


def test_low_confidence_urgent_predictions_cannot_pass_gate():
    item = row("immediate_alert", "immediate_alert")
    item["decision"]["confidence"] = 0.5
    metrics = summarize([item], expected_cases=1)
    assert metrics["urgent_precision"] == 1
    assert metrics["confidence_coverage"] == 0
    assert not metrics["quality_gate_passed"]


@pytest.mark.asyncio
async def test_budget_reserved_before_each_call_and_missing_cost_charged_conservatively():
    cases = load_cases(max_cases=2)
    client = SimpleNamespace(assess=AsyncMock(return_value=decision(cost=None)))
    reserve = reservation_usd(cases[0])
    rows, summary = await evaluate_cases(cases, client, max_cost_usd=reserve)
    assert client.assess.await_count == 1
    assert rows[0]["budget_consumed_usd"] == reserve
    assert summary["cost_missing_count"] == 1
    assert summary["stop_reason"] == "budget_guard"
    assert not summary["quality_gate_passed"]


@pytest.mark.asyncio
async def test_budget_limit_and_auth_failure_never_retry():
    client = SimpleNamespace(
        assess=AsyncMock(side_effect=JevError("authentication", status_code=401))
    )
    with pytest.raises(ValueError):
        await evaluate_cases(load_cases(), client, max_cost_usd=0.11)
    client.assess.assert_not_awaited()
    rows, summary = await evaluate_cases(load_cases(), client, max_cost_usd=0.1)
    assert client.assess.await_count == 1
    assert rows[0]["error"] == "authentication"
    assert summary["provider_failures"] == 1
    assert summary["stop_reason"] == "provider_auth_or_credit"
    assert not summary["quality_gate_passed"]


@pytest.mark.asyncio
async def test_actual_provider_usage_and_shared_request_shape_are_recorded():
    cases = load_cases(max_cases=1)
    original = copy.deepcopy(cases[0].state)
    client = SimpleNamespace(assess=AsyncMock(return_value=decision()))
    rows, summary = await evaluate_cases(cases, client, max_cost_usd=0.1)
    sent = client.assess.call_args.args[0]
    expected = copy.deepcopy(original)
    for record in expected["evidence"]:
        record.setdefault("revision", 1)
    assert sent == expected
    assert cases[0].state == original
    assert summary["reported_cost_usd"] == 0.00001
    assert summary["input_tokens"] == 100
    assert summary["quality_gate_passed"]
    assert json.loads(json.dumps(rows))[0]["decision"]["model"] == "typesafe/jev-1.14"


def test_holdout_is_balanced_and_disjoint_from_calibration_corpus():
    from news_agent.evaluation.alerts import ROOT

    holdout = load_cases(ROOT / "tests/evaluation/market_alert_holdout_cases.jsonl")
    original = load_cases()
    assert len(holdout) == 24
    assert Counter(case.expected_delivery for case in holdout) == {
        "immediate_alert": 8,
        "daily_report": 8,
        "ignore": 8,
    }
    assert not ({case.id for case in holdout} & {case.id for case in original})
    assert all(case.synthetic and case.rationale for case in holdout)


@pytest.mark.asyncio
async def test_openai_cost_is_estimated_and_raw_response_not_persisted():
    result = decision().model_copy(update={"model": "gpt-6-luna"})
    client = SimpleNamespace(
        assess=AsyncMock(return_value=result), last_response={"secret": "not-for-report"}
    )
    rows, summary = await evaluate_cases(
        load_cases(max_cases=1), client, max_cost_usd=0.1, provider="openai"
    )
    assert rows[0]["reported_cost_usd"] is None
    assert rows[0]["estimated_cost_usd"] == 0.00001
    assert rows[0]["cost_basis"] == "estimated"
    assert "provider_response" not in rows[0]
    assert summary["estimated_cost_usd"] == 0.00001
    assert summary["reported_cost_usd"] == 0
    assert summary["provider"] == "openai"
    assert summary["quality_gate_passed"]


def test_openai_gate_rejects_high_accuracy_with_low_urgent_recall():
    rows = [row("immediate_alert", "immediate_alert")]
    rows += [row("immediate_alert", "daily_report")]
    rows += [row("daily_report", "daily_report") for _ in range(8)]
    assert summarize(rows, expected_cases=10, provider="jev")["quality_gate_passed"]
    metrics = summarize(rows, expected_cases=10, provider="openai")
    assert metrics["route_accuracy"] == 0.9
    assert metrics["host_urgent_precision"] == 1
    assert metrics["host_urgent_recall"] == 0.5
    assert not metrics["quality_gate_passed"]


def test_openai_reservation_uses_provider_rate():
    case = load_cases()[0]
    assert reservation_usd(case, provider="openai") > reservation_usd(case, provider="jev")


@pytest.mark.parametrize("provider", ["openai", "jev"])
@pytest.mark.parametrize("environment_key", [None, "environment-key"])
def test_cli_uses_environment_then_cwd_dotenv(
    monkeypatch, tmp_path, capsys, provider, environment_key
):
    import sys

    from news_agent.evaluation import alerts

    key_name = "OPENAI_API_KEY" if provider == "openai" else "OPENROUTER_API_KEY"
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{key_name}=dotenv-key\n")
    monkeypatch.delenv(key_name, raising=False)
    if environment_key:
        monkeypatch.setenv(key_name, environment_key)
    monkeypatch.setattr(sys, "argv", ["alerts", "--live", "--provider", provider])
    captured = {}

    class Client:
        def __init__(self, config):
            captured["key"] = (
                config if isinstance(config, str) else config.api_key.get_secret_value()
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    if provider == "openai":
        from news_agent.alerts import openai_decision

        monkeypatch.setattr(openai_decision, "OpenAIDecisionClient", Client)
    else:
        monkeypatch.setattr(alerts, "EvaluationJevClient", Client)
    evaluate = AsyncMock(return_value=([], {"quality_gate_passed": True}))
    monkeypatch.setattr(alerts, "evaluate_cases", evaluate)
    monkeypatch.setattr(alerts, "write_reports", lambda *_: (tmp_path / "rows", tmp_path / "sum"))
    alerts.main()
    assert captured["key"] == (environment_key or "dotenv-key")
    assert evaluate.call_args.kwargs["provider"] == provider
    output = capsys.readouterr().out
    assert "environment-key" not in output and "dotenv-key" not in output


@pytest.mark.asyncio
async def test_openai_failure_keeps_estimated_usage_and_stops_on_credit_error():
    client = SimpleNamespace(
        assess=AsyncMock(
            side_effect=JevError(
                "billing",
                status_code=402,
                usage=JevUsage(input_tokens=100, output_tokens=0, cost=0.00001),
            )
        )
    )
    rows, summary = await evaluate_cases(
        load_cases(max_cases=2), client, max_cost_usd=0.1, provider="openai"
    )
    assert client.assess.await_count == 1
    assert rows[0]["reported_cost_usd"] is None
    assert rows[0]["estimated_cost_usd"] == 0.00001
    assert summary["budget_consumed_usd"] == 0.00001
    assert summary["stop_reason"] == "provider_auth_or_credit"
    assert not summary["quality_gate_passed"]


def test_openai_holdout_is_balanced_synthetic_and_independent():
    from news_agent.evaluation.alerts import ROOT

    holdout = load_cases(ROOT / "tests/evaluation/market_alert_openai_holdout_cases.jsonl")
    locked = load_cases() + load_cases(ROOT / "tests/evaluation/market_alert_holdout_cases.jsonl")
    assert Counter(case.expected_delivery for case in holdout) == {
        "immediate_alert": 8,
        "daily_report": 8,
        "ignore": 8,
    }
    assert not ({case.id for case in holdout} & {case.id for case in locked})
    assert not (
        {case.state["evidence"][0]["text"] for case in holdout}
        & {case.state["evidence"][0]["text"] for case in locked}
    )
    assert {"correction", "source_ambiguity", "prompt_injection"} <= {
        case.category for case in holdout
    }
    assert all(case.synthetic and case.rationale for case in holdout)
