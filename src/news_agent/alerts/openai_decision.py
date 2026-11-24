from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from news_agent.alerts.decision import JevDecision, JevError, JevUsage, build_questions

OPENAI_DECISION_MODEL = "gpt-6-luna"
OPENAI_DECISION_INPUT_PRICE_PER_MILLION = 0.10
OPENAI_DECISION_PROMPT_VERSION = "market-alert-openai-v1"
_ENDPOINT = "https://api.openai.com/v1/decisions"
_MAX_RESPONSE_BYTES = 262144
_BILLING_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "project_spend_limit_exceeded",
    "organization_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
}


def _number(value: object, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid number")
    return float(value)


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid token count")
    return value


def _questions(tickers: Sequence[str]) -> list[dict[str, Any]]:
    result = []
    for name, question in build_questions(tickers).items():
        item = {"name": name, "type": question["type"], "instructions": question["instructions"]}
        if question["type"] == "choice":
            item["choices"] = [
                {"value": value, "description": description}
                for value, description in question["criteria"].items()
            ]
        else:
            item["levels"] = [{"label": label} for label in question["criteria"]]
        result.append(item)
    return result


def _usage(payload: object) -> JevUsage:
    if not isinstance(payload, dict):
        raise ValueError("Missing usage")
    inputs = _integer(payload.get("input_tokens"))
    outputs = _integer(payload.get("output_tokens"))
    details = payload.get("input_tokens_details")
    if details is None:
        details = {}
    if not isinstance(details, dict):
        raise ValueError("Invalid usage details")
    cached = _integer(details.get("cached_tokens", 0))
    writes = _integer(details.get("cache_write_tokens", 0))
    if cached + writes > inputs:
        raise ValueError("Unexpected cache accounting")
    if "total_tokens" in payload and _integer(payload["total_tokens"]) != inputs + outputs:
        raise ValueError("Invalid total tokens")
    return JevUsage(
        input_tokens=inputs,
        output_tokens=outputs,
        # Charge reads at uncached rates and unexpected 30-minute writes at their higher rate.
        cost=(inputs * OPENAI_DECISION_INPUT_PRICE_PER_MILLION + writes * 0.025) / 1_000_000,
    )


def _parse(payload: object, tickers: Sequence[str], request_id: str | None) -> JevDecision:
    usage = None
    try:
        if not isinstance(payload, dict):
            raise ValueError("Invalid response")
        usage = _usage(payload.get("usage"))
        if payload.get("status") not in (None, "completed"):
            raise ValueError("Incomplete response")
        model = payload.get("model")
        if not isinstance(model, str) or not (
            model == OPENAI_DECISION_MODEL or model.startswith(OPENAI_DECISION_MODEL + "-")
        ):
            raise ValueError("Unexpected model")
        questions = build_questions(tickers)
        answers = payload.get("answers")
        if not isinstance(answers, list) or len(answers) != len(questions):
            raise ValueError("Unexpected answer count")
        parsed = {}
        distributions = {}
        for answer in answers:
            if not isinstance(answer, dict):
                raise ValueError("Invalid answer")
            name = answer.get("name")
            if not isinstance(name, str) or name not in questions or name in parsed:
                raise ValueError("Unexpected answer name")
            question = questions[name]
            if answer.get("type") != question["type"]:
                raise ValueError("Refused or unexpected answer")
            _number(answer.get("confidence"), 1)
            is_score = question["type"] == "score"
            criteria = question["criteria"]
            expected = set(range(len(criteria))) if is_score else set(criteria)
            probabilities = answer.get("probabilities")
            if not isinstance(probabilities, list) or len(probabilities) != len(expected):
                raise ValueError("Unexpected options")
            distribution = {}
            for item in probabilities:
                if not isinstance(item, dict):
                    raise ValueError("Invalid probability")
                value = item.get("value")
                if type(value) is not (int if is_score else str):
                    raise ValueError("Invalid option type")
                if value not in expected or str(value) in distribution:
                    raise ValueError("Unexpected option")
                if is_score and item.get("label") != criteria[value]:
                    raise ValueError("Unexpected score label")
                distribution[str(value)] = _number(item.get("probability"), 1)
            if not math.isclose(sum(distribution.values()), 1, rel_tol=0, abs_tol=1e-6):
                raise ValueError("Invalid distribution")
            if is_score:
                score = _number(answer.get("score"), len(criteria) - 1)
                expected_score = sum(
                    int(level) * probability for level, probability in distribution.items()
                )
                if not math.isclose(score, expected_score, rel_tol=0, abs_tol=1e-6):
                    raise ValueError("Inconsistent score")
            else:
                choice = answer.get("choice")
                if not isinstance(choice, str) or choice not in expected:
                    raise ValueError("Unexpected choice")
                if distribution[choice] + 1e-6 < max(distribution.values()):
                    raise ValueError("Inconsistent choice")
            parsed[name] = answer
            distributions[name] = distribution
        return JevDecision(
            delivery=parsed["delivery"]["choice"],
            confidence=parsed["delivery"]["confidence"],
            materiality=parsed["materiality"]["score"],
            event_type=parsed["event_type"]["choice"],
            reason_code=parsed["reason"]["choice"],
            sentiments={
                ticker: parsed[f"sentiment_{i}"]["choice"] for i, ticker in enumerate(tickers)
            },
            model=model,
            usage=usage,
            probabilities=distributions,
            attempts=1,
            prompt_version=OPENAI_DECISION_PROMPT_VERSION,
            provider="openai",
            request_id=request_id,
        )
    except (ValueError, TypeError, KeyError, OverflowError, ValidationError):
        raise JevError("invalid_response", attempts=1, usage=usage) from None


class OpenAIDecisionClient:
    def __init__(
        self, api_key: str, *, timeout_seconds: float = 15, client: httpx.AsyncClient | None = None
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError("Invalid decision timeout")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=False)

    async def __aenter__(self) -> OpenAIDecisionClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def assess(self, state: Mapping[str, Any], *, tickers: Sequence[str] = ()) -> JevDecision:
        if not self._api_key or any(character.isspace() for character in self._api_key):
            raise JevError("authentication")
        if (
            isinstance(tickers, str | bytes)
            or len(tickers) > 10
            or any(
                not isinstance(t, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", t)
                for t in tickers
            )
            or len(set(tickers)) != len(tickers)
            or not isinstance(state, Mapping)
        ):
            raise JevError("validation")
        try:
            state_json = json.dumps(dict(state), ensure_ascii=False, allow_nan=False)
            state_bytes = len(state_json.encode("utf-8"))
        except (ValueError, TypeError, RecursionError):
            raise JevError("validation") from None
        if len(state_json) > 12000 or state_bytes > 48000:
            raise JevError("input_too_large")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "POST",
                    _ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": OPENAI_DECISION_MODEL,
                        "input": state_json,
                        "questions": _questions(tickers),
                        "prompt_cache_options": {"mode": "explicit"},
                    },
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_RESPONSE_BYTES:
                            raise JevError("invalid_response", attempts=1)
                    try:
                        data = json.loads(content)
                    except (ValueError, UnicodeError, RecursionError):
                        data = None
                    if response.status_code != 200:
                        self._raise_status(response, data)
                    return _parse(data, tickers, response.headers.get("x-request-id"))
        except (TimeoutError, httpx.TimeoutException):
            raise JevError("timeout", retryable=True, attempts=1) from None
        except httpx.RequestError:
            raise JevError("unavailable", retryable=True, attempts=1) from None

    def _raise_status(self, response: httpx.Response, data: object) -> None:
        status = response.status_code
        error = data.get("error", {}) if isinstance(data, dict) else {}
        billing = status == 402 or (
            status == 429
            and isinstance(error, dict)
            and any(
                isinstance(error.get(key), str) and error[key] in _BILLING_CODES
                for key in ("code", "type")
            )
        )
        code = (
            "billing_limit"
            if billing
            else {
                401: "authentication",
                403: "authentication",
                400: "validation",
                422: "validation",
                429: "rate_limited",
            }.get(status, "unavailable")
        )
        try:
            retry_after = float(response.headers.get("retry-after", "0"))
        except ValueError:
            retry_after = 0
        if not math.isfinite(retry_after):
            retry_after = 0
        raise JevError(
            code,
            status_code=status,
            attempts=1,
            retryable=not billing and (status == 429 or 500 <= status <= 599),
            usage=JevUsage(input_tokens=0, output_tokens=0, cost=0)
            if billing or status in (401, 403)
            else None,
            retry_after_seconds=max(0, min(retry_after, self._timeout_seconds)),
        )
