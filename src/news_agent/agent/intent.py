import json
import logging

from openai import APIError, AsyncOpenAI

from news_agent.agent.router import extract_stock_symbols, parse_message
from news_agent.graph.state import Intent
from news_agent.settings import Settings

ROUTABLE_INTENTS: set[Intent] = {
    "stocks",
    "runtime",
    "research",
    "candidates",
    "signals",
    "general_chat",
    "help",
}
ROUTER_SYSTEM_PROMPT = """
You are the routing layer for a Telegram assistant with three product surfaces:
1. Market-impact research
2. Market quotes and technical analysis
3. Runtime debugging and execution-history lookup

Return only valid JSON with this exact schema:
{
  "intent": "stocks" | "runtime" | "research" | "candidates"
    | "signals" | "general_chat" | "help",
  "args": ["STRING", "..."]
}

Routing policy:
- Use "stocks" for requests about stock price, quote, performance, movement,
  ticker lookup, chart-style commentary, or technical analysis.
- Use "research" for market-impact questions about finance, earnings, filings,
  macro, company-impacting technology, policy/regulatory, or geopolitics with
  plausible market relevance.
- Use "runtime" for requests about runtime history, refresh steps, execution
  traces, recent failures, alerts, job status, or debugging what happened
  during a run.
- Use "candidates" for requests about names, stocks, or themes starting to get
  attention, weak signals, emerging attention, or current rankings.
- Use "signals" for requests asking why a specific ticker is ranked or showing
  up in market research signals.
- Use "research" for deep market research requests across themes, candidates,
  market-moving news, or attention/momentum signals.
- Use "help" when the user is explicitly asking what the assistant can do or how to use it.
- Use "general_chat" for casual conversation, broad factual questions, and
  general current-events questions outside market research and stock-analysis
  flows. These requests will be answered with general web search.

Args policy:
- For "stocks", args should contain canonical ticker symbols when identifiable from the message.
- For "research" and "signals", args should contain identifiable ticker symbols
  when relevant.
- If a company name clearly maps to a public ticker, resolve it to the ticker.
- Do not invent tickers when the entity is ambiguous or not clearly public.
- Keep args empty when no useful structured symbol extraction is possible.

Examples:
- "what's google performance today" -> {"intent":"stocks","args":["GOOGL"]}
- "research nvidia and today's ai capex news" -> {"intent":"research","args":["NVDA"]}
- "what happened in the stock market today" -> {"intent":"research","args":[]}
- "what happened in the last refresh?" -> {"intent":"runtime","args":[]}
- "what names are starting to get attention?" -> {"intent":"candidates","args":[]}
- "why is MU showing up in the candidates list?" -> {"intent":"signals","args":["MU"]}
- "what can you do?" -> {"intent":"help","args":[]}
- "who won the world series last year?" -> {"intent":"general_chat","args":[]}
- "hello" -> {"intent":"general_chat","args":[]}

Do not output prose, markdown, or explanations.
""".strip()
logger = logging.getLogger(__name__)


class IntentClassifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    async def classify(self, text: str) -> tuple[str, list[str], Intent]:
        command, args, intent = parse_message(text)
        if command:
            return command, args, intent

        if self.client is None:
            return self._fallback_classify(text)

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:1000]},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
        except APIError:
            return self._fallback_classify(text)

        try:
            payload = json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            logger.warning("intent router returned invalid JSON")
            return self._fallback_classify(text)

        routed_intent = str(payload.get("intent", "")).strip().lower()
        routed_args = payload.get("args", [])
        if routed_intent not in ROUTABLE_INTENTS:
            return self._fallback_classify(text)
        if not isinstance(routed_args, list):
            routed_args = []

        normalized_args = self._normalize_args(routed_args)
        if routed_intent in {"stocks", "research", "signals"} and not normalized_args:
            normalized_args = extract_stock_symbols(text)
        return "", normalized_args, routed_intent

    def _fallback_classify(self, text: str) -> tuple[str, list[str], Intent]:
        symbols = extract_stock_symbols(text)
        lowered = text.lower()
        if any(term in lowered for term in ("help", "what can you do", "commands", "/help")):
            return "", [], "help"
        if any(
            term in lowered
            for term in ("last refresh", "during refresh", "runtime", "trace", "alert", "debug")
        ):
            return "", [], "runtime"
        if any(
            term in lowered
            for term in ("starting to get attention", "weak signals", "candidates")
        ):
            return "", symbols, "candidates"
        if (
            "why" in lowered
            and symbols
            and any(term in lowered for term in ("rank", "signal", "showing up"))
        ):
            return "", symbols, "signals"
        if any(
            term in lowered
            for term in (
                "deep research",
                "market news",
                "stock market today",
                "earnings",
                "filings",
                "macro",
                "regulation",
                "policy",
                "geopolitics",
            )
        ):
            return "", symbols, "research"
        if symbols:
            return "", symbols, "stocks"
        return "", [], "general_chat"

    def _normalize_args(self, args: list[object]) -> list[str]:
        normalized: list[str] = []
        for item in args:
            if not isinstance(item, str):
                continue
            value = item.strip().upper()
            if not value:
                continue
            normalized.append(value)
        return list(dict.fromkeys(normalized))
