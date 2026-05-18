from typing import Any


def build_market_research_digest(
    articles: list[dict[str, Any]],
    summaries: list[str],
    market_context: list[dict[str, Any]],
) -> str:
    lines = ["Market-impact research digest:"]

    if articles:
        lines.append("\nTop headlines:")
        for article in articles[:5]:
            source = article.get("source") or "unknown source"
            lines.append(f"- {article.get('title', 'Untitled')} (source: {source})")
    else:
        lines.append(
            "\nNo fresh market-impact articles are stored yet. "
            "Run /refresh after adding sources."
        )

    if summaries:
        lines.append("\nRecent summaries:")
        lines.extend(f"- {summary}" for summary in summaries[:3])

    if market_context:
        lines.append("\nMarket snapshots:")
        for item in market_context[:5]:
            change = item.get("percent_change")
            change_text = f"{change:.2f}%" if isinstance(change, (int, float)) else "n/a"
            lines.append(f"- {item.get('symbol')}: {item.get('price', 'n/a')} ({change_text})")

    return "\n".join(lines)


def build_stocks_response(tickers: list[str], market_context: list[dict[str, Any]]) -> str:
    if not tickers:
        return "Use /stocks <ticker...>, for example /stocks AAPL TSLA."

    lines = ["Stock context:"]
    snapshot_by_symbol = {item.get("symbol"): item for item in market_context}
    for ticker in tickers:
        snapshot = snapshot_by_symbol.get(ticker)
        if not snapshot:
            lines.append(f"- {ticker}: no fresh market snapshot is available yet.")
            continue
        indicators = snapshot.get("indicators") or {}
        price = _format_number(snapshot.get("price"))
        change = snapshot.get("percent_change")
        change_text = f"{change:.2f}%" if isinstance(change, (int, float)) else "n/a"
        source = snapshot.get("source", "stored")
        lines.extend(
            [
                f"\n{ticker}",
                f"- Latest price: {price} ({source})",
                f"- Performance: {change_text} vs previous close",
                (
                    "- Technicals: "
                    f"trend {indicators.get('trend', 'n/a')}, "
                    f"RSI {indicators.get('rsi', 'n/a')}, "
                    f"SMA20 {_format_number(indicators.get('sma_20'))}, "
                    f"SMA50 {_format_number(indicators.get('sma_50'))}, "
                    f"MACD {_format_number(indicators.get('macd'))}"
                ),
                "- Note: this is informational context, not a buy/sell recommendation.",
            ]
        )
    return "\n".join(lines)


def _format_number(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "n/a"
