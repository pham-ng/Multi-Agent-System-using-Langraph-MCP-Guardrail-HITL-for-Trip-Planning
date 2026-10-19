# TripMate — Guardrail + Human-in-the-Loop

Multi-agent travel planner built with FastAPI and LangGraph. The flow is:

`guardrail → supervisor → flights → hotels → weather → budget → itinerary draft → human approval → final answer`

## Setup

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `GROQ_API_KEY`, `AVIATIONSTACK_API_KEY`, `OPEN_WEATHER_API_KEY`,
`TAVILY_API_KEY` and `DATABASE_URL` in `.env`.

## Run

```powershell
python app.py
```

Open `http://127.0.0.1:8000`.

The first request returns `status: "awaiting_approval"` with a draft. Resume
the same `thread_id` using:

```json
{
  "thread_id": "...",
  "approved": true,
  "feedback": ""
}
```

For a revision, send `approved: false` and include feedback. PostgreSQL is used
by LangGraph to persist the paused workflow and resume it safely.

## API

- `GET /health` — liveness check.
- `POST /api/travel` — starts a plan or resumes human approval.
- `GET /docs` — interactive FastAPI documentation.

The application uses live flight status data, web search and weather data where
the configured provider plan supports them. Ticket prices are not returned by
the AviationStack status API and must be treated as estimates only.
