"""Agent nodes.

Each node reads the shared state, calls the model and its tools, and returns a
partial update. The supervisor decides where control goes next. If a node still
fails after LangGraph's retries, the error handler parks the case at approval
instead of crashing the run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.errors import NodeError
from langgraph.types import Command, interrupt

from enquiry_desk.cost import Usage, add_usage, empty_usage
from enquiry_desk.data import INTENTS, RESEARCH_INTENTS
from enquiry_desk.errors import JSONParseError, ModelOutputError, public_error
from enquiry_desk.models import AgentModel, priced
from enquiry_desk.parsing import parse_json_object
from enquiry_desk.prompts import system_prompt
from enquiry_desk.state import EnquiryState, TraceEvent
from enquiry_desk.tools import ToolRegistry, facts_from_tool_results
from enquiry_desk.workflow import decide, recommend, resolve_outcome

Validator = Callable[[dict[str, Any]], None]


@dataclass
class DeskRuntime:
    model: AgentModel
    tools: ToolRegistry
    max_json_repairs: int
    max_tool_rounds: int


def tool_traces(logs: list[dict[str, Any]]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for log in logs:
        if log["ok"]:
            head = log["name"] if log["attempt"] == 1 else f"{log['name']} attempt {log['attempt']}"
            message = f"{head}: {log['detail']}"
        else:
            message = f"{log['name']} attempt {log['attempt']} failed: {log['detail']}"
        events.append({"agent": log["agent"], "event": "tool", "message": message})
    return events


def sum_usage(events: list[dict[str, Any]]) -> Usage:
    total = empty_usage()
    for event in events:
        total = add_usage(
            total,
            {
                "prompt_tokens": event["prompt_tokens"],
                "completion_tokens": event["completion_tokens"],
                "total_tokens": event["prompt_tokens"] + event["completion_tokens"],
                "estimated_cost_usd": event["estimated_cost_usd"],
            },
        )
    return total


def _usage_event(agent: str, response) -> dict[str, Any]:
    cost, label = priced(response)
    return {
        "agent": agent,
        "model": response.model_name,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "estimated_cost_usd": cost,
        "rate_label": label,
    }


def call_model_json(
    runtime: DeskRuntime,
    agent: str,
    payload: dict[str, Any],
    validate: Validator,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[TraceEvent]]:
    """Call the model, and on bad JSON ask again with the parse error attached."""
    system = system_prompt(agent)
    format_error: str | None = None
    usage_events: list[dict[str, Any]] = []
    trace: list[TraceEvent] = []
    for _ in range(runtime.max_json_repairs + 1):
        response = runtime.model.complete(
            agent=agent,
            system=system,
            payload={**payload, "format_error": format_error},
        )
        usage_events.append(_usage_event(agent, response))
        try:
            data = parse_json_object(response.text)
            validate(data)
        except JSONParseError as exc:
            format_error = str(exc)
            trace.append(
                {
                    "agent": agent,
                    "event": "retry",
                    "message": (
                        f"Invalid JSON from the model "
                        f"({response.prompt_tokens}+{response.completion_tokens} tokens). {exc}"
                    ),
                }
            )
            continue
        return data, usage_events, trace
    raise ModelOutputError(
        format_error or "Invalid JSON from the model.",
        usage_events=usage_events,
        trace=trace,
    )


def _with_usage(update: dict[str, Any], usage_events: list[dict[str, Any]]) -> dict[str, Any]:
    if usage_events:
        update["usage_events"] = usage_events
        update["usage"] = sum_usage(usage_events)
    return update


def _require_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    calls = data.get("tool_calls")
    if not isinstance(calls, list):
        raise JSONParseError("tool_calls must be a list.")
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not call["name"]:
            raise JSONParseError("Each tool call needs a name.")
        args = call.get("args", {})
        if not isinstance(args, dict):
            raise JSONParseError("Tool call args must be an object.")
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise JSONParseError("notes must be a string.")
    return calls


def make_supervisor(max_review_rounds: int):
    def supervisor(state: EnquiryState) -> dict[str, Any]:
        nxt = decide(state, max_review_rounds=max_review_rounds)
        update: dict[str, Any] = {
            "next_agent": nxt,
            "trace": [{"agent": "supervisor", "event": "route", "message": f"Next is {nxt}."}],
        }
        if state["status"] == "received":
            update["status"] = "in_progress"
        if nxt == "approval":
            update["status"] = "awaiting_approval"
            update["recommended_action"] = recommend(state)
        return update

    return supervisor


def make_router(runtime: DeskRuntime):
    def validate(data: dict[str, Any]) -> None:
        if data.get("intent") not in INTENTS:
            raise JSONParseError(
                "intent must be viewing, maintenance, application, spam, or unknown."
            )
        if data.get("urgency") not in {"normal", "urgent"}:
            raise JSONParseError("urgency must be normal or urgent.")
        if not isinstance(data.get("listing_query", ""), str):
            raise JSONParseError("listing_query must be a string.")
        if not isinstance(data.get("reasoning", ""), str):
            raise JSONParseError("reasoning must be a string.")

    def router(state: EnquiryState) -> dict[str, Any]:
        enquiry = state["enquiry"]
        text = f"{enquiry['subject']}\n{enquiry['body']}"
        result, logs = runtime.tools.call("router", "match_intent_hints", {"text": text})
        hints = list(result.get("matches") or [])
        data, usage_events, trace = call_model_json(
            runtime,
            "router",
            {
                "enquiry": enquiry,
                "hints": hints,
                "tools": runtime.tools.catalog(),
            },
            validate,
        )
        intent = str(data["intent"])
        listing_query = str(data.get("listing_query") or "").strip()[:80]
        reasoning = str(data.get("reasoning") or "").strip()
        decision = f"Classified as {intent}, urgency {data['urgency']}. {reasoning}"
        if listing_query:
            decision += f" Property hint: {listing_query}."
        update: dict[str, Any] = {
            "intent": intent,
            "urgency": str(data["urgency"]),
            "listing_query": listing_query,
            "router_reasoning": reasoning,
            "hints": hints,
            "trace": [
                *tool_traces(logs),
                *trace,
                {"agent": "router", "event": "decision", "message": decision},
            ],
            "tool_events": logs,
        }
        if intent not in RESEARCH_INTENTS:
            update["research_status"] = "skipped"
        return _with_usage(update, usage_events)

    return router


def make_research(runtime: DeskRuntime):
    def validate(data: dict[str, Any]) -> None:
        _require_tool_calls(data)

    def research(state: EnquiryState) -> dict[str, Any]:
        tool_results: list[dict[str, Any]] = []
        trace: list[TraceEvent] = []
        usage_events: list[dict[str, Any]] = []
        tool_events: list[dict[str, Any]] = []
        notes = ""
        for _ in range(runtime.max_tool_rounds):
            data, turn_usage, turn_trace = call_model_json(
                runtime,
                "research",
                {
                    "enquiry": state["enquiry"],
                    "intent": state["intent"],
                    "listing_query": state["listing_query"],
                    "tool_results": tool_results,
                    "tools": runtime.tools.catalog(),
                },
                validate,
            )
            usage_events.extend(turn_usage)
            trace.extend(turn_trace)
            notes = str(data.get("notes") or "")
            calls = data["tool_calls"]
            if not calls:
                break
            for call in calls:
                args = dict(call.get("args") or {})
                result, logs = runtime.tools.call("research", call["name"], args)
                tool_events.extend(logs)
                trace.extend(tool_traces(logs))
                tool_results.append({"name": call["name"], "args": args, "result": result})
        else:
            notes = notes or "Stopped at the tool round limit."

        facts = facts_from_tool_results(tool_results)
        status = _research_status(state["intent"], facts)
        trace.append(
            {
                "agent": "research",
                "event": "note",
                "message": f"{notes} Facts recorded: {len(facts)}. Status {status}.",
            }
        )
        return _with_usage(
            {
                "facts": facts,
                "research_status": status,
                "trace": trace,
                "tool_events": tool_events,
            },
            usage_events,
        )

    return research


def _research_status(intent: str | None, facts: list[dict[str, Any]]) -> str:
    if intent == "maintenance":
        if any(fact.get("source") == "find_repair" for fact in facts):
            return "ok"
        return "holding"
    if intent in {"viewing", "application"}:
        if any(fact.get("source") == "get_listing" for fact in facts):
            return "ok"
        return "holding"
    return "skipped"


def make_drafter(runtime: DeskRuntime):
    def validate(data: dict[str, Any]) -> None:
        calls = _require_tool_calls(data)
        draft = data.get("draft", "")
        if not isinstance(draft, str):
            raise JSONParseError("draft must be a string.")
        if not calls and not draft.strip():
            raise JSONParseError("Return a draft, or request a tool.")

    def drafter(state: EnquiryState) -> dict[str, Any]:
        tool_results: list[dict[str, Any]] = []
        trace: list[TraceEvent] = []
        usage_events: list[dict[str, Any]] = []
        tool_events: list[dict[str, Any]] = []
        draft = ""
        notes = ""
        for _ in range(runtime.max_tool_rounds):
            data, turn_usage, turn_trace = call_model_json(
                runtime,
                "drafter",
                {
                    "enquiry": state["enquiry"],
                    "intent": state["intent"],
                    "research_status": state["research_status"],
                    "facts": state["facts"],
                    "review_issues": state["review_issues"],
                    "draft_version": state["draft_version"],
                    "tool_results": tool_results,
                    "tools": runtime.tools.catalog(),
                },
                validate,
            )
            usage_events.extend(turn_usage)
            trace.extend(turn_trace)
            notes = str(data.get("notes") or "")
            calls = data["tool_calls"]
            if calls:
                for call in calls:
                    args = dict(call.get("args") or {})
                    result, logs = runtime.tools.call("drafter", call["name"], args)
                    tool_events.extend(logs)
                    trace.extend(tool_traces(logs))
                    tool_results.append({"name": call["name"], "args": args, "result": result})
                continue
            draft = str(data.get("draft") or "").strip()
            break
        if not draft:
            raise ModelOutputError(
                "The drafter finished without a reply.",
                usage_events=usage_events,
                trace=trace,
            )
        round_number = state["draft_version"] + 1
        trace.append(
            {
                "agent": "drafter",
                "event": "draft",
                "message": f"Draft round {round_number}. {notes}".strip(),
            }
        )
        return _with_usage(
            {
                "draft": draft,
                "drafts": [draft],
                "draft_version": round_number,
                "trace": trace,
                "tool_events": tool_events,
            },
            usage_events,
        )

    return drafter


def make_reviewer(runtime: DeskRuntime):
    def validate(data: dict[str, Any]) -> None:
        if not isinstance(data.get("note", ""), str):
            raise JSONParseError("note must be a string.")

    def reviewer(state: EnquiryState) -> dict[str, Any]:
        result, logs = runtime.tools.call(
            "reviewer",
            "audit_reply",
            {
                "draft": state["draft"],
                "facts": state["facts"],
                "intent": state["intent"],
            },
        )
        trace: list[TraceEvent] = tool_traces(logs)
        usage_events: list[dict[str, Any]] = []
        try:
            data, turn_usage, turn_trace = call_model_json(
                runtime,
                "reviewer",
                {
                    "intent": state["intent"],
                    "draft": state["draft"],
                    "audit": {"passed": result.get("passed"), "issues": result.get("issues", [])},
                },
                validate,
            )
            usage_events.extend(turn_usage)
            trace.extend(turn_trace)
            note = str(data.get("note") or "").strip() or "See the audit result."
        except ModelOutputError as exc:
            usage_events.extend(exc.usage_events)
            trace.extend(exc.trace)
            note = "The model note could not be read. The audit result stands."

        passed = bool(result.get("passed"))
        issues = [str(issue) for issue in result.get("issues") or []]
        record = {
            "round": state["review_rounds"] + 1,
            "passed": passed,
            "issues": issues,
            "note": note,
        }
        if passed:
            message = f"Review passed. {note}"
        else:
            message = f"Review failed. {note}"
        trace.append({"agent": "reviewer", "event": "decision", "message": message})
        return _with_usage(
            {
                "review_passed": passed,
                "review_issues": issues,
                "review_rounds": state["review_rounds"] + 1,
                "reviewed_version": state["draft_version"],
                "review_history": [record],
                "trace": trace,
                "tool_events": logs,
            },
            usage_events,
        )

    return reviewer


def approval_node(state: EnquiryState) -> dict[str, Any]:
    """Pause until a person approves or rejects. The node restarts when resumed."""
    recommended = recommend(state)
    decision = interrupt(
        {
            "enquiry_id": state["enquiry"]["enquiry_id"],
            "recommended_action": recommended,
            "review_passed": state["review_passed"],
            "review_issues": state["review_issues"],
            "fatal_error": state["fatal_error"],
            "draft": state["draft"],
        }
    )
    if isinstance(decision, str):
        decision = {"action": decision, "note": ""}
    if not isinstance(decision, dict):
        decision = {"action": "reject", "note": "Decision was not an object."}
    action = str(decision.get("action", "")).strip().lower()
    note = str(decision.get("note", "")).strip()
    if action not in {"approve", "reject"}:
        note = f"Unrecognised decision recorded as reject. {note}".strip()
        action = "reject"
    return {
        "approval": {"action": action, "note": note},
        "recommended_action": recommended,
        "status": "decision_recorded",
    }


def make_finalize(runtime: DeskRuntime):
    def finalize(state: EnquiryState) -> dict[str, Any]:
        approval = state["approval"] or {"action": "reject", "note": ""}
        recommended = recommend(state)
        outcome = resolve_outcome(recommended, approval["action"])
        recording: TraceEvent = {
            "agent": "finalize",
            "event": "decision",
            "message": f"Recorded outcome {outcome}.",
        }
        document = {
            "enquiry_id": state["enquiry"]["enquiry_id"],
            "enquiry": state["enquiry"],
            "model_name": state["model_name"],
            "intent": state["intent"],
            "urgency": state["urgency"],
            "research_status": state["research_status"],
            "facts": state["facts"],
            "drafts": state["drafts"],
            "draft": state["draft"],
            "review_history": state["review_history"],
            "recommended_action": recommended,
            "approval": approval,
            "outcome": outcome,
            "fatal_error": state["fatal_error"],
            "usage": state["usage"],
            "usage_events": state["usage_events"],
            "trace": [*state["trace"], recording],
            "tool_events": state["tool_events"],
        }
        result, logs = runtime.tools.call("finalize", "record_decision", {"document": document})
        trace = [recording, *tool_traces(logs)]
        path = result.get("path") if result.get("ok") else None
        if not result.get("ok"):
            trace.append(
                {
                    "agent": "finalize",
                    "event": "error",
                    "message": str(result.get("error") or "Could not write the run log."),
                }
            )
        return {
            "outcome": outcome,
            "status": "closed",
            "recommended_action": recommended,
            "log_path": path,
            "trace": trace,
            "tool_events": logs,
        }

    return finalize


def handle_agent_error(state: EnquiryState, error: NodeError) -> Command:
    """Send an exhausted agent failure to the same human checkpoint."""
    message = public_error(error.error)
    carried_usage = list(getattr(error.error, "usage_events", []) or [])
    carried_trace = list(getattr(error.error, "trace", []) or [])
    update: dict[str, Any] = {
        "fatal_error": f"{error.node} failed: {message}",
        "recommended_action": "escalate",
        "status": "awaiting_approval",
        "trace": [
            *carried_trace,
            {
                "agent": "supervisor",
                "event": "error",
                "message": (
                    f"{error.node} failed after retries. The case is waiting for a person. {message}"
                ),
            },
        ],
    }
    if carried_usage:
        update["usage_events"] = carried_usage
        update["usage"] = sum_usage(carried_usage)
    return Command(goto="approval", update=update)
