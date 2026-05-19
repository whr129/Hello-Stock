from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.app.state import AgentResult, SupervisorState
from news_agent.observability.runtime import RuntimeTraceService
from news_agent.research.analysis import explain_candidates
from news_agent.research.planner import PlannerAgent
from news_agent.research.reporting import format_candidates, format_research_status, format_signal
from news_agent.research.scheduler import (
    extract_market_mentions,
    prune_market_research_data,
    score_market_signals,
)
from news_agent.settings import Settings
from news_agent.storage.repositories import MarketSignalRepository, RuntimeRunRepository


class ResearchSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.planner = PlannerAgent()
        self.trace_service = RuntimeTraceService(session_factory, settings)

    async def run(self, state: SupervisorState) -> AgentResult:
        plan = self.planner.plan(
            command=state.get("command", ""),
            args=state.get("args", []),
            message_text=state.get("message_text", ""),
        )
        step_ids: list[int] = []
        if state.get("runtime_run_id"):
            step_ids.append(
                await self.trace_service.start_step(
                    run_id=state["runtime_run_id"],
                    workflow="chat",
                    step_name="research:plan",
                    step_type="tool",
                    parent_step_id=state.get("active_step_id"),
                    metadata={
                        "task_type": plan.task_type,
                        "tickers": plan.entities.tickers,
                        "agents": plan.agents_to_run,
                    },
                )
            )
            await self.trace_service.finish_step(step_ids[-1], status="completed")

        if plan.command == "/researchstatus":
            async with self.session_factory() as session:
                runs = await RuntimeRunRepository(session).list_recent(
                    limit=5,
                    workflow="market_research",
                )
            return {
                "response": format_research_status(runs),
                "metadata": {"capability": "market_research", "plan": plan.task_type},
            }

        async with self.session_factory() as session:
            if plan.command == "/research":
                extraction_step_id = await self._start_iteration_step(
                    state,
                    "research:extract_mentions",
                    {"limit": 100},
                )
                mention_count = await extract_market_mentions(
                    session,
                    self.settings,
                    limit=100,
                )
                await self._finish_iteration_step(
                    extraction_step_id,
                    {"mention_count": mention_count},
                )
                scoring_step_id = await self._start_iteration_step(
                    state,
                    "research:score_signals",
                    {"windows": ["1h", "24h", "7d", "30d"]},
                )
                signal_count = await score_market_signals(session, self.settings)
                await self._finish_iteration_step(scoring_step_id, {"signal_count": signal_count})
                cleanup_step_id = await self._start_iteration_step(state, "research:cleanup", {})
                pruned_count = await prune_market_research_data(session, self.settings)
                await self._finish_iteration_step(cleanup_step_id, {"pruned_count": pruned_count})
                await session.commit()
            else:
                mention_count = 0
                signal_count = 0
                pruned_count = 0

            repository = MarketSignalRepository(session)
            since = datetime.now(UTC) - timedelta(days=30)
            retrieval_step_id = await self._start_iteration_step(
                state,
                "research:retrieve_context",
                {"task_type": plan.task_type, "since_days": 30},
            )
            if plan.task_type == "stock_lookup":
                ticker = plan.entities.tickers[0] if plan.entities.tickers else ""
                snapshots = await repository.fetch_signal_history(ticker, limit=10)
                response = format_signal(explain_candidates(snapshots, ticker=ticker), ticker)
            else:
                snapshots = await repository.fetch_top_candidates(
                    window="24h",
                    limit=plan.constraints.max_candidates,
                    since=since,
                )
                response = format_candidates(explain_candidates(snapshots))
            await self._finish_iteration_step(
                retrieval_step_id,
                {"snapshot_count": len(snapshots), "context_compaction": "deterministic_top_n"},
            )

        return {
            "response": response,
            "metadata": {
                "capability": "market_research",
                "plan": plan.task_type,
                "mention_count": mention_count,
                "signal_count": signal_count,
                "pruned_count": pruned_count,
            },
        }

    async def _start_iteration_step(
        self,
        state: SupervisorState,
        name: str,
        metadata: dict,
    ) -> int | None:
        if not state.get("runtime_run_id"):
            return None
        return await self.trace_service.start_step(
            run_id=state["runtime_run_id"],
            workflow="chat",
            step_name=name,
            step_type="tool",
            parent_step_id=state.get("active_step_id"),
            metadata=metadata,
        )

    async def _finish_iteration_step(self, step_id: int | None, metadata: dict) -> None:
        if step_id is not None:
            await self.trace_service.finish_step(step_id, status="completed", metadata=metadata)
