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
