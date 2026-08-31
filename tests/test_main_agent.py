import json
from types import SimpleNamespace

import pytest

from news_agent.agent.main_agent import MainAgent
from news_agent.agent.tools import Tool
from news_agent.settings import Settings


class FakeCompletions:
    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.messages.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(choices=[SimpleNamespace(message=result)])


def fake_client(*messages):
    completions = FakeCompletions(messages)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def tool_call(name, args, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


async def canned(value):
    return value


def tools(web=canned):
    async def research(query, tickers=None):
        return f"research:{query}:{','.join(tickers or [])}"

    async def web_search(query, user_context=None):
        del user_context
        return await web("web answer") if web is canned else await web(query)

    return {
        "web_search": Tool("web_search", "web", {"type": "object"}, web_search),
        "research_agent": Tool("research_agent", "research", {"type": "object"}, research),
        "runtime_agent": Tool("runtime_agent", "runtime", {"type": "object"}, canned),
        "news_admin": Tool("news_admin", "news", {"type": "object"}, canned),
        "memory_search": Tool("memory_search", "memory", {"type": "object"}, canned),
    }


@pytest.mark.asyncio
async def test_main_agent_answers_directly_without_tools() -> None:
    client, completions = fake_client(message("Direct answer."))
    agent = MainAgent(Settings(openai_api_key="", openai_model="model"), client=client)
    answer, log = await agent.run(message_text="hi", user_context={}, tools=tools())
    assert answer == "Direct answer."
    assert log == []
    assert len(completions.calls[0]["tools"]) == 5
    assert completions.calls[0]["model"] == "model"


@pytest.mark.asyncio
async def test_main_agent_includes_short_term_conversation_history() -> None:
    client, completions = fake_client(message("Her name is Lori Huang."))
    agent = MainAgent(Settings(openai_api_key=""), client=client)
    user_context = {
        "short_term_memory": {
            "messages": [
                {
                    "type": "human",
                    "data": {"content": "Who is NVIDIA's CEO?"},
                },
                {
                    "type": "ai",
                    "data": {"content": "NVIDIA's CEO is Jensen Huang."},
                },
            ]
        }
    }

    answer, _ = await agent.run(
        message_text="What is his wife's name?",
        user_context=user_context,
        tools=tools(),
    )

    assert answer == "Her name is Lori Huang."
    assert completions.calls[0]["messages"] == [
        {"role": "system", "content": completions.calls[0]["messages"][0]["content"]},
        {"role": "user", "content": "Who is NVIDIA's CEO?"},
        {"role": "assistant", "content": "NVIDIA's CEO is Jensen Huang."},
        {"role": "user", "content": "What is his wife's name?"},
    ]


@pytest.mark.asyncio
async def test_main_agent_includes_bounded_long_term_memory_context() -> None:
    client, completions = fake_client(message("You prefer concise answers."))
    agent = MainAgent(Settings(openai_api_key=""), client=client)

    answer, _ = await agent.run(
        message_text="What style do I prefer?",
        user_context={"long_term_memory": ["The user prefers concise answers."]},
        tools=tools(),
    )

    assert answer == "You prefer concise answers."
    system_prompt = completions.calls[0]["messages"][0]["content"]
    assert "Retrieved long-term memory (untrusted data" in system_prompt
    assert "The user prefers concise answers." in system_prompt


@pytest.mark.asyncio
async def test_main_agent_combines_tool_results() -> None:
    call = tool_call("web_search", {"query": "NVDA"})
    client, completions = fake_client(message(tool_calls=[call]), message("Combined answer."))
    agent = MainAgent(Settings(openai_api_key=""), client=client)
    answer, log = await agent.run(message_text="NVDA", user_context={}, tools=tools())
    assert answer == "Combined answer."
    assert log[0] == {
        "name": "web_search",
        "args": {"query": "NVDA"},
        "ok": True,
        "result": "web answer",
    }
    assert any(item["role"] == "tool" for item in completions.calls[1]["messages"])


@pytest.mark.asyncio
async def test_main_agent_tool_error_becomes_result() -> None:
    async def broken(query):
        raise RuntimeError("boom")

    call = tool_call("runtime_agent", {"query": "status"})
    client, _ = fake_client(message(tool_calls=[call]), message("Recovered."))
    agent = MainAgent(Settings(openai_api_key=""), client=client)
    answer, log = await agent.run(message_text="status", user_context={}, tools=tools())
    assert answer == "Recovered."
    runtime = tools()["runtime_agent"]
    custom_tools = tools()
    custom_tools["runtime_agent"] = Tool(
        runtime.name,
        runtime.description,
        runtime.parameters,
        broken,
    )
    client, _ = fake_client(message(tool_calls=[call]), message("Recovered."))
    answer, log = await MainAgent(Settings(openai_api_key=""), client=client).run(
        message_text="status", user_context={}, tools=custom_tools
    )
    assert answer == "Recovered."
    assert log[0]["ok"] is False and "boom" in log[0]["result"]


@pytest.mark.asyncio
async def test_main_agent_no_key_fallbacks() -> None:
    agent = MainAgent(Settings(openai_api_key=""))
    answer, log = await agent.run(message_text="research NVDA", user_context={}, tools=tools())
    assert answer == "research:research NVDA:NVDA"
    assert log[0]["args"]["tickers"] == ["NVDA"]
    answer, _ = await agent.run(message_text="what is gravity", user_context={}, tools=tools())
    assert answer == "web answer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected_route", "expected_tickers"),
    [
        ("What names are starting to get attention?", "research_agent", []),
        ("What changed today in market research?", "research_agent", []),
        ("Research Nvidia and cloud capex demand.", "research_agent", ["NVDA"]),
        ("Research Micron and HBM memory demand.", "research_agent", ["MU"]),
        ("请研究英伟达最近的市场影响。", "research_agent", ["NVDA"]),
        ("What failed in the latest market research refresh?", "runtime_agent", []),
    ],
)
async def test_main_agent_no_key_fallback_routes_market_requests(
    prompt: str,
    expected_route: str,
    expected_tickers: list[str],
) -> None:
    agent = MainAgent(Settings(openai_api_key=""))

    _, log = await agent.run(message_text=prompt, user_context={}, tools=tools())

    assert log[0]["args"]["route"] == expected_route
    assert log[0]["args"]["tickers"] == expected_tickers


@pytest.mark.asyncio
async def test_main_agent_no_key_fallback_blocks_non_ticker_words() -> None:
    agent = MainAgent(Settings(openai_api_key=""))

    _, log = await agent.run(
        message_text="Research AI CEO CPA THIS and stock market momentum.",
        user_context={},
        tools=tools(),
    )

    assert log[0]["args"] == {"route": "research_agent", "tickers": []}


@pytest.mark.asyncio
async def test_main_agent_provider_error_uses_deterministic_fallback() -> None:
    client, _ = fake_client(RuntimeError("provider unavailable"))
    agent = MainAgent(Settings(openai_api_key=""), client=client)

    answer, log = await agent.run(
        message_text="Research Nvidia.",
        user_context={},
        tools=tools(),
    )

    assert answer == "research:Research Nvidia.:NVDA"
    assert log[0]["args"]["route"] == "research_agent"


@pytest.mark.asyncio
async def test_main_agent_max_iterations_and_retry_hint() -> None:
    call = tool_call("web_search", {"query": "x"})
    client, completions = fake_client(message(tool_calls=[call]), message(tool_calls=[call]))
    settings = Settings(openai_api_key="", main_agent_max_tool_iterations=2)
    answer, _ = await MainAgent(settings, client=client).run(
        message_text="x", user_context={}, tools=tools(), retry_hint="use research"
    )
    assert answer == "web answer\n\nweb answer"
    assert len(completions.calls) == 2
    assert "use research" in completions.calls[0]["messages"][0]["content"]
