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
        text = state.get("message_text", "")
        command = text.split()[0] if text.startswith("/") else ""
        return {**state, "command": command, "args": [], "intent": "general_chat"}

    async def route_request(self, state):
        self.calls.append("route_request")
        agent = {"/sources": "news", "/runtime": "runtime", "/research": "research"}.get(
            state.get("command")
        )
        return {
            **state,
            "route": {
                "agents": [agent] if agent else [],
                "capabilities": [],
                "fallback_response": "help" if state.get("command") and agent is None else None,
            },
        }

    async def run_news_agent(self, state):
        self.calls.append("run_news_agent")
        return self._response(state, "news", "news response")

    async def run_runtime_agent(self, state):
        self.calls.append("run_runtime_agent")
        return self._response(state, "runtime", "runtime response")

    async def run_research_agent(self, state):
        self.calls.append("run_research_agent")
        return self._response(state, "research", "research response")

    async def run_main_agent(self, state):
        self.calls.append("run_main_agent")
        return self._response(state, "main_agent", "main agent response")

    @staticmethod
    def _response(state, name, response):
        return {
            **state,
            f"{name}_result": {"response": response},
            "final_response": response,
            "response": response,
        }

    async def guardrail_check(self, state):
        self.calls.append("guardrail_check")
        return state

    async def reflect_result(self, state):
        self.calls.append("reflect_result")
        return state

    async def persist_session(self, state):
        self.calls.append("persist_session")
        return {**state, "metadata": {**state.get("metadata", {}), "calls": self.calls}}


@pytest.mark.asyncio
async def test_chat_graph_runs_single_news_subagent(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", DummySupervisorNodes)
    result = await build_chat_graph(None, Settings(openai_api_key="")).ainvoke(
        {"message_text": "/sources"}
    )
    calls = result["metadata"]["calls"]
    assert result["response"] == "news response"
    assert "run_news_agent" in calls
    assert "run_runtime_agent" not in calls
    assert "run_main_agent" not in calls
    assert calls[-1] == "persist_session"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["who won the world series last year", "/brief"],
)
async def test_chat_graph_free_text_and_unknown_commands_use_main_agent(
    monkeypatch, message
) -> None:
    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", DummySupervisorNodes)
    result = await build_chat_graph(None, Settings(openai_api_key="")).ainvoke(
        {"message_text": message}
    )
    assert result["response"] == "main agent response"
    assert "run_main_agent" in result["metadata"]["calls"]


@pytest.mark.asyncio
async def test_chat_graph_runs_runtime_agent_when_requested(monkeypatch) -> None:
    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", DummySupervisorNodes)
    result = await build_chat_graph(None, Settings(openai_api_key="")).ainvoke(
        {"message_text": "/runtime"}
    )
    assert result["response"] == "runtime response"


@pytest.mark.asyncio
async def test_chat_graph_reflection_retries_with_corrected_agent(monkeypatch) -> None:
    class RetryNodes(DummySupervisorNodes):
        async def reflect_result(self, state):
            self.calls.append("reflect_result")
            metadata = dict(state.get("metadata", {}))
            metadata.pop("reflection_retry", None)
            if state.get("reflection_attempts", 0) == 0:
                metadata["reflection_retry"] = True
                return {
                    **state,
                    "command": "/research",
                    "reflection_attempts": 1,
                    "reflection_decision": {
                        "corrected_agent": "research",
                        "retry_hint": "use NVDA",
                    },
                    "metadata": metadata,
                    "final_response": "",
                    "response": "",
                }
            return {**state, "metadata": metadata}

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", RetryNodes)
    result = await build_chat_graph(None, Settings(openai_api_key="")).ainvoke(
        {"message_text": "what is NVDA doing?"}
    )
    calls = result["metadata"]["calls"]
    assert result["response"] == "research response"
    assert calls.count("route_request") == 1
    assert "run_main_agent" in calls and "run_research_agent" in calls


@pytest.mark.asyncio
async def test_chat_graph_reflection_exhaustion_persists_note(monkeypatch) -> None:
    class ExhaustedNodes(DummySupervisorNodes):
        async def reflect_result(self, state):
            self.calls.append("reflect_result")
            note = "Note: I could not confidently repair this answer after a reflection retry."
            response = f"{state.get('response', '')}\n\n{note}"
            return {
                **state,
                "response": response,
                "final_response": response,
                "reflection_exhausted": True,
            }

    monkeypatch.setattr("news_agent.app.supervisor.SupervisorNodes", ExhaustedNodes)
    result = await build_chat_graph(None, Settings(openai_api_key="")).ainvoke(
        {"message_text": "bad route"}
    )
    assert "could not confidently repair" in result["response"]
    assert result["reflection_exhausted"] is True
