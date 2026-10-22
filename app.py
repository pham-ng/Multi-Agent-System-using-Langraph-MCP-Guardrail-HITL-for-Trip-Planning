import logging
import hmac
import os
import threading
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    auth_required,
    create_session,
    create_user,
    create_password_reset_token,
    find_user_by_credentials,
    get_user_from_session,
    revoke_session,
    reset_password,
    validate_password,
)
from backend import (
    close_database_connections,
    database_ready,
    delete_user_data,
    delete_travel_thread,
    enqueue_travel_job,
    get_travel_job,
    app_connection,
    archive_trip,
    audit_event,
    create_share_token,
    get_travel_thread,
    get_travel_thread_history,
    get_shared_trip,
    list_travel_threads,
    revoke_share_token,
    rename_trip,
    run_travel_agent,
)
from job_worker import worker as travel_job_worker

logger = logging.getLogger("tripmate.api")

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = "2.4.0"


@asynccontextmanager
async def lifespan(_app):
    if os.getenv("JOB_WORKER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
        travel_job_worker.start()
    try:
        yield
    finally:
        travel_job_worker.stop()
        close_database_connections()


app = FastAPI(
    title="AI Travel Planning Agent System",
    description="LangGraph travel planner with guardrail and human approval",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str | None = Field(default=None, max_length=8000)
    thread_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    approved: bool | None = None
    feedback: str = Field(default="", max_length=4000)
    async_mode: bool = False


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class TripUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


class SimpleRateLimiter:
    def __init__(self, limit: int = 30, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = [item for item in self._events.get(key, []) if now - item < self.window_seconds]
            if len(events) >= self.limit:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            if len(self._events) > 5000:
                self._events = {name: values for name, values in self._events.items() if values}
            return True


rate_limiter = SimpleRateLimiter(int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
request_metrics = Counter()
request_latency_ms = []
metrics_lock = threading.Lock()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or uuid.uuid4().hex


def _current_user(request: Request) -> dict[str, str]:
    token = request.cookies.get(SESSION_COOKIE)
    user = get_user_from_session(app_connection(), token) if token else None
    if user:
        return user
    if auth_required():
        raise PermissionError("Bạn cần đăng nhập để tiếp tục.")
    return {"id": "guest", "email": "guest@local.invalid", "display_name": "Guest"}


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


def _clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _same_origin_request(request: Request) -> bool:
    """Reject cross-site state changes when a browser session cookie is present."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or not request.cookies.get(SESSION_COOKIE):
        return True
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients commonly omit Origin; cookie remains HttpOnly.
    parsed = urlsplit(origin)
    expected_host = request.headers.get("host", "").lower()
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:20])
    start = time.perf_counter()
    if not _same_origin_request(request):
        response = JSONResponse(
            status_code=403,
            content={
                "success": False,
                "code": "csrf_blocked",
                "error": "Yêu cầu không hợp lệ từ nguồn khác.",
                "request_id": request.state.request_id,
            },
        )
    else:
        response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-App-Version"] = APP_VERSION
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self' https://cdnjs.cloudflare.com; connect-src 'self'; font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com"
    with metrics_lock:
        request_metrics[f"http_{request.method.lower()}_{request.url.path}_{response.status_code}"] += 1
        request_latency_ms.append((time.perf_counter() - start) * 1000)
        if len(request_latency_ms) > 5000:
            del request_latency_ms[:1000]
    logger.info("request method=%s path=%s status=%s latency_ms=%.1f request_id=%s", request.method, request.url.path, response.status_code, (time.perf_counter() - start) * 1000, request.state.request_id)
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/travel")
def travel_planner(request_data: TravelRequest, request: Request):
    try:
        user = _current_user(request)
        client_host = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(f"{user['id']}:{client_host}"):
            return JSONResponse(status_code=429, content={"success": False, "code": "rate_limited", "error": "Bạn gửi quá nhiều yêu cầu. Vui lòng thử lại sau một phút.", "request_id": _request_id(request)})
        message = request_data.message.strip() if request_data.message else None
        is_resume = request_data.approved is not None
        if not is_resume and not message:
            return JSONResponse(status_code=400, content={"success": False, "error": "Message cannot be empty"})
        if is_resume and not request_data.thread_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "thread_id is required for approval"})

        if request_data.async_mode:
            idempotency_key = request.headers.get("Idempotency-Key", "").strip()[:128] or None
            job = enqueue_travel_job(
                user["id"],
                {
                    "user_input": message,
                    "thread_id": request_data.thread_id,
                    "approved": request_data.approved,
                    "feedback": request_data.feedback,
                },
                request_id=_request_id(request),
                idempotency_key=idempotency_key,
            )
            audit_event("travel.queued", user_id=user["id"], thread_id=job.get("thread_id"), request_id=_request_id(request), metadata={"job_id": job["job_id"]})
            return JSONResponse(status_code=202, content={"success": True, "status": job["status"], **job})

        result = run_travel_agent(
            user_input=message,
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
            user_id=user["id"],
            request_id=_request_id(request),
        )
        return JSONResponse(content=result)
    except PermissionError as exc:
        status = 401 if "đăng nhập" in str(exc).lower() else 403
        return JSONResponse(status_code=status, content={"success": False, "code": "unauthenticated" if status == 401 else "forbidden", "error": str(exc), "request_id": _request_id(request)})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_request", "error": str(exc), "request_id": _request_id(request)})
    except Exception:
        logger.exception("travel_request_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=500, content={"success": False, "code": "internal_error", "error": "Không thể hoàn tất yêu cầu lúc này. Vui lòng thử lại.", "request_id": _request_id(request)})


@app.get("/api/jobs/{job_id}")
def travel_job(job_id: str, request: Request):
    try:
        user = _current_user(request)
        job = get_travel_job(job_id, user["id"])
        if not job:
            return JSONResponse(status_code=404, content={"success": False, "code": "not_found", "error": "Không tìm thấy tác vụ."})
        return {"success": True, "job": job, "job_id": job["id"], "status": job["status"], "result": job.get("result"), "error": job.get("error_message")}
    except PermissionError as exc:
        return JSONResponse(status_code=401, content={"success": False, "code": "unauthenticated", "error": str(exc)})
    except Exception:
        logger.exception("job_status_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=503, content={"success": False, "code": "job_unavailable", "error": "Không thể đọc trạng thái tác vụ."})


@app.post("/api/auth/signup")
def signup(request_data: AuthRequest, request: Request):
    try:
        user = create_user(app_connection(), request_data.email, validate_password(request_data.password), request_data.display_name)
        token = create_session(app_connection(), user["id"])
        audit_event("auth.signup", user_id=user["id"], request_id=_request_id(request))
        response = JSONResponse(content={"success": True, "user": user})
        _set_session(response, token)
        return response
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_auth", "error": str(exc), "request_id": _request_id(request)})
    except Exception:
        logger.exception("signup_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=500, content={"success": False, "code": "internal_error", "error": "Không thể tạo tài khoản lúc này.", "request_id": _request_id(request)})


@app.post("/api/auth/login")
def login(request_data: AuthRequest, request: Request):
    try:
        user = find_user_by_credentials(app_connection(), request_data.email, request_data.password)
        if not user:
            return JSONResponse(status_code=401, content={"success": False, "code": "invalid_credentials", "error": "Email hoặc mật khẩu không đúng.", "request_id": _request_id(request)})
        token = create_session(app_connection(), user["id"])
        audit_event("auth.login", user_id=user["id"], request_id=_request_id(request))
        response = JSONResponse(content={"success": True, "user": user})
        _set_session(response, token)
        return response
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_auth", "error": str(exc), "request_id": _request_id(request)})
    except Exception:
        logger.exception("login_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=500, content={"success": False, "code": "internal_error", "error": "Không thể đăng nhập lúc này.", "request_id": _request_id(request)})


@app.post("/api/auth/logout")
def logout(request: Request):
    try:
        current = _current_user(request)
        if current["id"] != "guest":
            audit_event("auth.logout", user_id=current["id"], request_id=_request_id(request))
        revoke_session(app_connection(), request.cookies.get(SESSION_COOKIE))
    except Exception:
        logger.exception("logout_failed request_id=%s", _request_id(request))
    response = JSONResponse(content={"success": True})
    _clear_session(response)
    return response


@app.post("/api/auth/password-reset/request")
def password_reset_request(request_data: PasswordResetRequest, request: Request):
    try:
        token = create_password_reset_token(app_connection(), request_data.email)
        # A mail provider can be connected later without changing this API contract.
        payload = {"success": True, "message": "Nếu email tồn tại, hướng dẫn đặt lại mật khẩu sẽ được gửi."}
        if token and os.getenv("PASSWORD_RESET_DEBUG", "false").lower() in {"1", "true", "yes"}:
            payload["debug_token"] = token
        return payload
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_auth", "error": str(exc), "request_id": _request_id(request)})
    except Exception:
        logger.exception("password_reset_request_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=500, content={"success": False, "code": "internal_error", "error": "Không thể xử lý yêu cầu lúc này."})


@app.post("/api/auth/password-reset/confirm")
def password_reset_confirm(request_data: PasswordResetConfirm, request: Request):
    try:
        if not reset_password(app_connection(), request_data.token, request_data.password):
            return JSONResponse(status_code=400, content={"success": False, "code": "invalid_token", "error": "Token đặt lại mật khẩu không hợp lệ hoặc đã hết hạn."})
        return {"success": True, "message": "Mật khẩu đã được cập nhật. Vui lòng đăng nhập lại."}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_auth", "error": str(exc), "request_id": _request_id(request)})


@app.delete("/api/account")
def delete_account(request: Request):
    try:
        user = _current_user(request)
        if user["id"] == "guest":
            return JSONResponse(status_code=401, content={"success": False, "code": "unauthenticated", "error": "Bạn cần đăng nhập để xoá tài khoản."})
        delete_user_data(user["id"])
        response = JSONResponse(content={"success": True, "message": "Tài khoản và dữ liệu đã được xoá."})
        _clear_session(response)
        return response
    except PermissionError as exc:
        return JSONResponse(status_code=401, content={"success": False, "code": "unauthenticated", "error": str(exc)})


@app.get("/api/auth/me")
def me(request: Request):
    try:
        user = _current_user(request)
        return {"success": True, "authenticated": user["id"] != "guest", "auth_required": auth_required(), "user": user}
    except PermissionError:
        return {"success": True, "authenticated": False, "auth_required": True, "user": None}


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": APP_VERSION, "message": "AI Travel Planner is running"}


@app.get("/ready")
async def readiness_check():
    configuration_issues = []
    if auth_required() and not os.getenv("APP_SECRET_KEY", "").strip():
        configuration_issues.append("APP_SECRET_KEY is required when AUTH_REQUIRED=true")
    if auth_required() and os.getenv("COOKIE_SECURE", "true").lower() not in {"1", "true", "yes", "on"}:
        configuration_issues.append("COOKIE_SECURE must be enabled when AUTH_REQUIRED=true")
    if configuration_issues:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "configuration invalid", "issues": configuration_issues})
    ready, detail = database_ready()
    payload = {"status": "ready" if ready else "not_ready", "database": detail}
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/internal/metrics")
def internal_metrics(request: Request):
    configured = os.getenv("METRICS_TOKEN", "").strip()
    supplied = request.headers.get("X-Metrics-Token", "")
    if not configured or not supplied or not hmac.compare_digest(configured, supplied):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    with metrics_lock:
        latencies = sorted(request_latency_ms)
        count = len(latencies)
        p95 = latencies[min(count - 1, int(count * 0.95))] if count else 0
        return {
            "success": True,
            "requests": dict(request_metrics),
            "latency_ms": {"count": count, "p50": latencies[int(count * 0.50)] if count else 0, "p95": p95, "max": latencies[-1] if count else 0},
        }


@app.get("/api/threads")
def threads(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"success": True, "threads": list_travel_threads(limit, _current_user(request)["id"])}
    except PermissionError as exc:
        return JSONResponse(status_code=401, content={"success": False, "code": "unauthenticated", "error": str(exc)})
    except Exception:
        logger.exception("threads_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=503, content={"success": False, "code": "history_unavailable", "error": "Lịch sử tạm thời chưa sẵn sàng."})


@app.get("/api/threads/{thread_id}/history")
def thread_history(thread_id: str, request: Request, limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"success": True, "thread_id": thread_id, "history": get_travel_thread_history(thread_id, limit, _current_user(request)["id"])}
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})
    except Exception:
        logger.exception("thread_history_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=503, content={"success": False, "code": "history_unavailable", "error": "Lịch sử tạm thời chưa sẵn sàng."})


@app.get("/api/threads/{thread_id}")
def thread_detail(thread_id: str, request: Request):
    try:
        result = get_travel_thread(thread_id, _current_user(request)["id"])
        if result is None:
            return JSONResponse(status_code=404, content={"success": False, "code": "not_found", "error": "Không tìm thấy chuyến đi."})
        return JSONResponse(content=result)
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})
    except Exception:
        logger.exception("thread_detail_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=503, content={"success": False, "code": "thread_unavailable", "error": "Chuyến đi tạm thời chưa sẵn sàng."})


@app.patch("/api/threads/{thread_id}")
def update_thread(thread_id: str, request_data: TripUpdateRequest, request: Request):
    try:
        user_id = _current_user(request)["id"]
        result: dict[str, object] = {}
        if request_data.title is not None:
            result.update(rename_trip(thread_id, user_id, request_data.title))
        if request_data.archived is not None:
            result.update(archive_trip(thread_id, user_id, request_data.archived))
        if not result:
            raise ValueError("Không có thay đổi nào.")
        return {"success": True, "trip": result}
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})
    except KeyError:
        return JSONResponse(status_code=404, content={"success": False, "code": "not_found", "error": "Không tìm thấy chuyến đi."})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "code": "invalid_request", "error": str(exc)})


@app.get("/api/threads/{thread_id}/export")
def export_thread(thread_id: str, request: Request, format: str = Query(default="markdown", pattern="^(markdown|ics)$")):
    try:
        data = get_travel_thread(thread_id, _current_user(request)["id"])
        if not data:
            return JSONResponse(status_code=404, content={"success": False, "code": "not_found", "error": "Không tìm thấy chuyến đi."})
        if format == "ics":
            description = str(data.get("answer", "")).replace("\n", " ").replace(",", "\\,")[:800]
            content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//TripMate//Travel Planner//EN\nBEGIN:VEVENT\nSUMMARY:TripMate itinerary\nDESCRIPTION:" + description + "\nEND:VEVENT\nEND:VCALENDAR\n"
            return Response(content=content, media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="tripmate-{thread_id}.ics"'})
        content = f"# TripMate\n\n{data.get('answer', '')}\n\n## Verification\n- Status: {data.get('grounding_status', 'pending')}\n- LLM calls: {data.get('llm_calls', 0)}\n"
        return PlainTextResponse(content=content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="tripmate-{thread_id}.md"'})
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})


@app.post("/api/threads/{thread_id}/share")
def share_thread(thread_id: str, request: Request):
    try:
        token = create_share_token(thread_id, _current_user(request)["id"])
        return {"success": True, "expires_in_hours": 72, "url": f"{str(request.base_url).rstrip('/')}/shared/{token}"}
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})


@app.delete("/api/threads/{thread_id}/share")
def unshare_thread(thread_id: str, request: Request):
    try:
        revoke_share_token(thread_id, _current_user(request)["id"])
        return {"success": True}
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})


@app.get("/shared/{token}")
def shared_thread(token: str):
    try:
        result = get_shared_trip(token)
        if not result:
            return JSONResponse(status_code=404, content={"success": False, "code": "share_expired", "error": "Liên kết chia sẻ không tồn tại hoặc đã hết hạn."})
        return result
    except Exception:
        logger.exception("shared_thread_failed")
        return JSONResponse(status_code=503, content={"success": False, "code": "share_unavailable", "error": "Không thể tải liên kết chia sẻ lúc này."})


@app.delete("/api/threads/{thread_id}")
def remove_thread(thread_id: str, request: Request, x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    try:
        deleted = delete_travel_thread(thread_id, _current_user(request)["id"])
        if not deleted:
            return JSONResponse(status_code=404, content={"success": False, "code": "not_found", "error": "Không tìm thấy chuyến đi."})
        return {"success": True, "thread_id": thread_id, "deleted": True}
    except PermissionError as exc:
        return JSONResponse(status_code=403, content={"success": False, "code": "forbidden", "error": str(exc)})
    except Exception:
        logger.exception("thread_delete_failed request_id=%s", _request_id(request))
        return JSONResponse(status_code=503, content={"success": False, "code": "delete_unavailable", "error": "Không thể xóa chuyến đi lúc này."})


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
