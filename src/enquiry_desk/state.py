"""Shared state for one enquiry.

Agents return partial updates. LangGraph merges them. Lists that should grow
(the trace, tool calls, drafts, token events) use an append reducer. Everything
else is replaced by the agent that owns it.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from enquiry_desk.cost import Usage, add_usage, empty_usage


class Enquiry(TypedDict):
    enquiry_id: str
    received_at: str
    sender_name: str
    sender_email: str
    channel: str
    subject: str
    body: str


class TraceEvent(TypedDict):
    agent: str
    event: str
    message: str


class ToolEvent(TypedDict):
    agent: str
    name: str
    ok: bool
    attempt: int
    detail: str
    args: dict[str, Any]


class UsageEvent(TypedDict):
    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    rate_label: str


class EnquiryState(TypedDict):
    enquiry: Enquiry
    model_name: str
    intent: str | None
    urgency: str | None
    listing_query: str
    router_reasoning: str
    hints: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    research_status: str
    draft: str | None
    drafts: Annotated[list[str], operator.add]
    draft_version: int
    review_passed: bool | None
    review_issues: list[str]
    review_rounds: int
    reviewed_version: int
    review_history: Annotated[list[dict[str, Any]], operator.add]
    recommended_action: str
    approval: dict[str, str] | None
    outcome: str | None
    fatal_error: str | None
    status: str
    next_agent: str
    log_path: str | None
    trace: Annotated[list[TraceEvent], operator.add]
    tool_events: Annotated[list[ToolEvent], operator.add]
    usage_events: Annotated[list[UsageEvent], operator.add]
    usage: Annotated[Usage, add_usage]


def initial_state(enquiry: Enquiry, *, model_name: str) -> EnquiryState:
    return {
        "enquiry": enquiry,
        "model_name": model_name,
        "intent": None,
        "urgency": None,
        "listing_query": "",
        "router_reasoning": "",
        "hints": [],
        "facts": [],
        "research_status": "pending",
        "draft": None,
        "drafts": [],
        "draft_version": 0,
        "review_passed": None,
        "review_issues": [],
        "review_rounds": 0,
        "reviewed_version": 0,
        "review_history": [],
        "recommended_action": "",
        "approval": None,
        "outcome": None,
        "fatal_error": None,
        "status": "received",
        "next_agent": "",
        "log_path": None,
        "trace": [],
        "tool_events": [],
        "usage_events": [],
        "usage": empty_usage(),
    }
