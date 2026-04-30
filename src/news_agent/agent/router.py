from news_agent.graph.state import Intent

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
    "/memory": "memory",
    "/forget": "forget",
    "/resetmemory": "resetmemory",
    "/help": "help",
    "/start": "help",
}


def parse_message(text: str) -> tuple[str, list[str], Intent]:
    parts = text.strip().split()
    if not parts:
        return "", [], "unknown"

    command = parts[0].lower()
    args = parts[1:]
    if command.startswith("/"):
        return command, args, COMMAND_INTENTS.get(command, "unknown")

    lowered = text.lower()
    if any(token in lowered for token in ("stock", "ticker", "price", "market")):
        return "", parts, "stocks"
    if any(token in lowered for token in ("news", "brief", "headline", "happened")):
        return "", parts, "brief"
    return "", parts, "general_chat"
