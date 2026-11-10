"""Token totals and an example price table.

The prices are here so a run can show a cost figure. They are round example
numbers, not a provider invoice. Replace them with the prices you actually pay.
"""

from __future__ import annotations

from typing import TypedDict


class Usage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


# USD per 1,000,000 tokens. "example" is a stand-in for a live model name
# that is not listed here. Stub and scripted calls are priced at zero.
RATES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "stub": {"input": 0.0, "output": 0.0},
    "scripted": {"input": 0.0, "output": 0.0},
    "example": {"input": 3.0, "output": 15.0},
}


def empty_usage() -> Usage:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }


def estimate_tokens(text: str) -> int:
    """Offline token estimate: about four characters per token."""
    return max(1, (len(text) + 3) // 4)


def rate_for(model_name: str) -> tuple[dict[str, float], str]:
    if model_name in RATES_USD_PER_MILLION and model_name != "example":
        return RATES_USD_PER_MILLION[model_name], model_name
    return RATES_USD_PER_MILLION["example"], "example"


def estimate_cost_usd(model_name: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, str]:
    rate, label = rate_for(model_name)
    cost = (prompt_tokens * rate["input"] + completion_tokens * rate["output"]) / 1_000_000
    return round(cost, 6), label


def usage_from_counts(model_name: str, prompt_tokens: int, completion_tokens: int) -> tuple[Usage, str]:
    cost, label = estimate_cost_usd(model_name, prompt_tokens, completion_tokens)
    usage: Usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "estimated_cost_usd": cost,
    }
    return usage, label


def add_usage(current: Usage | None, update: Usage | None) -> Usage:
    """LangGraph reducer. Totals grow as each agent returns its own calls."""
    base = current or empty_usage()
    extra = update or empty_usage()
    prompt = base["prompt_tokens"] + extra["prompt_tokens"]
    completion = base["completion_tokens"] + extra["completion_tokens"]
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "estimated_cost_usd": round(base["estimated_cost_usd"] + extra["estimated_cost_usd"], 6),
    }
