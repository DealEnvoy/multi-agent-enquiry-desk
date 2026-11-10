"""Supervisor policy.

This module is ordinary Python. It does not call a model. The supervisor uses
it to choose the next node from the shared state, which keeps routing testable
and stops a malformed model reply from skipping the human checkpoint.
"""

from __future__ import annotations

from enquiry_desk.data import RESEARCH_INTENTS
from enquiry_desk.state import EnquiryState

DEFAULT_MAX_REVIEW_ROUNDS = 2
DEFAULT_MODEL_ATTEMPTS = 3
DEFAULT_JSON_REPAIRS = 2
DEFAULT_TOOL_ROUNDS = 4
DEFAULT_TOOL_ATTEMPTS = 3


def decide(state: EnquiryState, *, max_review_rounds: int) -> str:
    """Return the name of the next node."""
    if state["fatal_error"]:
        return "approval" if state["approval"] is None else "finalize"
    if not state["intent"]:
        return "router"
    if state["intent"] in RESEARCH_INTENTS and state["research_status"] == "pending":
        return "research"
    if _needs_draft(state, max_review_rounds=max_review_rounds):
        return "drafter"
    if state["reviewed_version"] < state["draft_version"]:
        return "reviewer"
    if state["approval"] is None:
        return "approval"
    return "finalize"


def _needs_draft(state: EnquiryState, *, max_review_rounds: int) -> bool:
    if state["draft_version"] == 0:
        return True
    return (
        state["review_passed"] is False
        and state["reviewed_version"] == state["draft_version"]
        and state["draft_version"] < max_review_rounds
    )


def recommend(state: EnquiryState) -> str:
    """Say what the desk wants a person to do. This does not release a reply."""
    if state["fatal_error"] or state["review_passed"] is not True or state["intent"] in (None, "", "unknown"):
        return "escalate"
    if state["intent"] == "spam":
        return "archive"
    if state["intent"] in RESEARCH_INTENTS:
        return "send"
    return "escalate"


def resolve_outcome(recommended: str, action: str) -> str:
    """Map a human decision to a recorded outcome.

    Approve only releases a reply when the recommendation is send, which
    itself requires a passing review. A failed review stays escalated.
    """
    if action != "approve":
        return "withheld"
    if recommended == "send":
        return "approved_to_send"
    if recommended == "archive":
        return "archived"
    return "escalated"
