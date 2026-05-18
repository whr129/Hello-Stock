from news_agent.research.planner import PlannerAgent


def test_research_command_produces_deep_research_plan() -> None:
    plan = PlannerAgent().plan(
        command="/research",
        args=[],
        message_text="/research",
    )

    assert plan.task_type == "deep_research"
    assert plan.research_horizon == "30d"
    assert "analysis" in plan.agents_to_run


def test_candidates_command_produces_candidate_ranking_plan() -> None:
    plan = PlannerAgent().plan(
        command="/candidates",
        args=[],
        message_text="/candidates",
    )

    assert plan.task_type == "candidate_ranking"
    assert plan.constraints.max_candidates == 5


def test_signals_command_extracts_ticker() -> None:
    plan = PlannerAgent().plan(
        command="/signals",
        args=["mu"],
        message_text="/signals mu",
    )

    assert plan.task_type == "stock_lookup"
    assert plan.entities.tickers == ["MU"]
