# TripMate — Guardrail + Human-in-the-Loop Travel Planner

TripMate is a production-oriented multi-agent travel planner built with **FastAPI**, **LangGraph**, **MCP**, and **Groq**. It turns a natural-language travel request into a reviewed itinerary while keeping the workflow observable and interruptible.

The core principle is simple: the system can research and draft a plan, but it does not present the final itinerary until a human approves it.

## Features

- Guardrail classification before external research.
- Supervisor routing that selects only relevant specialist agents.
- Flight, hotel, destination, weather, and budget context.
- MCP integration with a local weather server and search adapters.
- LangGraph `interrupt()` approval checkpoint.
- Approval and revision flows on the same persisted `thread_id`.
- PostgreSQL checkpoint storage for safe pause/resume.
- Responsive web UI with live status, agent pipeline, approval controls, and expandable research details.
- Docker and Render deployment configuration.

## Architecture

```text
Browser → FastAPI → Guardrail → Supervisor
                            ├─ Flight agent
                            ├─ Hotel agent
                            ├─ Weather agent
                            ├─ Budget agent
                            └─ Itinerary agent → Human approval → Final answer
```

| File | Responsibility |
| --- | --- |
| `app.py` | FastAPI app, UI, health endpoint, travel API |
| `backend.py` | LangGraph state, guardrail, supervisor, agents, HITL resume |
| `mcp_client.py` | MCP helpers and provider adapters |
| `custom_weather_mcp_server.py` | Local weather MCP server |
| `templates/index.html` | TripMate web shell |
| `static/style.css` | Responsive design system |
| `static/script.js` | Chat, approval, tabs, loading/error states |
| `render.yaml` | Render Blueprint configuration |

## Requirements

- Python 3.11+
- PostgreSQL 14+ or a managed PostgreSQL connection string
- Groq API key
- Optional provider keys for flight, weather, and web search data
- `uv` for MCP tool execution; the Dockerfile installs it automatically

## Local setup

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `.env`:

| Variable | Required | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Yes | LLM reasoning and response generation |
| `DATABASE_URL` | Yes for persistent HITL | PostgreSQL checkpoint storage |
| `AVIATIONSTACK_API_KEY` | Recommended | Flight context |
| `OPEN_WEATHER_API_KEY` | Recommended | Weather context |
| `TAVILY_API_KEY` | Recommended | Destination/web research |
| `DEFAULT_ORIGIN_IATA` | No | Fallback airport, default `SGN` |
| `LANGSMITH_TRACING` | No | Set `true` to enable tracing |
| `LANGSMITH_API_KEY` | No | LangSmith credential |
| `LANGSMITH_ENDPOINT` | No | Defaults to `https://api.smith.langchain.com` |
| `LANGSMITH_PROJECT` | No | Trace project name |

Never commit `.env`; only `.env.example` belongs in Git.

## Run locally

```powershell
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). For a production-like local server:

```powershell
uvicorn app:app --host 0.0.0.0 --port 8000
```

## API

### Health check

```http
GET /health
```

```json
{"status":"ok","message":"AI Travel Planner is running"}
```

### Start a plan

```bash
curl -X POST http://127.0.0.1:8000/api/travel \
  -H "Content-Type: application/json" \
  -d '{"message":"Plan a 2 day trip from Hanoi to Tokyo with flights, hotels and weather"}'
```

The response includes a `thread_id`. When the workflow reaches the approval gate, it returns `status: "awaiting_approval"` and an `approval_request`.

### Approve

```bash
curl -X POST http://127.0.0.1:8000/api/travel \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"YOUR_THREAD_ID","approved":true,"feedback":""}'
```

### Request a revision

```bash
curl -X POST http://127.0.0.1:8000/api/travel \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"YOUR_THREAD_ID","approved":false,"feedback":"Ưu tiên khách sạn gần ga tàu và giảm ngân sách."}'
```

Interactive OpenAPI docs are available at `/docs`.

## Docker

```powershell
docker build -t tripmate-guardrail-hitl .
docker run --rm -p 8000:8000 --env-file .env tripmate-guardrail-hitl
```

The image uses Render's `PORT` variable and falls back to port `8000` locally.

## Deploy to Render

`render.yaml` defines a Docker web service named `tripmate-guardrail-hitl` on branch `main` with automatic deploys and health check `/health`.

### Dashboard

1. Choose **New → Blueprint** in Render.
2. Select this repository and branch `main`.
3. Apply the Blueprint.
4. Add every `sync: false` variable in the service Environment page.
5. Save changes and wait for the deploy to become live.

### CLI

```powershell
render login
render workspace set YOUR_WORKSPACE
render blueprints validate .\render.yaml
```

Keep API keys and database URLs out of `render.yaml`, Git commits, Docker build arguments, and frontend code. Render secrets should be entered through its Environment settings.

## Production checklist

- [ ] Set `GROQ_API_KEY` and `DATABASE_URL` in Render.
- [ ] Add provider keys for the agents you use.
- [ ] Confirm `/health` returns HTTP 200.
- [ ] Test a safe travel request and approve the draft.
- [ ] Test an unrelated request and confirm guardrail behavior.
- [ ] Test revision with `approved: false` and feedback.
- [ ] Review database network access and rotate exposed credentials.
- [ ] Enable LangSmith tracing only when needed.

## Verification

```powershell
python -m py_compile app.py backend.py custom_weather_mcp_server.py mcp_client.py
node --check static/script.js
git diff --check
```

## Data notes and limitations

- Provider availability and pricing depend on API plans and live responses.
- Aviation status data is not a guaranteed booking or final ticket price.
- Weather forecasts can change after a plan is generated.
- Render's free plan may sleep between requests and is not intended for high-throughput production traffic.
- Always verify final prices, availability, cancellation policies, and travel requirements with the provider.

## License

Add the project's preferred license before distributing the application publicly.
