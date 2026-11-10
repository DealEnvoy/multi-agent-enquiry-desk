"""Plain-text transcript for a run."""

from __future__ import annotations

from enquiry_desk.state import EnquiryState
from enquiry_desk.workflow import recommend

OUTCOME_COPY = {
    "approved_to_send": "Marked ready to send. Mail delivery is out of scope for this demo.",
    "archived": "Marked for archive.",
    "escalated": "Held for a person. The desk did not mark a reply ready to send.",
    "withheld": "Withheld after the human decision.",
}


def render_pause(values: EnquiryState, *, model_label: str) -> str:
    sections = [
        _header(values, model_label),
        "Trace",
        render_trace(values["trace"]),
        "",
        render_drafts(values["drafts"]),
        "",
        render_reviews(values["review_history"]),
        "",
        render_checkpoint(values),
        "",
        render_usage(values),
    ]
    return "\n".join(sections).rstrip() + "\n"


def render_close(values: EnquiryState) -> str:
    approval = values["approval"] or {"action": "reject", "note": ""}
    outcome = values["outcome"] or "escalated"
    lines = [
        f"Decision: {approval['action']}",
        f"Outcome: {outcome}",
        OUTCOME_COPY.get(outcome, "Recorded."),
    ]
    if values.get("log_path"):
        lines.append(f"Run log: {values['log_path']}")
    return "\n" + "\n".join(lines) + "\n"


def render_trace(trace: list[dict]) -> str:
    if not trace:
        return "(no trace)"
    lines = []
    for index, event in enumerate(trace, start=1):
        lines.append(f"{index:>2}  {event['agent']:<11} {event['message']}")
    return "\n".join(lines)


def render_drafts(drafts: list[str]) -> str:
    if not drafts:
        return "No customer draft was written."
    blocks = []
    for index, draft in enumerate(drafts, start=1):
        blocks.append(f"Draft round {index}\n{draft}")
    return "\n\n".join(blocks)


def render_reviews(history: list[dict]) -> str:
    if not history:
        return "Review\nReview did not run."
    lines = ["Review"]
    for item in history:
        if item["passed"]:
            lines.append(f"Round {item['round']} passed. {item['note']}")
        else:
            issues = "; ".join(item["issues"])
            lines.append(f"Round {item['round']} failed. {item['note']} Issues: {issues}")
    return "\n".join(lines)


def render_checkpoint(values: EnquiryState) -> str:
    lines = [
        "Checkpoint",
        f"Recommended action: {recommend(values)}",
        "A person has to decide before the desk records an outcome.",
    ]
    if values.get("fatal_error"):
        lines.append(f"Failure: {values['fatal_error']}")
    return "\n".join(lines)


def render_usage(values: EnquiryState) -> str:
    usage = values["usage"]
    labels = {event["rate_label"] for event in values["usage_events"]}
    lines = [
        (
            f"Tokens: prompt {usage['prompt_tokens']}, "
            f"completion {usage['completion_tokens']}, "
            f"total {usage['total_tokens']}"
        )
    ]
    if labels <= {"stub"}:
        lines.append("Illustrative cost: $0.00. Stub rates are zero. This is not a bill.")
    else:
        lines.append(
            f"Illustrative cost: ${usage['estimated_cost_usd']:.4f}. "
            "Example rate table, not a provider invoice."
        )
    return "\n".join(lines)


def _header(values: EnquiryState, model_label: str) -> str:
    enquiry = values["enquiry"]
    return "\n".join(
        [
            "Enquiry desk",
            "Hale & Quay Lettings (fictional agency, demo data)",
            "",
            f"Enquiry: {enquiry['enquiry_id']}",
            f"From: {enquiry['sender_name']} <{enquiry['sender_email']}>",
            f"Subject: {enquiry['subject']}",
            f"Model: {model_label}",
            "",
        ]
    )
