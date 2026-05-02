import re
from dataclasses import dataclass

from news_agent.app.state import Capability, Intent

COMMAND_INTENTS: dict[str, Intent] = {
    "/brief": "brief",
    "/stocks": "stocks",
    "/watch": "watch",
    "/unwatch": "unwatch",
    "/topics": "topics",
    "/local": "local",
    "/sources": "sources",
    "/addsource": "addsource",
    "/removesource": "removesource",
    "/sourceconfig": "sourceconfig",
    "/sourcefields": "sourcefields",
    "/sourcetest": "sourcetest",
    "/refresh": "refresh",
    "/memory": "memory",
    "/forget": "forget",
    "/resetmemory": "resetmemory",
    "/timezone": "timezone",
    "/recaptime": "recaptime",
    "/recapoff": "recapoff",
    "/recapstatus": "recapstatus",
    "/runtime": "runtime",
    "/job": "job",
    "/trace": "trace",
    "/step": "step",
    "/alerts": "alerts",
    "/skills": "skills",
    "/help": "help",
    "/start": "help",
}

CASHTAG_PATTERN = re.compile(r"\$([A-Za-z]{1,5})(?:\b|$)")
UPPERCASE_TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")
DIRECT_TICKER_PATTERN = re.compile(r"\b(?:for|of)\s+\$?([A-Za-z]{1,5})(?:\b|$)")
NON_TICKER_WORDS = {"FOR", "MARKET", "PRICE", "QUOTE", "STOCK", "THE"}


@dataclass(frozen=True)
class RouteDecision:
    agents: tuple[str, ...]
    capabilities: tuple[Capability, ...]
    fallback_response: str | None = None


def help_response() -> str:
    return (
        "I can help with personalized news, market analysis, and general web lookups. "
        "Try /skills for the full command list, or ask a general question directly."
    )


def skills_response() -> str:
    return (
        "Available skills and commands:\n"
        "\n"
        "News briefings\n"
        "- /brief\n"
        "- /topics <topic...>\n"
        "- /local <region>\n"
        "\n"
        "Market data and technical analysis\n"
        "- /stocks <ticker...>\n"
        "- /watch <ticker...>\n"
        "- /unwatch <ticker...>\n"
        "\n"
        "Source management\n"
        "- /sources\n"
        "- /addsource <provider> <target>\n"
        "- /sourceconfig <source-id> <key> <value>\n"
        "- /sourcefields <source-id> <field> <mapped-value>\n"
        "- /sourcetest <source-id>\n"
        "- /removesource <source-id>\n"
        "\n"
        "Refresh and recap\n"
        "- /refresh\n"
        "- /timezone <Area/City>\n"
        "- /recaptime <HH:MM>\n"
        "- /recapoff\n"
        "- /recapstatus\n"
        "\n"
        "Runtime debugging\n"
        "- /runtime\n"
        "- /job <run-id>\n"
        "- /trace <run-id>\n"
        "- /step <run-id> <step-name>\n"
        "- /alerts\n"
        "\n"
        "Memory and assistant info\n"
        "- /memory\n"
        "- /forget <memory-id>\n"
        "- /resetmemory\n"
        "- /help\n"
        "- /skills\n"
        "\n"
        "General web questions\n"
        "- Ask a question directly, for example: who won the world series last year?"
    )


def parse_message(text: str) -> tuple[str, list[str], Intent]:
    parts = text.strip().split()
    if not parts:
        return "", [], "unknown"

    command = parts[0].lower()
    args = parts[1:]
    if command.startswith("/"):
        return command, args, COMMAND_INTENTS.get(command, "unknown")

    return "", [], "general_chat"


def route_request(
    intent: Intent,
    message_text: str = "",
    command: str = "",
    args: list[str] | None = None,
) -> RouteDecision:
    del command
    args = args or []
    has_symbol_args = any(_looks_like_ticker(item) for item in args)

    if intent == "brief":
        if has_symbol_args:
            return RouteDecision(
                agents=("news", "market"),
                capabilities=("news_brief", "market_snapshot", "technical_analysis"),
            )
        return RouteDecision(agents=("news",), capabilities=("news_brief",))
    if intent == "stocks":
        return RouteDecision(
            agents=("market",),
            capabilities=("market_snapshot", "technical_analysis"),
        )
    if intent in {"watch", "unwatch"}:
        return RouteDecision(agents=("market",), capabilities=("watchlist_admin",))
    if intent == "topics":
        return RouteDecision(agents=("news",), capabilities=("topic_preferences",))
    if intent == "local":
        return RouteDecision(agents=("news",), capabilities=("local_preferences",))
    if intent in {"sources", "addsource", "removesource"}:
        return RouteDecision(agents=("news",), capabilities=("source_admin",))
    if intent in {"sourceconfig", "sourcefields", "sourcetest"}:
        return RouteDecision(agents=("news",), capabilities=("source_admin",))
    if intent == "refresh":
        return RouteDecision(agents=("news",), capabilities=("scheduler_admin",))
    if intent in {"memory", "forget", "resetmemory"}:
        return RouteDecision(agents=("news",), capabilities=("memory_admin",))
    if intent in {"timezone", "recaptime", "recapoff", "recapstatus"}:
        return RouteDecision(agents=("news",), capabilities=("recap_admin",))
    if intent in {"runtime", "job", "trace", "step"}:
        return RouteDecision(agents=("runtime",), capabilities=("runtime_inspection",))
    if intent == "alerts":
        return RouteDecision(agents=("runtime",), capabilities=("runtime_alerts",))
    if intent == "skills":
        return RouteDecision(agents=("news",), capabilities=("skills",))
    if intent == "help":
        return RouteDecision(agents=("news",), capabilities=("help",))
    if intent == "general_chat":
        if _looks_like_runtime_query(message_text):
            return RouteDecision(agents=("runtime",), capabilities=("runtime_inspection",))
        return RouteDecision(agents=(), capabilities=("general_search",))
    return RouteDecision(agents=(), capabilities=(), fallback_response=help_response())


def route_intent(intent: Intent) -> RouteDecision:
    return route_request(intent)


def extract_stock_symbols(text: str) -> list[str]:
    symbols = [
        match.group(1).upper()
        for match in CASHTAG_PATTERN.finditer(text)
        if _looks_like_ticker(match.group(1))
    ]
    symbols.extend(
        match.group(1).upper()
        for match in UPPERCASE_TICKER_PATTERN.finditer(text)
        if _looks_like_ticker(match.group(1))
    )
    symbols.extend(
        match.group(1).upper()
        for match in DIRECT_TICKER_PATTERN.finditer(text)
        if _looks_like_ticker(match.group(1))
    )
    return sorted(dict.fromkeys(symbols))


def _looks_like_ticker(value: str) -> bool:
    normalized = value.upper()
    return bool(
        normalized.isalpha()
        and 1 <= len(normalized) <= 5
        and normalized not in NON_TICKER_WORDS
    )


def _looks_like_runtime_query(message_text: str) -> bool:
    lowered = message_text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "last refresh",
            "during refresh",
            "calling history",
            "call history",
            "debug",
            "which step",
            "runtime",
            "trace",
            "alert",
            "failed source",
        )
    )
