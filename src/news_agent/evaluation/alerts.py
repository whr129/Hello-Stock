"""Opt-in, bounded Jev quality evaluation using synthetic public fixtures only."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator

from news_agent.alerts.decision import (
    JEV_INPUT_PRICE_PER_MILLION,
    PROMPT_VERSION,
    Delivery,
    JevClient,
    JevConfig,
    JevError,
    SentimentLabel,
    build_questions,
)
from news_agent.alerts.evidence import ArticleEvidence
from news_agent.alerts.policy import DeliveryPolicyConfig, guard_delivery, is_material_update
from news_agent.alerts.service import assessment_state, build_evidence_state

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES = ROOT / "tests/evaluation/market_alert_cases.jsonl"
CONFIDENCE_THRESHOLD = 0.85


class EvaluationJevClient(JevClient):
    """Capture only decision fields, never headers, keys, request text, or error bodies."""

    last_response: dict[str, Any] | None = None

    async def _request(self, payload: dict, key: str, attempts: int) -> object:
        data = await super()._request(payload, key, attempts)
        if isinstance(data, dict):
            self.last_response = {
                field: data[field] for field in ("model", "answers", "usage") if field in data
            }
        return data


class AlertCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    category: str
    synthetic: bool
    rationale: str = Field(min_length=10)
    state: dict[str, Any]
    tickers: list[str]
    expected_delivery: Delivery
    expected_sentiments: dict[str, SentimentLabel]

    @model_validator(mode="after")
    def isolated_fixture(self) -> AlertCase:
        if not self.synthetic:
            raise ValueError("Only explicitly synthetic fixtures are permitted")
        if len(set(self.tickers)) != len(self.tickers) or len(self.tickers) > 10:
            raise ValueError("Ticker list must be unique and bounded")
        if set(self.tickers) != set(self.expected_sentiments):
            raise ValueError("Every supplied ticker requires exactly one expected label")
        if len(json.dumps(self.state, allow_nan=False)) > 12000:
            raise ValueError("Fixture exceeds production state size")
        return self


def load_cases(path: Path = DEFAULT_CASES, max_cases: int = 50) -> list[AlertCase]:
    if not 1 <= max_cases <= 50:
        raise ValueError("max_cases must be between 1 and 50")
    cases = [
        AlertCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate evaluation case IDs")
    return cases[:max_cases]


def reservation_usd(case: AlertCase) -> float:
    questions = build_questions(case.tickers)
    # Upper bound bytes as tokens, repeated state per question, plus framing headroom.
    tokens = len(json.dumps(case.state, ensure_ascii=False).encode()) * len(questions)
    tokens += len(json.dumps(questions).encode()) + len(questions) * 2048
    return max(0.001, tokens * JEV_INPUT_PRICE_PER_MILLION / 1_000_000)


def summarize(rows: list[dict], *, expected_cases: int) -> dict[str, Any]:
    successful = [row for row in rows if row.get("decision")]
    failures = [row for row in rows if row.get("error")]
    covered = [row for row in successful if row["decision"]["confidence"] >= CONFIDENCE_THRESHOLD]
    host_urgent = [row for row in successful if row.get("host_guard", {}).get("allowed")]
    host_true = sum(row["expected_delivery"] == "immediate_alert" for row in host_urgent)
    host_precision = host_true / len(host_urgent) if host_urgent else None
    actual_urgent = [row for row in successful if row["decision"]["delivery"] == "immediate_alert"]
    expected_urgent = sum(row["expected_delivery"] == "immediate_alert" for row in rows)
    true_urgent = sum(row["expected_delivery"] == "immediate_alert" for row in actual_urgent)
    confident_urgent = [row for row in actual_urgent if row in covered]
    confident_true = sum(row["expected_delivery"] == "immediate_alert" for row in confident_urgent)
    correct = sum(row["decision"]["delivery"] == row["expected_delivery"] for row in successful)
    sentiment_pairs = [
        (expected, row["decision"]["sentiments"].get(ticker, "unknown"))
        for row in successful
        for ticker, expected in row["expected_sentiments"].items()
    ]
    labels = {}
    for label in ("positive", "negative", "neutral", "mixed", "unknown"):
        support = sum(expected == label for expected, _ in sentiment_pairs)
        predicted = sum(actual == label for _, actual in sentiment_pairs)
        true = sum(expected == actual == label for expected, actual in sentiment_pairs)
        labels[label] = {
            "support": support,
            "predicted": predicted,
            "precision": true / predicted if predicted else None,
            "recall": true / support if support else None,
        }
    precision = true_urgent / len(actual_urgent) if actual_urgent else None
    confident_precision = confident_true / len(confident_urgent) if confident_urgent else None
    accuracy = correct / len(successful) if successful else None
    completed = len(rows) == expected_cases and not failures
    gate = bool(
        completed
        and precision is not None
        and precision >= 0.9
        and confident_precision is not None
        and confident_precision >= 0.9
        and host_precision is not None
        and host_precision >= 0.9
        and accuracy is not None
        and accuracy >= 0.9
    )
    return {
        "mode": "live_provider",
        "prompt_version": PROMPT_VERSION,
        "cases_expected": expected_cases,
        "cases_attempted": len(rows),
        "cases_completed": len(successful),
        "provider_failures": len(failures),
        "failure_codes": dict(Counter(row["error"] for row in failures)),
        "route_accuracy": accuracy,
        "urgent_predicted": len(actual_urgent),
        "urgent_expected": expected_urgent,
        "urgent_precision": precision,
        "urgent_recall": true_urgent / expected_urgent if expected_urgent else None,
        "host_urgent_predicted": len(host_urgent),
        "host_urgent_precision": host_precision,
        "host_urgent_recall": host_true / expected_urgent if expected_urgent else None,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "confidence_coverage": len(covered) / len(successful) if successful else 0,
        "confident_urgent_predicted": len(confident_urgent),
        "confident_urgent_precision": confident_precision,
        "confident_urgent_recall": confident_true / expected_urgent if expected_urgent else None,
        "sentiment_accuracy": sum(a == b for a, b in sentiment_pairs) / len(sentiment_pairs)
        if sentiment_pairs
        else None,
        "sentiment_by_label": labels,
        "reported_cost_usd": sum(row.get("reported_cost_usd") or 0 for row in rows),
        "budget_consumed_usd": sum(row["budget_consumed_usd"] for row in rows),
        "cost_missing_count": sum(row.get("reported_cost_usd") is None for row in rows),
        "input_tokens": sum(row.get("input_tokens", 0) for row in rows),
        "output_tokens": sum(row.get("output_tokens", 0) for row in rows),
        "model_versions": sorted({row["decision"]["model"] for row in successful}),
        "quality_gate_passed": gate,
        "gate_criteria": "Complete live corpus; route accuracy >=90%; raw, host-eligible, "
        "and confidence-qualified "
        "urgent precision >=90%; at least one urgent prediction in each group.",
        "limitations": "Synthetic diagnostic corpus, not production prevalence or calibrated "
        "confidence. Explicit as_of/history context is supplied. No trade outcomes, "
        "source verification, Telegram, or database evaluated. Host guards run on fixture "
        "trust/primary annotations, which are benchmark assumptions, not verified news.",
    }


async def evaluate_cases(
    cases: list[AlertCase], client: Any, *, max_cost_usd: float, host_confidence: float = 0.85
) -> tuple[list, dict]:
    if not 0 < max_cost_usd <= 0.10:
        raise ValueError("Live evaluation spending cap must be positive and no more than USD 0.10")
    if not 0 <= host_confidence <= 1 or not 1 <= len(cases) <= 50:
        raise ValueError("Confidence must be in [0,1] and corpus must contain 1..50 cases")
    rows = []
    consumed = 0.0
    stop_reason = None
    for case in cases:
        reserve = reservation_usd(case)
        if consumed + reserve > max_cost_usd:
            stop_reason = "budget_guard"
            break
        row = {
            "id": case.id,
            "category": case.category,
            "expected_delivery": case.expected_delivery,
            "expected_sentiments": case.expected_sentiments,
            "input_digest": hashlib.sha256(
                json.dumps(case.state, sort_keys=True).encode()
            ).hexdigest(),
            "reserved_usd": reserve,
        }
        if isinstance(client, EvaluationJevClient):
            client.last_response = None
        start = time.monotonic()
        try:
            request_state = assessment_state(
                build_evidence_state(
                    [ArticleEvidence.model_validate(entry) for entry in case.state["evidence"]],
                    windows=case.state.get("sentiment_windows"),
                ),
                as_of=datetime.fromisoformat(case.state["as_of"]),
                event_revision=case.state["event_revision"],
                delivery_history=case.state["delivery_history"],
            )
            row["input_digest"] = hashlib.sha256(
                json.dumps(request_state, sort_keys=True).encode()
            ).hexdigest()
            decision = await client.assess(request_state, tickers=case.tickers)
            history_times = [
                datetime.fromisoformat(entry["delivered_at"])
                for entry in request_state["delivery_history"]
                if entry.get("delivered_at")
            ]
            guard = guard_delivery(
                decision,
                [ArticleEvidence.model_validate(entry) for entry in request_state["evidence"]],
                config=DeliveryPolicyConfig(
                    enabled=True, shadow_mode=False, chat_id=1, min_confidence=host_confidence
                ),
                now=datetime.fromisoformat(request_state["as_of"]),
                last_delivered_at=max(history_times, default=None),
                recent_deliveries=history_times,
                material_update=is_material_update(
                    decision,
                    event_revision=request_state["event_revision"],
                    delivery_history=request_state["delivery_history"],
                ),
            )
            row["host_guard"] = {
                "allowed": guard.allowed,
                "reason": guard.reason,
                "not_before": guard.not_before.isoformat() if guard.not_before else None,
            }
            row.update(
                decision=decision.model_dump(mode="json"),
                reported_cost_usd=decision.usage.cost,
                input_tokens=decision.usage.input_tokens,
                output_tokens=decision.usage.output_tokens,
                budget_consumed_usd=decision.usage.cost
                if decision.usage.cost is not None
                else reserve,
            )
        except JevError as error:
            actual = error.usage.cost if error.usage else None
            row.update(
                error=error.code,
                status_code=error.status_code,
                reported_cost_usd=actual,
                input_tokens=error.usage.input_tokens if error.usage else 0,
                output_tokens=error.usage.output_tokens if error.usage else 0,
                budget_consumed_usd=actual if actual is not None else reserve,
            )
            if error.status_code in (401, 403, 402):
                stop_reason = "provider_auth_or_credit"
        if isinstance(client, EvaluationJevClient):
            row["provider_response"] = client.last_response
        row["latency_seconds"] = round(time.monotonic() - start, 4)
        rows.append(row)
        consumed += row["budget_consumed_usd"]
        if consumed > max_cost_usd:
            stop_reason = "provider_reported_cost_exceeded_reservation"
        if stop_reason:
            break
    summary = summarize(rows, expected_cases=len(cases))
    expected_urgent = sum(case.expected_delivery == "immediate_alert" for case in cases)
    summary["urgent_expected"] = expected_urgent
    for prefix in ("urgent", "confident_urgent", "host_urgent"):
        true_count = sum(
            row["expected_delivery"] == "immediate_alert"
            and (
                row.get("host_guard", {}).get("allowed", False)
                if prefix == "host_urgent"
                else bool(row.get("decision"))
                and row["decision"]["delivery"] == "immediate_alert"
                and (prefix == "urgent" or row["decision"]["confidence"] >= CONFIDENCE_THRESHOLD)
            )
            for row in rows
        )
        summary[prefix + "_recall"] = true_count / expected_urgent if expected_urgent else None
    summary.update(
        max_cost_usd=max_cost_usd,
        stop_reason=stop_reason,
        host_confidence_threshold=host_confidence,
    )
    summary["host_precision_recall_target_passed"] = bool(
        summary["cases_completed"] == len(cases)
        and summary["host_urgent_predicted"] > 0
        and summary["host_urgent_precision"] >= 0.9
        and (summary["host_urgent_recall"] or 0) >= 0.8
    )
    return rows, summary


def write_reports(rows: list[dict], summary: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    stem = "market_alert_eval_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    results_path = output / (stem + ".jsonl")
    summary_path = output / (stem + ".json")
    results_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    mismatches = [
        row
        for row in rows
        if row.get("decision")
        and (
            row["decision"]["delivery"] != row["expected_delivery"]
            or row["decision"]["sentiments"] != row["expected_sentiments"]
        )
    ]
    markdown = [
        "# Synthetic market-alert live evaluation",
        "",
        f"Quality gate passed: **{summary['quality_gate_passed']}**",
        "",
        f"Completed: {summary['cases_completed']}/{summary['cases_expected']}",
        f"Route accuracy: {summary['route_accuracy']}",
        f"Urgent precision / recall: {summary['urgent_precision']} / {summary['urgent_recall']}",
        f"Confident urgent precision / recall: {summary['confident_urgent_precision']} / "
        f"{summary['confident_urgent_recall']}",
        f"Host-eligible urgent precision / recall: {summary['host_urgent_precision']} / "
        f"{summary['host_urgent_recall']}",
        f"Confidence coverage: {summary['confidence_coverage']}",
        f"Sentiment accuracy: {summary['sentiment_accuracy']}",
        f"Provider-reported cost: USD {summary['reported_cost_usd']:.8f}",
        f"Provider failures: {summary['provider_failures']}",
        "",
        summary["limitations"],
        "",
        "## Mismatches",
        "",
        "| Case | Expected route | Actual route | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    markdown.extend(
        f"| {row['id']} | {row['expected_delivery']} | "
        f"{row['decision']['delivery']} | {row['decision']['confidence']:.4f} |"
        for row in mismatches
    )
    (output / (stem + ".md")).write_text("\n".join(markdown) + "\n")
    return results_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly permit bounded paid Jev calls"
    )
    parser.add_argument("--max-cases", type=int, default=50)
    parser.add_argument("--max-cost-usd", type=float, default=0.10)
    parser.add_argument(
        "--host-confidence",
        type=float,
        default=0.85,
        help="Evaluation-only host threshold; never changes application settings",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/eval")
    args = parser.parse_args()
    cases = load_cases(args.cases, args.max_cases)
    if not args.live:
        print(f"Validated {len(cases)} synthetic cases. No provider calls; use --live to opt in.")
        return
    if not 0 < args.max_cost_usd <= 0.10:
        parser.error("--max-cost-usd must be positive and <=0.10")
    # Intentionally override the stale inherited process key for this isolated runner only.
    # Never load/export other project secrets or print credential values.
    key = dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY")
    if not key:
        parser.error("Project .env OPENROUTER_API_KEY is unavailable")

    async def run() -> tuple[list, dict]:
        async with EvaluationJevClient(JevConfig(api_key=key, max_attempts=1)) as client:
            return await evaluate_cases(
                cases, client, max_cost_usd=args.max_cost_usd, host_confidence=args.host_confidence
            )

    rows, summary = asyncio.run(run())
    results_path, summary_path = write_reports(rows, summary, args.output)
    print(
        json.dumps(
            {
                "summary": summary,
                "results_path": str(results_path),
                "summary_path": str(summary_path),
            },
            indent=2,
        )
    )
    if not summary["quality_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
