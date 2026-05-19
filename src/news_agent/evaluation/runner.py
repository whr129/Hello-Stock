from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import APIError, AsyncOpenAI

from news_agent.graph.chat_graph import build_chat_graph
from news_agent.settings import Settings, get_settings
from news_agent.storage.database import create_session_factory

JUDGE_PROMPT = """
You are judging a Telegram market-research assistant answer.

Return strict JSON:
{
  "scores": {
    "relevance": 1-5,
    "ticker_correctness": 1-5,
    "theme_correctness": 1-5,
    "evidence_quality": 1-5,
    "freshness": 1-5,
    "source_attribution": 1-5,
    "usefulness": 1-5,
    "safety": 1-5,
    "concision": 1-5
  },
  "pass": true|false,
  "tags": [
    "too_generic|no_evidence|wrong_ticker|stale_data|hallucinated_source",
    "unclear_ranking_reason|too_verbose|missing_weak_evidence|not_useful_research"
  ],
  "notes": "short reason"
}

Prefer failing answers with fake tickers, unsupported themes, missing evidence, or advice.
""".strip()


@dataclass(frozen=True)
class EvalCase:
    id: str
    prompt: str
    expected: str


async def run_eval(case_path: Path, settings: Settings) -> tuple[Path, Path]:
    cases = _load_cases(case_path, settings.eval_max_cases)
    output_dir = Path(settings.eval_output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = output_dir / f"market_research_eval_{stamp}.jsonl"
    report_path = output_dir / f"market_research_eval_{stamp}.md"

    graph = build_chat_graph(create_session_factory(settings), settings)
    judge = _Judge(settings)
    results = []
    for index, case in enumerate(cases, start=1):
        answer_state = await graph.ainvoke(
            {
                "telegram_user_id": 900000 + index,
                "chat_id": 900000 + index,
                "message_text": case.prompt,
            }
        )
        answer = answer_state.get("final_response") or answer_state.get("response", "")
        judgment = await judge.evaluate(case, answer)
        result = {
            "id": case.id,
            "prompt": case.prompt,
            "expected": case.expected,
            "answer": answer,
            "judgment": judgment,
        }
        results.append(result)

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    report_path.write_text(_markdown_report(results), encoding="utf-8")
    return jsonl_path, report_path


class _Judge:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.eval_llm_enabled and bool(settings.openai_api_key)
        self.model = settings.eval_model or settings.openai_model
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if self.enabled else None

    async def evaluate(self, case: EvalCase, answer: str) -> dict[str, Any]:
        if not self.client:
            return _deterministic_judgment(case, answer)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUDGE_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Prompt:\n{case.prompt}\n\n"
                            f"Expected properties:\n{case.expected}\n\n"
                            f"Answer:\n{answer[:5000]}"
                        ),
                    },
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except (APIError, json.JSONDecodeError, TypeError, ValueError):
            return _deterministic_judgment(case, answer)
        return payload if isinstance(payload, dict) else _deterministic_judgment(case, answer)


def _deterministic_judgment(case: EvalCase, answer: str) -> dict[str, Any]:
    lowered = answer.lower()
    tags = []
    if "not financial advice" not in lowered and "financial advice" not in lowered:
        tags.append("missing_safety")
    if "evidence" in case.expected.lower() and "evidence" not in lowered:
        tags.append("no_evidence")
    if any(fake in answer.split() for fake in ("A", "V", "THIS")):
        tags.append("wrong_ticker")
    passed = not tags
    score = 4 if passed else 2
    return {
        "scores": {
            "relevance": score,
            "ticker_correctness": score,
            "theme_correctness": score,
            "evidence_quality": score,
            "freshness": 3,
            "source_attribution": score,
            "usefulness": score,
            "safety": 5 if "missing_safety" not in tags else 2,
            "concision": 4,
        },
        "pass": passed,
        "tags": tags,
        "notes": "deterministic fallback judgment",
    }


def _load_cases(path: Path, limit: int) -> list[EvalCase]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            cases.append(
                EvalCase(
                    id=str(payload["id"]),
                    prompt=str(payload["prompt"]),
                    expected=str(payload["expected"]),
                )
            )
            if len(cases) >= limit:
                break
    return cases


def _markdown_report(results: list[dict[str, Any]]) -> str:
    passed = sum(1 for result in results if result["judgment"].get("pass"))
    lines = [
        "# Market Research Evaluation Report",
        "",
        f"- Cases: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {len(results) - passed}",
        "",
        "## Cases",
    ]
    for result in results:
        judgment = result["judgment"]
        status = "PASS" if judgment.get("pass") else "FAIL"
        lines.extend(
            [
                "",
                f"### {result['id']} - {status}",
                f"- Prompt: {result['prompt']}",
                f"- Tags: {', '.join(judgment.get('tags', [])) or 'none'}",
                f"- Notes: {judgment.get('notes', '')}",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run market research answer evals.")
    parser.add_argument(
        "--cases",
        default="docs/market-research/evals/market_research_cases.jsonl",
        help="JSONL eval case file.",
    )
    args = parser.parse_args()
    jsonl_path, report_path = asyncio.run(run_eval(Path(args.cases), get_settings()))
    print(f"JSONL: {jsonl_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
