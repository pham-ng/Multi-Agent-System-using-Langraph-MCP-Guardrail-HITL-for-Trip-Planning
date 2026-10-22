"""Authenticated async workflow smoke test.

Usage:
    python scripts/smoke_async.py

The test creates a temporary account, verifies idempotency and approval resume,
then deletes the account unless KEEP_SMOKE_DATA=true.
"""

from __future__ import annotations

import os
import time
import uuid

import requests


BASE_URL = os.getenv("TRIPMATE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PASSWORD = os.getenv("TRIPMATE_SMOKE_PASSWORD", "SmokeTest-2026!SafePassword")
EMAIL = os.getenv("TRIPMATE_SMOKE_EMAIL", f"smoke-{uuid.uuid4().hex[:10]}@example.invalid")
KEEP_DATA = os.getenv("KEEP_SMOKE_DATA", "false").lower() in {"1", "true", "yes"}


def call(session: requests.Session, method: str, path: str, **kwargs):
    response = session.request(method, BASE_URL + path, timeout=60, **kwargs)
    body = response.json()
    response.raise_for_status()
    return response, body


def poll(session: requests.Session, job_id: str) -> dict:
    deadline = time.time() + 300
    while time.time() < deadline:
        _, body = call(session, "GET", f"/api/jobs/{job_id}")
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(2)
    raise TimeoutError(f"job did not finish: {job_id}")


def main() -> None:
    session = requests.Session()
    try:
        call(session, "POST", "/api/auth/signup", json={"email": EMAIL, "password": PASSWORD, "display_name": "Async Smoke"})
        headers = {"Idempotency-Key": f"smoke-{uuid.uuid4().hex}"}
        payload = {"message": "Plan a 1 day trip from Hanoi to Da Nang with flights and weather.", "async_mode": True}
        first_response, first = call(session, "POST", "/api/travel", headers=headers, json=payload)
        second_response, second = call(session, "POST", "/api/travel", headers=headers, json=payload)
        assert first_response.status_code == 202 and second_response.status_code == 202
        assert first["job_id"] == second["job_id"], (first, second)

        draft_job = poll(session, first["job_id"])
        draft = draft_job.get("result") or {}
        final_job = draft_job
        if draft_job["status"] == "awaiting_approval":
            _, resume = call(session, "POST", "/api/travel", json={"thread_id": draft["thread_id"], "approved": True, "async_mode": True})
            final_job = poll(session, resume["job_id"])
        final = final_job.get("result") or {}
        _, threads = call(session, "GET", "/api/threads")
        _, detail = call(session, "GET", f"/api/threads/{draft['thread_id']}")
        assert any(item.get("thread_id") == draft["thread_id"] for item in threads.get("threads", []))
        assert detail.get("status") == final_job["status"]
        print({"draft_status": draft_job["status"], "final_status": final_job["status"], "grounding_status": final.get("grounding_status"), "job_id": first["job_id"]})
    finally:
        if not KEEP_DATA:
            session.delete(BASE_URL + "/api/account", timeout=60)


if __name__ == "__main__":
    main()
