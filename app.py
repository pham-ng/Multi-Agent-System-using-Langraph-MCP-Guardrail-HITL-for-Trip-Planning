import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from backend import run_travel_agent

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(
    title="AI Travel Planning Agent System",
    description="LangGraph travel planner with guardrail and human approval",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str | None = None
    thread_id: str | None = None
    approved: bool | None = None
    feedback: str = ""


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


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
