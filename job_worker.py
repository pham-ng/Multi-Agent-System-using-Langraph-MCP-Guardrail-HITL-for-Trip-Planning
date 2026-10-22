"""Durable PostgreSQL-backed worker for long-running travel graph jobs."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("tripmate.jobs")


class TravelJobWorker:
    def __init__(self, poll_seconds: float = 1.5):
        self.poll_seconds = max(0.5, poll_seconds)
        self.worker_id = f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="tripmate-job-worker", daemon=True)
        self._thread.start()
        logger.info("travel job worker started worker_id=%s", self.worker_id)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # Import lazily to keep app startup and unit tests independent from the graph.
        from backend import (
            cleanup_operational_data,
            claim_next_travel_job,
            fail_travel_job,
            finish_travel_job,
            recover_stale_travel_jobs,
            run_travel_agent,
        )

        last_cleanup = 0.0
        while not self._stop.is_set():
            try:
                recover_stale_travel_jobs()
                now = time.monotonic()
                if now - last_cleanup > 3600:
                    cleanup_operational_data()
                    last_cleanup = now
                job = claim_next_travel_job(self.worker_id)
                if not job:
                    self._stop.wait(self.poll_seconds)
                    continue
                payload = dict(job.get("payload") or {})
                try:
                    result = run_travel_agent(
                        user_input=payload.get("user_input"),
                        thread_id=payload.get("thread_id"),
                        approved=payload.get("approved"),
                        feedback=payload.get("feedback", ""),
                        user_id=job["user_id"],
                        request_id=job.get("request_id"),
                    )
                    finish_travel_job(job["id"], result)
                except Exception as exc:
                    logger.exception("travel job failed job_id=%s attempt=%s", job["id"], job.get("attempts"))
                    fail_travel_job(job["id"], exc, retry_delay_seconds=min(60, 2 ** min(job.get("attempts", 1), 5)))
            except Exception:
                logger.exception("travel job worker loop failed")
                self._stop.wait(min(10, self.poll_seconds * 3))


worker = TravelJobWorker()
