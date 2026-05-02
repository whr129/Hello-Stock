import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.agent.chains import build_stocks_response
from news_agent.agent.router import extract_stock_symbols
from news_agent.app.state import AgentResult, SupervisorState
from news_agent.markets.provider import MarketDataProvider
from news_agent.observability.runtime import RuntimeTraceService
from news_agent.settings import Settings
from news_agent.storage.repositories import TickerRepository
from news_agent.storage.retrieval import RetrievalService


class MarketSubagent:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        market_provider: MarketDataProvider,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.market_provider = market_provider
        self.trace_service = RuntimeTraceService(session_factory, settings)

    async def run(self, state: SupervisorState) -> AgentResult:
        capabilities = set(state.get("route", {}).get("capabilities", []))
        if "watchlist_admin" in capabilities:
            return await self._watchlist_admin(state)
        return await self._market_snapshot(state)

    async def _watchlist_admin(self, state: SupervisorState) -> AgentResult:
        command = state.get("command", "")
        args = state.get("args", [])
        user_id = state["user_context"]["user_id"]
        async with self.session_factory() as session:
            repository = TickerRepository(session)
            if command == "/watch":
                added = await repository.add_many(user_id, args)
                await session.commit()
                current = sorted(set(state["user_context"].get("watched_tickers", [])) | set(added))
                state["user_context"]["watched_tickers"] = current
                return {
                    "response": f"Now watching: {', '.join(added) or 'no new tickers'}",
                    "metadata": {"capability": "watchlist_admin"},
                }

            if command == "/unwatch":
                removed = await repository.remove_many(user_id, args)
                await session.commit()
                removed_set = set(removed)
                current = [
                    ticker
                    for ticker in state["user_context"].get("watched_tickers", [])
                    if ticker not in removed_set
                ]
                state["user_context"]["watched_tickers"] = current
                return {
                    "response": f"Removed: {', '.join(removed) or 'none'}",
                    "metadata": {"capability": "watchlist_admin"},
                }

        return {
            "response": "Watchlist request could not be completed.",
            "metadata": {"capability": "watchlist_admin"},
        }

    async def _market_snapshot(self, state: SupervisorState) -> AgentResult:
        tickers = requested_tickers(state)
        if not tickers:
            return {
                "response": "You are not watching any tickers yet. Use /watch AAPL TSLA to add some.",
                "metadata": {
                    "capability": "market_snapshot",
                    "ticker_count": 0,
                    "snapshot_count": 0,
                    "needs_search_fallback": False,
                },
            }

        stored_context = await self._stored_market_context(state, tickers)
        stored_by_symbol = {item.get("symbol"): item for item in stored_context}
        market_context: list[dict[str, Any]] = []
        for ticker in tickers:
            provider_step_id: int | None = None
            try:
                if state.get("runtime_run_id"):
                    provider_step_id = await self.trace_service.start_step(
                        run_id=state["runtime_run_id"],
                        workflow="chat",
                        step_name=f"market:{ticker}",
                        step_type="provider",
                        parent_step_id=state.get("active_step_id"),
                        metadata={"ticker": ticker},
                    )
                snapshot = await asyncio.wait_for(
                    asyncio.to_thread(self.market_provider.get_snapshot, ticker),
                    timeout=self.settings.market_fetch_timeout_seconds,
                )
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="completed",
                        metadata={"symbol": snapshot.symbol},
                    )
                market_context.append(
                    {
                        "symbol": snapshot.symbol,
                        "price": snapshot.price,
                        "percent_change": snapshot.percent_change,
                        "indicators": snapshot.indicators,
                        "source": "live",
                    }
                )
            except Exception as exc:
                if provider_step_id is not None:
                    await self.trace_service.finish_step(
                        provider_step_id,
                        status="failed",
                        error_message=str(exc),
                    )
                    await self.trace_service.record_error(
                        run_id=state["runtime_run_id"],
                        workflow="chat",
                        step_name=f"market:{ticker}",
                        error_message=str(exc),
                        step_id=provider_step_id,
                        metadata={"ticker": ticker},
                    )
                fallback = stored_by_symbol.get(ticker)
                if fallback:
                    market_context.append(fallback)

        return {
            "response": build_stocks_response(tickers, market_context),
            "metadata": {
                "capability": "market_snapshot",
                "ticker_count": len(tickers),
                "snapshot_count": len(market_context),
                "needs_search_fallback": len(market_context) == 0,
            },
        }

    async def _stored_market_context(
        self, state: SupervisorState, tickers: list[str]
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            context = await RetrievalService(session).retrieve_for_brief(
                user_id=state["user_context"]["user_id"],
                topics=[],
                tickers=tickers,
                article_max_age_hours=self.settings.news_freshness_hours,
                summary_max_age_hours=self.settings.summary_freshness_hours,
                snapshot_max_age_minutes=self.settings.snapshot_freshness_minutes,
            )
        return [
            {
                "symbol": snapshot.symbol,
                "price": snapshot.price,
                "percent_change": snapshot.percent_change,
                "indicators": snapshot.indicators,
                "source": "stored",
            }
            for snapshot in context.market_snapshots
        ]


def requested_tickers(state: SupervisorState) -> list[str]:
    requested = [ticker.upper() for ticker in state.get("requested_symbols", []) if ticker.strip()]
    if requested:
        return sorted(dict.fromkeys(requested))

    args = [
        item.upper()
        for item in state.get("args", [])
        if item.isalpha() and 1 <= len(item) <= 5
    ]
    if (state.get("command") == "/stocks" or state.get("intent") == "stocks") and args:
        return sorted(dict.fromkeys(args))

    symbols = extract_stock_symbols(state.get("message_text", ""))
    if symbols:
        return symbols

    user_context = state.get("user_context", {})
    watched = [ticker.upper() for ticker in user_context.get("watched_tickers", [])]
    if not watched:
        watched = [ticker.upper() for ticker in state.get("watched_tickers", [])]
    return sorted(dict.fromkeys(watched))
