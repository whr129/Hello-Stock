from typing import Any


def build_brief_response(
    articles: list[dict[str, Any]],
    summaries: list[str],
    market_context: list[dict[str, Any]],
    local_region: str,
) -> str:
    lines = [f"News brief for global, local ({local_region}), and market context:"]

    if articles:
        lines.append("\nTop headlines:")
        for article in articles[:5]:
            source = article.get("source") or "unknown source"
            lines.append(f"- {article.get('title', 'Untitled')} (source: {source})")
    else:
        lines.append("\nNo fresh articles are stored yet. Run the scheduler after adding sources.")

    if summaries:
        lines.append("\nRecent summaries:")
        lines.extend(f"- {summary}" for summary in summaries[:3])

    if market_context:
        lines.append("\nWatched stocks:")
        for item in market_context[:5]:
            change = item.get("percent_change")
            change_text = f"{change:.2f}%" if isinstance(change, (int, float)) else "n/a"
            lines.append(f"- {item.get('symbol')}: {item.get('price', 'n/a')} ({change_text})")

    return "\n".join(lines)


def build_stocks_response(tickers: list[str], market_context: list[dict[str, Any]]) -> str:
    if not tickers:
        return "You are not watching any tickers yet. Use /watch AAPL TSLA to add some."

    lines = ["Watched stock context:"]
    snapshot_by_symbol = {item.get("symbol"): item for item in market_context}
    for ticker in tickers:
        snapshot = snapshot_by_symbol.get(ticker)
        if not snapshot:
            lines.append(f"- {ticker}: no recent market snapshot yet.")
            continue
        indicators = snapshot.get("indicators") or {}
        lines.append(
            f"- {ticker}: price {snapshot.get('price', 'n/a')}, "
            f"change {snapshot.get('percent_change', 'n/a')}%, "
            f"RSI {indicators.get('rsi', 'n/a')}, trend {indicators.get('trend', 'n/a')}."
        )
    return "\n".join(lines)
