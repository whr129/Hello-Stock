import pytest

from news_agent.graph.chat_graph import build_chat_graph
from news_agent.settings import Settings


class DummySupervisorNodes:
    def __init__(self, session_factory, settings) -> None:
        self.calls: list[str] = []

    def traced(self, step_name, func, **kwargs):
        del step_name, kwargs
        return func

    async def load_user_context(self, state):
        self.calls.append("load_user_context")
        return state

    async def classify_request(self, state):
        self.calls.append("classify_request")
        return state

    async def route_request(self, state):
        self.calls.append("route_request")
        requested_agents = (
            ["news", "market"] if "mixed" in state.get("message_text", "") else ["news"]
        )
        return {
            **state,
            "route": {"agents": requested_agents, "capabilities": []},
            "pending_agents": requested_agents,
            "completed_agents": [],
        }

    async def run_news_agent(self, state):
        self.calls.append("run_news_agent")
        pending = [agent for agent in state.get("pending_agents", []) if agent != "news"]
        completed = list(state.get("completed_agents", [])) + ["news"]
        return {
            **state,
            "news_result": {"response": "news response"},
            "pending_agents": pending,
            "completed_agents": completed,
        }

    async def run_market_agent(self, state):
        self.calls.append("run_market_agent")
        pending = [agent for agent in state.get("pending_agents", []) if agent != "market"]
        completed = list(state.get("completed_agents", [])) + ["market"]
        return {
            **state,
            "market_result": {"response": "market response"},
            "pending_agents": pending,
            "completed_agents": completed,
        }

    async def run_general_search(self, state):
        self.calls.append("run_general_search")
        return {
            **state,
            "search_result": {"response": "search response"},
        }

    async def run_runtime_agent(self, state):
        self.calls.append("run_runtime_agent")
        pending = [agent for agent in state.get("pending_agents", []) if agent != "runtime"]
        completed = list(state.get("completed_agents", [])) + ["runtime"]
        return {
            **state,
            "runtime_result": {"response": "runtime response"},
            "pending_agents": pending,
            "completed_agents": completed,
        }

    async def merge_agent_outputs(self, state):
        self.calls.append("merge_agent_outputs")
        parts = []
        if state.get("news_result"):
            parts.append(state["news_result"]["response"])
        if state.get("market_result"):
            parts.append(state["market_result"]["response"])
        if state.get("runtime_result"):
            parts.append(state["runtime_result"]["response"])
        if state.get("search_result"):
            parts.append(state["search_result"]["response"])
        return {**state, "final_response": "\n\n".join(parts), "response": "\n\n".join(parts)}

    async def guardrail_check(self, state):
        self.calls.append("guardrail_check")
        return state

    async def reflect_result(self, state):
        self.calls.append("reflect_result")
        return state

    async def persist_session(self, state):
        self.calls.append("persist_session")
        return {**state, "metadata": {"calls": self.calls}}


@pytest.mark.asyncio
async def test_chat_graph_runs_single_news_subagent(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", DummySupervisorNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "news only"})
    calls = result["metadata"]["calls"]

    assert result["response"] == "news response"
    assert "run_news_agent" in calls
    assert "run_market_agent" not in calls
    assert calls[-1] == "persist_session"


@pytest.mark.asyncio
async def test_chat_graph_runs_both_subagents_for_mixed_route(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", DummySupervisorNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "mixed route"})
    calls = result["metadata"]["calls"]

    assert result["response"] == "news response\n\nmarket response"
    assert "run_news_agent" in calls
    assert "run_market_agent" in calls


@pytest.mark.asyncio
async def test_chat_graph_runs_general_search_when_no_agents(monkeypatch) -> None:
    class GeneralSearchNodes(DummySupervisorNodes):
        async def route_request(self, state):
            self.calls.append("route_request")
            return {
                **state,
                "route": {"agents": [], "capabilities": ["general_search"]},
                "pending_agents": [],
                "completed_agents": [],
            }

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", GeneralSearchNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "who won the world series last year"})
    calls = result["metadata"]["calls"]

    assert result["response"] == "search response"
    assert "run_general_search" in calls


@pytest.mark.asyncio
async def test_chat_graph_runs_runtime_agent_when_requested(monkeypatch) -> None:
    class RuntimeNodes(DummySupervisorNodes):
        async def route_request(self, state):
            self.calls.append("route_request")
            return {
                **state,
                "route": {"agents": ["runtime"], "capabilities": ["runtime_inspection"]},
                "pending_agents": ["runtime"],
                "completed_agents": [],
            }

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", RuntimeNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "what happened in the last refresh"})
    calls = result["metadata"]["calls"]

    assert result["response"] == "runtime response"
    assert "run_runtime_agent" in calls


@pytest.mark.asyncio
async def test_chat_graph_reflection_retries_with_corrected_route(monkeypatch) -> None:
    class ReflectionRetryNodes(DummySupervisorNodes):
        async def classify_request(self, state):
            self.calls.append("classify_request")
            return {**state, "intent": "general_chat", "args": []}

        async def route_request(self, state):
            self.calls.append("route_request")
            if state.get("intent") == "stocks":
                return {
                    **state,
                    "route": {
                        "agents": ["market"],
                        "capabilities": ["market_snapshot", "technical_analysis"],
                    },
                    "pending_agents": ["market"],
                    "completed_agents": [],
                }
            return {
                **state,
                "route": {"agents": [], "capabilities": ["general_search"]},
                "pending_agents": [],
                "completed_agents": [],
            }

        async def reflect_result(self, state):
            self.calls.append("reflect_result")
            metadata = dict(state.get("metadata", {}))
            metadata.pop("reflection_retry", None)
            if state.get("reflection_attempts", 0) == 0:
                metadata["reflection_retry"] = True
                return {
                    **state,
                    "intent": "stocks",
                    "args": ["AAPL"],
                    "requested_symbols": ["AAPL"],
                    "route": {},
                    "pending_agents": [],
                    "completed_agents": [],
                    "search_result": {},
                    "final_response": "",
                    "response": "",
                    "metadata": metadata,
                    "reflection_attempts": 1,
                }
            return {**state, "metadata": metadata}

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", ReflectionRetryNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "what is Apple doing today?"})
    calls = result["metadata"]["calls"]

    assert result["response"] == "market response"
    assert calls.count("route_request") == 2
    assert "run_general_search" in calls
    assert "run_market_agent" in calls


@pytest.mark.asyncio
async def test_chat_graph_reflection_exhaustion_persists_note(monkeypatch) -> None:
    class ReflectionExhaustedNodes(DummySupervisorNodes):
        note = (
            "Note: I could not confidently repair this answer after a reflection retry. "
            "You may want to rephrase or use a direct command such as /research, /stocks, "
            "or /runtime."
        )

        async def route_request(self, state):
            self.calls.append("route_request")
            return {
                **state,
                "route": {"agents": [], "capabilities": ["general_search"]},
                "pending_agents": [],
                "completed_agents": [],
            }

        async def reflect_result(self, state):
            self.calls.append("reflect_result")
            return {
                **state,
                "final_response": (
                    state.get("final_response", "") + "\n\n" + self.note
                ).strip(),
                "response": (state.get("response", "") + "\n\n" + self.note).strip(),
                "reflection_exhausted": True,
            }

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", ReflectionExhaustedNodes)

    graph = build_chat_graph(session_factory=None, settings=Settings(openai_api_key=""))
    result = await graph.ainvoke({"message_text": "bad route"})

    assert "could not confidently repair" in result["response"]
    assert result["reflection_exhausted"] is True
