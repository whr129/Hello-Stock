from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from openai import APIError, AsyncOpenAI

from news_agent.app.state import Intent
from news_agent.settings import Settings

logger = logging.getLogger(__name__)

ReflectionVerdict = Literal["pass", "retry", "fail"]

REFLECTION_PROMPT = """
You are a strict quality-control layer for a Telegram assistant.

Decide whether the assistant selected the right intent, subagent/tool path, and final answer
for the user's request.

Return only valid JSON with this exact schema:
{
  "verdict": "pass" | "retry" | "fail",
  "reason": "short internal reason",
  "corrected_intent": "stocks|runtime|research|candidates|signals|general_chat|help|null",
  "corrected_args": ["STRING", "..."]
}

Rules:
- Use "pass" when the answer reasonably addresses the request.
- Use "retry" only when the route/tool/subagent is clearly wrong or the answer is clearly
  mismatched to the request.
- Use "fail" only when the answer is unusable and retrying with a different route is not likely
  to help.
- Do not retry for minor style issues, missing nuance, or harmless wording.
- If the user asks about stocks, prices, tickers, securities, or technical analysis,
  corrected_intent should usually be "stocks".
- If the user asks about runtime history, traces, jobs, alerts, refresh failures, or debugging,
  corrected_intent should usually be "runtime".
- If the user asks for market-moving news, company-impact research, macro,
  policy/regulatory, or candidate/signal analysis, corrected_intent should
  usually be "research", "candidates", or "signals".
- If the user asks a broad factual/general current-events question, corrected_intent should usually
  be "general_chat".
- corrected_args should include ticker symbols only when relevant.
""".strip()

RETRYABLE_INTENTS: set[str] = {
    "stocks",
    "runtime",
    "research",
    "candidates",
    "signals",
    "general_chat",
    "help",
}


@dataclass(frozen=True)
class ReflectionDecision:
    verdict: ReflectionVerdict
    reason: str
    corrected_intent: Intent | None = None
    corrected_args: list[str] | None = None
    status: str = "ok"


class ReflectionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def reflect(self, state: dict[str, Any]) -> ReflectionDecision:
        if self.client is None:
            return ReflectionDecision(
                verdict="pass",
                reason="reflection unavailable: missing OpenAI client",
                status="unavailable",
            )

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": REFLECTION_PROMPT},
                    {"role": "user", "content": _reflection_payload(state)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=self.settings.llm_timeout_seconds,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except (APIError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("answer reflection failed")
            return ReflectionDecision(
                verdict="pass",
                reason="reflection unavailable: invalid or failed model response",
                status="unavailable",
            )

        return _decision_from_payload(payload)


def _decision_from_payload(payload: dict[str, Any]) -> ReflectionDecision:
    verdict = str(payload.get("verdict", "pass")).strip().lower()
    if verdict not in {"pass", "retry", "fail"}:
        verdict = "pass"

    corrected_intent = payload.get("corrected_intent")
    if corrected_intent is None:
        normalized_intent = None
    else:
        normalized = str(corrected_intent).strip().lower()
        normalized_intent = normalized if normalized in RETRYABLE_INTENTS else None

    if verdict == "retry" and normalized_intent is None:
        verdict = "pass"

    raw_args = payload.get("corrected_args", [])
    corrected_args = [str(item).strip().upper() for item in raw_args if str(item).strip()]

    return ReflectionDecision(
        verdict=verdict,  # type: ignore[arg-type]
        reason=str(payload.get("reason", "")).strip()[:500],
        corrected_intent=normalized_intent,  # type: ignore[arg-type]
        corrected_args=list(dict.fromkeys(corrected_args)),
    )


def _reflection_payload(state: dict[str, Any]) -> str:
    payload = {
        "user_message": state.get("message_text", ""),
        "intent": state.get("intent", ""),
        "args": state.get("args", []),
        "requested_symbols": state.get("requested_symbols", []),
        "route": state.get("route", {}),
        "completed_agents": state.get("completed_agents", []),
        "news_metadata": state.get("news_result", {}).get("metadata", {}),
        "market_metadata": state.get("market_result", {}).get("metadata", {}),
        "runtime_metadata": state.get("runtime_result", {}).get("metadata", {}),
        "search_metadata": state.get("search_result", {}).get("metadata", {}),
        "final_response": str(state.get("final_response", ""))[:4000],
        "reflection_attempts": state.get("reflection_attempts", 0),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)
