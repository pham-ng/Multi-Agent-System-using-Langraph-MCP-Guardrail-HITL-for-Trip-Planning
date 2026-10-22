"""Small in-process observability counters for model usage and provider health.

The counters are intentionally safe to expose through the protected metrics endpoint
without persisting prompts, completions, or secrets. A production deployment can
forward the same values to OpenTelemetry/Prometheus later.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from typing import Any


_lock = threading.Lock()
_llm_usage = Counter()


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def extract_usage(response: Any) -> dict[str, int]:
    """Read token usage across LangChain/Groq response metadata variants."""
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(response, "response_metadata", None)
        usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
        if not isinstance(usage, dict) and isinstance(metadata, dict):
            usage = metadata.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _as_int(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _as_int(usage.get("output_tokens", usage.get("completion_tokens")))
    total_tokens = _as_int(usage.get("total_tokens")) or input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def record_llm_usage(response: Any, *, provider: str = "groq", model: str = "") -> dict[str, int]:
    usage = extract_usage(response)
    with _lock:
        _llm_usage["calls"] += 1
        _llm_usage["input_tokens"] += usage["input_tokens"]
        _llm_usage["output_tokens"] += usage["output_tokens"]
        _llm_usage["total_tokens"] += usage["total_tokens"]
        _llm_usage[f"provider_{provider}_calls"] += 1
        if model:
            _llm_usage[f"model_{model}_calls"] += 1
    return usage


def llm_usage_snapshot() -> dict[str, Any]:
    with _lock:
        data = dict(_llm_usage)
    input_rate = _as_float(os.getenv("LLM_INPUT_COST_PER_1M_USD", "0"))
    output_rate = _as_float(os.getenv("LLM_OUTPUT_COST_PER_1M_USD", "0"))
    data["estimated_cost_usd"] = round(
        data.get("input_tokens", 0) * input_rate / 1_000_000
        + data.get("output_tokens", 0) * output_rate / 1_000_000,
        8,
    )
    data["pricing"] = {
        "input_usd_per_1m": input_rate,
        "output_usd_per_1m": output_rate,
    }
    return data
