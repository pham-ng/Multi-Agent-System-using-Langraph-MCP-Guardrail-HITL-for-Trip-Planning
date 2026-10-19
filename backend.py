import asyncio
import json
import operator
import os
import re
import threading
import uuid
from typing import Annotated, Any, TypedDict

import certifi
import psycopg
import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row

from mcp_client import extract_destination, forecast_mcp_search, tavily_mcp_search, weather_mcp_search

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to .env.")

llm = ChatGroq(model="openai/gpt-oss-120b", api_key=GROQ_API_KEY)


class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str
    flight_results: str
    hotel_results: str
    weather_result: str
    budget_results: str
    itinerary: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str
    llm_calls: int


def _clip(value: Any, max_chars: int = 1800) -> str:
    text = str(value or "")
    return text if len(text) <= max_chars else text[:max_chars] + "\n...(truncated)"


def _mcp_result_text(result: Any) -> str:
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text", result[0]))
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _mcp_call_failed(result: Any) -> bool:
    try:
        parsed = json.loads(_mcp_result_text(result))
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and (parsed.get("ok") is False or "error" in parsed)


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


def _llm_text(system_prompt: str, user_prompt: str) -> str:
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return str(response.content)


def _json_from_llm(text: str) -> dict[str, Any]:
    cleaned = text.replace("```json", "").replace("```", "")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model did not return a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("The model returned a JSON value, not an object")
    return parsed


AGENTS = ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"]


def supervisor_agent(state: TravelState):
    query = state["user_query"]
    calls = state.get("llm_calls", 0)
    guardrail_prompt = f"""
Decide whether this request is valid for a travel-planning assistant.
Allow destinations, flights, hotels, weather, budgets, visas, transport,
sightseeing, food and packing. Block clearly unrelated, harmful or illegal
requests. Missing trip details are allowed.
Return JSON only: {{"allowed": true, "reason": ""}}
User request: {query}
"""
    try:
        guard = _json_from_llm(_llm_text("You are a strict travel input guardrail. Return JSON only.", guardrail_prompt))
        allowed = bool(guard.get("allowed", False))
        reason = str(guard.get("reason", "")).strip()
        calls += 1
    except Exception as exc:
        allowed = False
        reason = f"Guardrail could not validate the request: {exc}"

    if not allowed:
        reason = reason or "This assistant only supports travel planning."
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=reason)],
            "llm_calls": calls,
        }

    supervisor_prompt = f"""
Route this valid travel request. Return JSON only using:
{{"selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
"trip_constraints": {{"destination":"", "origin":"", "duration":"", "budget":"", "travel_style":"", "special_preferences":[]}},
"reasoning":""}}
User request: {query}
"""
    try:
        parsed = _json_from_llm(_llm_text("You route work to travel specialist agents. Return JSON only.", supervisor_prompt))
        selected = [name for name in AGENTS if name in parsed.get("selected_agents", [])]
        if "itinerary_agent" not in selected:
            selected.append("itinerary_agent")
        constraints = _empty_constraints()
        raw_constraints = parsed.get("trip_constraints", {})
        if isinstance(raw_constraints, dict):
            constraints.update(raw_constraints)
        reasoning = str(parsed.get("reasoning", "")).strip()
        calls += 1
    except Exception as exc:
        selected = AGENTS.copy()
        constraints = _empty_constraints()
        reasoning = f"Supervisor fallback: {exc}"

    return {
        "guardrail_allowed": True,
        "guardrail_reason": "Request accepted.",
        "selected_agents": selected,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": calls,
    }


def supervisor_route(state: TravelState) -> str:
    if not state.get("guardrail_allowed", False):
        return "guardrail_block"
    selected = [name for name in AGENTS if name in state.get("selected_agents", [])]
    return selected[0] if selected else "itinerary_agent"


def next_agent(state: TravelState, current: str) -> str:
    selected = [name for name in AGENTS if name in state.get("selected_agents", [])]
    if "itinerary_agent" not in selected:
        selected.append("itinerary_agent")
    try:
        next_index = selected.index(current) + 1
    except ValueError:
        next_index = 0
    return selected[next_index] if next_index < len(selected) else "human_approval"


def guardrail_block_agent(state: TravelState):
    reason = state.get("guardrail_reason") or "This request was blocked by the travel guardrail."
    return {"final_response": reason, "messages": [AIMessage(content=reason)]}


CITY_CODES = {
    "hanoi": "HAN", "ha noi": "HAN", "ho chi minh": "SGN", "saigon": "SGN",
    "da nang": "DAD", "tokyo": "NRT", "osaka": "KIX", "singapore": "SIN",
    "bangkok": "BKK", "kuala lumpur": "KUL", "seoul": "ICN", "paris": "CDG",
    "london": "LHR", "new york": "JFK", "los angeles": "LAX", "sydney": "SYD",
}


def _flight_codes(query: str) -> tuple[str | None, str | None]:
    normalized = re.sub(r"[^a-z0-9 ]", " ", query.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    match = re.search(r"from (.+?) to (.+?)(?: with| for| next| on|$)", normalized)
    if not match:
        match = re.search(r"(.+?) to (.+?)(?: with| for| next| on|$)", normalized)
    origin = os.getenv("DEFAULT_ORIGIN_IATA", "SGN")
    destination = None
    if match:
        origin_text, destination_text = match.groups()
        for name, code in sorted(CITY_CODES.items(), key=lambda item: -len(item[0])):
            if name in origin_text:
                origin = code
            if name in destination_text:
                destination = code
    return origin, destination


def search_flights(query: str, limit: int = 10) -> str:
    api_key = os.getenv("AVIATIONSTACK_API_KEY")
    if not api_key:
        return "Flight information unavailable: AVIATIONSTACK_API_KEY is not configured."
    origin, destination = _flight_codes(query)
    params = {"access_key": api_key, "limit": min(limit, 100)}
    if origin:
        params["dep_iata"] = origin
    if destination:
        params["arr_iata"] = destination
    try:
        response = requests.get("https://api.aviationstack.com/v1/flights", params=params, timeout=30)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Flight information unavailable: {exc}"
    if "error" in data:
        error = data["error"]
        return f"Flight API error: {error.get('message', error)}"
    flights = data.get("data", [])
    if not flights:
        return f"No live flight data found for {origin or '?'} to {destination or '?'}; ticket prices are unavailable from this API."
    rows = []
    for flight in flights[:limit]:
        departure = flight.get("departure", {})
        arrival = flight.get("arrival", {})
        airline = (flight.get("airline") or {}).get("name", "Unknown")
        number = (flight.get("flight") or {}).get("iata", "Unknown")
        rows.append(
            f"Airline: {airline}\nFlight: {number}\nStatus: {flight.get('flight_status', 'Unknown')}\n"
            f"Departure: {departure.get('airport', 'Unknown')} ({departure.get('iata', origin or '?')}) at {departure.get('scheduled', 'Unknown')}\n"
            f"Arrival: {arrival.get('airport', 'Unknown')} ({arrival.get('iata', destination or '?')}) at {arrival.get('scheduled', 'Unknown')}"
        )
    return f"Live flights from {origin or '?'} to {destination or '?'}\n\n" + "\n\n---\n\n".join(rows)


def flight_agent(state: TravelState):
    return {
        "flight_results": search_flights(state["user_query"]),
        "messages": [AIMessage(content="Flight results fetched.")],
    }


def hotel_agent(state: TravelState):
    try:
        result = asyncio.run(tavily_mcp_search(f"Best hotels for {state['user_query']}"))
        hotel_results = _mcp_result_text(result)
        if _mcp_call_failed(result):
            hotel_results = f"Hotel information unavailable: {hotel_results}"
    except Exception as exc:
        hotel_results = f"Hotel information unavailable: {exc}"
    return {"hotel_results": hotel_results, "messages": [AIMessage(content="Hotel information fetched.")]}


def weather_agent(state: TravelState):
    try:
        city = extract_destination(state["user_query"])
        current = asyncio.run(weather_mcp_search(city))
        forecast = asyncio.run(forecast_mcp_search(city))
        weather = f"City: {city}\nCurrent weather:\n{current}\nForecast:\n{forecast}"
    except Exception as exc:
        weather = f"Weather information unavailable: {exc}"
    return {"weather_result": weather, "messages": [AIMessage(content="Weather information fetched.")]}


def budget_agent(state: TravelState):
    prompt = f"""
Analyze the realistic budget for this trip. Clearly label estimates and never
invent live prices. Return categories, risks, saving suggestions and feasibility.
User request: {state['user_query']}
Constraints: {_clip(state.get('trip_constraints', {}))}
Flights: {_clip(state.get('flight_results', ''), 900)}
Hotels: {_clip(state.get('hotel_results', ''), 900)}
"""
    response = llm.invoke([
        SystemMessage(content="You are a practical travel budget analyst."),
        HumanMessage(content=prompt),
    ])
    return {
        "budget_results": str(response.content),
        "messages": [AIMessage(content="Budget analysis generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def itinerary_agent(state: TravelState):
    prompt = f"""
Create a practical draft itinerary for human review.
User request: {state['user_query']}
Constraints: {_clip(state.get('trip_constraints', {}))}
Flights: {_clip(state.get('flight_results', ''))}
Hotels: {_clip(state.get('hotel_results', ''))}
Weather: {_clip(state.get('weather_result', ''))}
Budget: {_clip(state.get('budget_results', ''))}
Do not claim unavailable prices or facts as live data.
"""
    response = llm.invoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt),
    ])
    return {
        "itinerary": str(response.content),
        "approval_request": "Review the draft itinerary. Approve it, or reject it with feedback for revision.",
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def human_approval_agent(state: TravelState):
    review = interrupt({
        "question": "Do you approve this itinerary?",
        "draft_itinerary": state.get("itinerary", ""),
        "approval_request": state.get("approval_request", ""),
        "expected_response": {"approved": True, "feedback": "Optional revision feedback"},
    })
    if not isinstance(review, dict):
        review = {"approved": False, "feedback": "Invalid approval response."}
    return {
        "approved": bool(review.get("approved", False)),
        "human_feedback": str(review.get("feedback", "")).strip(),
        "messages": [AIMessage(content="Human review completed.")],
    }


def final_agent(state: TravelState):
    review = "The user approved the draft." if state.get("approved") else (
        "Revise the draft using this feedback: " + (state.get("human_feedback") or "Improve the draft carefully.")
    )
    prompt = f"""
Write the final travel plan in Markdown with sections: Trip Summary, Flight
Information, Hotel Suggestions, Weather, Day-by-day Itinerary, Estimated
Budget and Final Recommendations.
Review instruction: {review}
User request: {state['user_query']}
Flights: {_clip(state.get('flight_results', ''), 1200)}
Hotels: {_clip(state.get('hotel_results', ''), 1200)}
Weather: {_clip(state.get('weather_result', ''), 1200)}
Budget: {_clip(state.get('budget_results', ''), 1200)}
Draft: {_clip(state.get('itinerary', ''), 2600)}
Do not fabricate prices, schedules or weather. Mark estimates clearly.
"""
    response = llm.invoke([
        SystemMessage(content="You are a professional travel planning assistant."),
        HumanMessage(content=prompt),
    ])
    return {
        "final_response": str(response.content),
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


graph = StateGraph(TravelState)
for name, node in {
    "supervisor": supervisor_agent,
    "guardrail_block": guardrail_block_agent,
    "flight_agent": flight_agent,
    "hotel_agent": hotel_agent,
    "weather_agent": weather_agent,
    "budget_agent": budget_agent,
    "itinerary_agent": itinerary_agent,
    "human_approval": human_approval_agent,
    "final_agent": final_agent,
}.items():
    graph.add_node(name, node)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", supervisor_route, {
    "guardrail_block": "guardrail_block",
    **{name: name for name in AGENTS},
})
graph.add_edge("guardrail_block", END)
for agent_name in AGENTS:
    graph.add_conditional_edges(
        agent_name,
        lambda state, current=agent_name: next_agent(state, current),
        {**{name: name for name in AGENTS}, "human_approval": "human_approval"},
    )
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)


_graph = None
_connection = None
_graph_lock = threading.Lock()


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is missing.")
    if "sslmode" not in database_url:
        database_url += "&sslmode=require" if "?" in database_url else "?sslmode=require"
    return database_url


def get_travel_graph():
    global _graph, _connection
    if _graph is not None:
        return _graph
    with _graph_lock:
        if _graph is None:
            _connection = psycopg.connect(get_database_url(), autocommit=True, row_factory=dict_row)
            checkpointer = PostgresSaver(_connection)
            checkpointer.setup()
            _graph = graph.compile(checkpointer=checkpointer)
    return _graph


def _initial_state(user_input: str) -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=user_input)],
        "user_query": user_input,
        "guardrail_allowed": False,
        "guardrail_reason": "",
        "selected_agents": [],
        "trip_constraints": _empty_constraints(),
        "supervisor_reasoning": "",
        "flight_results": "",
        "hotel_results": "",
        "weather_result": "",
        "budget_results": "",
        "itinerary": "",
        "approval_request": "",
        "approved": False,
        "human_feedback": "",
        "final_response": "",
        "llm_calls": 0,
    }


def run_travel_agent(
    user_input: str | None = None,
    thread_id: str | None = None,
    approved: bool | None = None,
    feedback: str = "",
):
    thread_id = thread_id or f"user_{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    if approved is not None:
        result = get_travel_graph().invoke(Command(resume={"approved": approved, "feedback": feedback}), config=config)
    else:
        if not user_input or not user_input.strip():
            raise ValueError("Message cannot be empty.")
        result = get_travel_graph().invoke(_initial_state(user_input.strip()), config=config)

    interrupts = result.get("__interrupt__", [])
    status = "awaiting_approval" if interrupts else "completed"
    messages = result.get("messages", [])
    last_message = messages[-1] if messages else None
    answer = result.get("final_response") or (result.get("itinerary") if interrupts else getattr(last_message, "content", ""))
    interrupt_value = interrupts[0].value if interrupts else None
    return {
        "success": True,
        "status": status,
        "thread_id": thread_id,
        "answer": str(answer or ""),
        "approval_request": result.get("approval_request", ""),
        "approval": interrupt_value,
        "approved": result.get("approved", False),
        "guardrail_allowed": result.get("guardrail_allowed", False),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "selected_agents": result.get("selected_agents", []),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_result": result.get("weather_result", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }
