from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from news_agent.app.state import AgentResult, SupervisorState
from news_agent.observability.runtime import RuntimeTraceService
from news_agent.research.analysis import explain_candidates, visible_candidate_explanations
from news_agent.research.link_validation import validate_candidate_links
from news_agent.research.orchestration import (
    CompanyResearchCoordinator,
    should_enrich_research,
)
from news_agent.research.planner import PlannerAgent
from news_agent.research.reporting import (
    format_candidates,
    format_research_status,
    format_signal,
    format_source_health,
)
from news_agent.research.scheduler import (
    backfill_signal_evidence_links,
    count_confident_signal_context,
    enrich_market_sectors,
    extract_market_mentions,
    prune_market_research_data,
    score_market_signals,
)
from news_agent.research.schemas import CandidateExplanation, CompanyResearchPacket
from news_agent.settings import Settings
from news_agent.storage.repositories import (
    MarketSignalRepository,
    RuntimeRunRepository,
    SourceRepository,
)


class ResearchSubagent:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.planner = PlannerAgent(settings)
        self.trace_service = RuntimeTraceService(session_factory, settings)
        self.company_research = CompanyResearchCoordinator(settings)

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
                        "sectors": plan.entities.sectors,
                        "agents": plan.agents_to_run,
                    },
                )
            )
            await self.trace_service.finish_step(step_ids[-1], status="completed")

        if plan.command == "/researchstatus":
            async with self.session_factory() as session:
                recent_runs = await RuntimeRunRepository(session).list_recent(limit=20)
                workflows = {"market_research", "market_research_refresh", "manual_refresh"}
                runs = [run for run in recent_runs if run.workflow in workflows][:5]
                sources = await SourceRepository(session).list_all_enabled()
            return {
                "response": format_research_status(runs, sources),
                "metadata": {"capability": "market_research", "plan": plan.task_type},
            }

        if plan.command == "/sourcehealth":
            async with self.session_factory() as session:
                sources = await SourceRepository(session).list_all()
            return {
                "response": format_source_health(sources),
                "metadata": {"capability": "market_research", "plan": plan.task_type},
            }

        if plan.command == "/signals" and not plan.entities.tickers:
            return {
                "response": "Usage: /signals <ticker>",
                "metadata": {
                    "capability": "market_research",
                    "plan": plan.task_type,
                    "status": "missing_ticker",
                },
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
                sector_step_id = await self._start_iteration_step(
                    state,
                    "research:sector_enrichment",
                    {"sectors": plan.entities.sectors},
                )
                sector_context_count = await enrich_market_sectors(session, self.settings)
                await self._finish_iteration_step(
                    sector_step_id,
                    {"sector_context_count": sector_context_count},
                )
                backfill_step_id = await self._start_iteration_step(
                    state,
                    "research:evidence_backfill",
                    {"limit": 500},
                )
                backfilled_count = await backfill_signal_evidence_links(session)
                await self._finish_iteration_step(
                    backfill_step_id,
                    {"signal_evidence_backfill_count": backfilled_count},
                )
                scoring_step_id = await self._start_iteration_step(
                    state,
                    "research:score_signals",
                    {"windows": ["1h", "24h", "7d", "30d"]},
                )
                signal_count = await score_market_signals(session, self.settings)
                await self._finish_iteration_step(scoring_step_id, {"signal_count": signal_count})
                confidence_step_id = await self._start_iteration_step(
                    state,
                    "research:confidence_filter",
                    {"window": "24h", "threshold": self.settings.signal_alert_threshold},
                )
                confident_signal_count = await count_confident_signal_context(
                    session,
                    self.settings,
                )
                await self._finish_iteration_step(
                    confidence_step_id,
                    {"confident_signal_count": confident_signal_count},
                )
                cleanup_step_id = await self._start_iteration_step(state, "research:cleanup", {})
                pruned_count = await prune_market_research_data(session, self.settings)
                await self._finish_iteration_step(cleanup_step_id, {"pruned_count": pruned_count})
                await session.commit()
            else:
                mention_count = 0
                sector_context_count = 0
                backfilled_count = 0
                signal_count = 0
                confident_signal_count = 0
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
                explanations = await validate_candidate_links(
                    explain_candidates(
                        snapshots,
                        ticker=ticker,
                        min_strong_sources=self.settings.signal_min_strong_evidence_sources,
                    ),
                    recheck_hours=self.settings.evidence_link_recheck_hours,
                )
                await _persist_validated_evidence(repository, explanations)
                report_explanations = explanations
                enrichment_candidates = explanations[:1]
            else:
                snapshots = await repository.fetch_top_candidates(
                    window="24h",
                    limit=max(plan.constraints.max_candidates * 3, plan.constraints.max_candidates),
                    since=since,
                )
                explanations = await validate_candidate_links(
                    explain_candidates(
                        snapshots,
                        min_strong_sources=self.settings.signal_min_strong_evidence_sources,
                    ),
                    recheck_hours=self.settings.evidence_link_recheck_hours,
                )
                await _persist_validated_evidence(repository, explanations)
                report_explanations = visible_candidate_explanations(
                    explanations,
                    limit=plan.constraints.max_candidates,
                    include_developing=plan.constraints.include_developing_evidence,
                )
                enrichment_candidates = report_explanations
            await self._finish_iteration_step(
                retrieval_step_id,
                {"snapshot_count": len(snapshots), "context_compaction": "deterministic_top_n"},
            )
            await session.commit()

        company_packets = []
        if should_enrich_research(plan, enrichment_candidates, self.settings):
            web_step_id = await self._start_iteration_step(
                state,
                "research:web_company_fanout",
                {
                    "requested_companies": len(enrichment_candidates),
                    "concurrency": min(
                        max(self.settings.research_web_concurrency, 1),
                        max(len(enrichment_candidates), 1),
                    ),
                },
            )
            company_packets = await self.company_research.research_many(
                enrichment_candidates,
                query=plan.query,
                horizon=plan.research_horizon,
            )
            await self._finish_iteration_step(
                web_step_id,
                {
                    "completed_companies": sum(
                        packet.status in {"complete", "partial"} for packet in company_packets
                    ),
                    "failed_companies": sum(
                        packet.status in {"failed", "timeout", "unavailable"}
                        for packet in company_packets
                    ),
                    "source_count": sum(len(packet.evidence) for packet in company_packets),
                },
            )

        usable_company_research = {
            packet.ticker: packet for packet in company_packets if packet.evidence
        }
        if plan.task_type == "stock_lookup":
            response = format_signal(
                report_explanations,
                ticker,
                max_evidence_items=self.settings.research_report_max_evidence_items,
                company_research=usable_company_research,
            )
        else:
            response = format_candidates(
                report_explanations,
                max_evidence_items=self.settings.research_report_max_evidence_items,
                company_research=usable_company_research,
            )

        return {
            "response": response,
            "metadata": {
                "capability": "market_research",
                "plan": plan.task_type,
                "sectors": plan.entities.sectors,
                "mention_count": mention_count,
                "sector_context_count": sector_context_count,
                "signal_evidence_backfill_count": backfilled_count,
                "signal_count": signal_count,
                "confident_signal_count": confident_signal_count,
                "pruned_count": pruned_count,
                "company_research_company_count": len(usable_company_research),
                "company_research_source_count": sum(
                    len(packet.evidence) for packet in company_packets
                ),
                "company_research_status_counts": _status_counts(company_packets),
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


async def _persist_validated_evidence(
    repository: MarketSignalRepository,
    explanations: list[CandidateExplanation],
) -> None:
    for explanation in explanations:
        if explanation.snapshot_id is None:
            continue
        await repository.update_snapshot_evidence(explanation.snapshot_id, explanation.evidence)


def _status_counts(packets: list[CompanyResearchPacket]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for packet in packets:
        counts[packet.status] = counts.get(packet.status, 0) + 1
    return counts
