import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import (
    database_ready,
    delete_travel_thread,
    get_travel_thread,
    get_travel_thread_history,
    list_travel_threads,
    run_travel_agent,
)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(
    title="AI Travel Planning Agent System",
    description="LangGraph travel planner with guardrail and human approval",
    version="2.2.0",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str | None = Field(default=None, max_length=8000)
    thread_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    approved: bool | None = None
    feedback: str = Field(default="", max_length=4000)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/travel")
def travel_planner(request_data: TravelRequest):
    try:
        message = request_data.message.strip() if request_data.message else None
        is_resume = request_data.approved is not None
        if not is_resume and not message:
            return JSONResponse(status_code=400, content={"success": False, "error": "Message cannot be empty"})
        if is_resume and not request_data.thread_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "thread_id is required for approval"})

        result = run_travel_agent(
            user_input=message,
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
        )
        return JSONResponse(content=result)
    except Exception:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Unable to complete the travel plan. Please try again later."},
        )


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "AI Travel Planner is running"}


@app.get("/ready")
async def readiness_check():
    ready, detail = database_ready()
    payload = {"status": "ready" if ready else "not_ready", "database": detail}
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/api/threads")
def threads(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"success": True, "threads": list_travel_threads(limit)}
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=503, content={"success": False, "error": "History is temporarily unavailable."})


@app.get("/api/threads/{thread_id}/history")
def thread_history(thread_id: str, limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"success": True, "thread_id": thread_id, "history": get_travel_thread_history(thread_id, limit)}
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=503, content={"success": False, "error": "Thread history is temporarily unavailable."})


@app.get("/api/threads/{thread_id}")
def thread_detail(thread_id: str):
    try:
        result = get_travel_thread(thread_id)
        if result is None:
            return JSONResponse(status_code=404, content={"success": False, "error": "Thread not found."})
        return JSONResponse(content=result)
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=503, content={"success": False, "error": "Thread is temporarily unavailable."})


@app.delete("/api/threads/{thread_id}")
def remove_thread(thread_id: str, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    import os

    expected = os.getenv("HISTORY_ADMIN_TOKEN")
    if not expected or x_admin_token != expected:
        return JSONResponse(status_code=403, content={"success": False, "error": "History deletion is not authorized."})
    try:
        deleted = delete_travel_thread(thread_id)
        if not deleted:
            return JSONResponse(status_code=404, content={"success": False, "error": "Thread not found."})
        return {"success": True, "thread_id": thread_id, "deleted": True}
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=503, content={"success": False, "error": "Thread deletion is temporarily unavailable."})


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
