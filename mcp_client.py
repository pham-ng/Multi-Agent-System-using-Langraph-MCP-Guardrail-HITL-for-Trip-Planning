import os
import shutil
import sys
import asyncio
from pathlib import Path

import certifi
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient
from reliability import CircuitBreaker, async_call_with_retry

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UV_CACHE_DIR = Path(os.getenv("UV_CACHE_DIR", str(BASE_DIR / ".uv-cache")))
UV_TOOL_DIR = Path(os.getenv("UV_TOOL_DIR", str(BASE_DIR / ".uv-tools")))
UV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
UV_TOOL_DIR.mkdir(parents=True, exist_ok=True)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AVIATION_STACK_API_KEY = os.getenv("AVIATIONSTACK_API_KEY")
OPEN_WEATHER_API_KEY = os.getenv("OPEN_WEATHER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

llm = None
_breakers = {
    "tavily": CircuitBreaker("tavily"),
    "aviationstack": CircuitBreaker("aviationstack_mcp"),
    "weather": CircuitBreaker("weather"),
}

TAVILY_SERVER = {
    "transport": "streamable_http",
    "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY}",
}
AVIATION_SERVER = {
    "transport": "stdio",
    "command": os.getenv("AVIATIONSTACK_MCP_COMMAND", shutil.which("uvx") or "uvx"),
    "args": ["aviationstack-mcp"],
    "env": {
        "AVIATION_STACK_API_KEY": AVIATION_STACK_API_KEY,
        "AVIATIONSTACK_API_KEY": AVIATION_STACK_API_KEY,
        "UV_CACHE_DIR": str(UV_CACHE_DIR),
        "UV_TOOL_DIR": str(UV_TOOL_DIR),
    },
}
WEATHER_SERVER = {
    "transport": "stdio",
    "command": sys.executable,
    "args": [str(BASE_DIR / "custom_weather_mcp_server.py")],
    "env": {"OPEN_WEATHER_API_KEY": OPEN_WEATHER_API_KEY},
}

# Isolate MCP servers so one unavailable integration does not break the others.
tavily_client = MultiServerMCPClient({"tavily": TAVILY_SERVER})
aviation_client = MultiServerMCPClient({"aviationstack": AVIATION_SERVER})
weather_client = MultiServerMCPClient({"weather": WEATHER_SERVER})


async def _invoke_tool(client, tool_name: str, args: dict):
    async def invoke_once():
        tools = await client.get_tools()
        tool = next((candidate for candidate in tools if candidate.name == tool_name), None)
        if tool is None:
            available = ", ".join(candidate.name for candidate in tools)
            raise RuntimeError(f"MCP tool '{tool_name}' was not found. Available: {available}")
        return await asyncio.wait_for(tool.ainvoke(args), timeout=float(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "45")))

    if client is tavily_client:
        breaker = _breakers["tavily"]
    elif client is aviation_client:
        breaker = _breakers["aviationstack"]
    else:
        breaker = _breakers["weather"]
    return await async_call_with_retry(invoke_once, breaker=breaker, attempts=3)


async def get_all_tools():
    print("\nAvailable MCP tools")
    for name, client in (("tavily", tavily_client), ("aviationstack", aviation_client), ("weather", weather_client)):
        try:
            tools = await client.get_tools()
            print(f"{name}: {', '.join(tool.name for tool in tools)}")
        except Exception as exc:
            print(f"{name}: unavailable ({exc})")


async def tavily_mcp_search(query: str):
    return await _invoke_tool(tavily_client, "tavily_search", {"query": query})


async def aviation_mcp_call(tool_name: str, tool_args: dict | None = None):
    return await _invoke_tool(aviation_client, tool_name, tool_args or {})


async def weather_mcp_search(city: str):
    return await _invoke_tool(weather_client, "get_current_weather", {"city": city})


async def forecast_mcp_search(city: str):
    return await _invoke_tool(weather_client, "get_forecast", {"city": city})


def extract_destination(query: str) -> str:
    global llm
    if llm is None:
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is missing")
        llm = ChatGroq(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), api_key=GROQ_API_KEY)
    response = llm.invoke(
        f"Extract only the destination city or country from this travel request. Return only its name.\n{query}"
    )
    return str(response.content).strip()
