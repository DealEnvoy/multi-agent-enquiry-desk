"""Tools the agents call.

Reads come from the fictional catalogue in data.py. The only write is
record_decision, which stores the run log on disk. Nothing is emailed.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from enquiry_desk.data import (
    HINT_PHRASES,
    LISTINGS,
    POLICIES,
    REPAIRS,
    describe_listing,
    find_listing,
)
from enquiry_desk.errors import TransientToolError, public_error

logger = logging.getLogger("enquiry_desk.tools")

Handler = Callable[[dict[str, Any]], dict[str, Any]]

BANNED_PHRASES = (
    "we can waive",
    "waive the deposit",
    "move in without one",
    "no deposit",
    "admin fee",
    "referencing fee",
    "skip referencing",
    "guaranteed",
)
NEGATION_WORDS = {"not", "no", "never", "cannot", "can't", "unable", "don't", "dont"}
CLOCK = re.compile(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b")
CASE_ID = re.compile(r"\bR-\d+\b")
CUSTOMER_INTENTS = frozenset({"viewing", "maintenance", "application"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Handler


def _phrase_in(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def match_intent_hints(args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text", "")).lower()
    matches = []
    for intent, phrases in HINT_PHRASES:
        found = [phrase for phrase in phrases if _phrase_in(text, phrase)]
        if found:
            matches.append({"intent": intent, "phrases": found})
    return {"ok": True, "matches": matches}


def search_listings(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip().lower()
    if not query:
        return {"ok": False, "error": "query is required."}
    hits = []
    for listing in LISTINGS:
        blob = f"{listing['listing_id']} {listing['title']} {listing['address']}".lower()
        parts = [part for part in query.split() if len(part) > 2]
        if query in blob or (parts and all(part in blob for part in parts)):
            hits.append(
                {
                    "listing_id": listing["listing_id"],
                    "title": listing["title"],
                    "status": listing["status"],
                }
            )
    return {"ok": True, "listings": hits}


def get_listing(args: dict[str, Any]) -> dict[str, Any]:
    listing_id = str(args.get("listing_id", "")).strip()
    listing = find_listing(listing_id)
    if listing is None:
        return {"ok": False, "error": f"No listing with id {listing_id!r}."}
    return {"ok": True, "listing": copy.deepcopy(listing)}


def get_viewing_slots(args: dict[str, Any]) -> dict[str, Any]:
    listing_id = str(args.get("listing_id", "")).strip()
    listing = find_listing(listing_id)
    if listing is None:
        return {"ok": False, "error": f"No listing with id {listing_id!r}."}
    return {"ok": True, "listing_id": listing_id, "slots": list(listing["slots"])}


def find_repair(args: dict[str, Any]) -> dict[str, Any]:
    email = str(args.get("email", "")).strip().lower()
    if not email:
        return {"ok": False, "error": "email is required."}
    for case in REPAIRS:
        if case["sender_email"].lower() != email:
            continue
        listing = find_listing(case["listing_id"])
        address = listing["address"] if listing else case["listing_id"]
        payload = copy.deepcopy(case)
        payload["address"] = address
        return {"ok": True, "case": payload}
    return {
        "ok": True,
        "case": None,
        "text": "No open repair case for this email address.",
    }


def get_policy_snippet(args: dict[str, Any]) -> dict[str, Any]:
    topic = str(args.get("topic", "")).strip().lower()
    text = POLICIES.get(topic)
    if text is None:
        return {
            "ok": False,
            "error": f"Unknown policy topic {topic!r}.",
            "topics": sorted(POLICIES),
        }
    return {"ok": True, "topic": topic, "text": text}


def _negated(text: str, index: int) -> bool:
    words = re.findall(r"[a-z']+", text[:index])[-4:]
    return any(word in NEGATION_WORDS for word in words)


def has_unnegated(text: str, phrase: str) -> bool:
    lowered = text.lower()
    start = 0
    while True:
        index = lowered.find(phrase, start)
        if index < 0:
            return False
        if not _negated(lowered, index):
            return True
        start = index + len(phrase)


def pound_amounts(text: str) -> list[str]:
    return [match.replace(",", "") for match in re.findall(r"£\s?([0-9][0-9,]*)", text)]


def audit_reply(draft: str, facts: list[dict[str, Any]], intent: str) -> dict[str, Any]:
    """Check a draft against tool facts and a small banned-phrase list.

    The tool result is the decision. A model note can explain it, and cannot
    overrule it. Negated refusals such as "we cannot waive the deposit" are
    allowed. The check is a desk rule, not a legal opinion.
    """
    issues: list[str] = []
    text = draft or ""
    if not text.strip():
        issues.append("Draft is empty.")
        return {"passed": False, "issues": issues}

    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if has_unnegated(lowered, phrase):
            issues.append(f"Banned wording without a clear refusal: '{phrase}'.")

    fact_blob = "\n".join(str(fact.get("text", "")) for fact in facts)
    known_amounts = set(pound_amounts(fact_blob))
    for amount in pound_amounts(text):
        if amount not in known_amounts:
            issues.append(f"Draft mentions £{amount}, which is not in the tool facts.")

    known_times = set(CLOCK.findall(fact_blob))
    for clock in CLOCK.findall(text):
        if clock not in known_times:
            issues.append(f"Draft mentions {clock}, which is not in the tool facts.")

    if intent in {"spam", "unknown"} and not lowered.startswith("internal note"):
        issues.append("This intent must stay an internal note.")
    if intent in CUSTOMER_INTENTS and lowered.startswith("internal note"):
        issues.append("A customer enquiry needs a customer reply or a holding reply.")

    if intent == "viewing":
        slot_blob = " ".join(
            str(fact.get("text", "")) for fact in facts if fact.get("source") == "get_viewing_slots"
        )
        slot_times = CLOCK.findall(slot_blob)
        if slot_times and not any(clock in text for clock in slot_times):
            issues.append("Viewing reply does not include a slot from the tools.")

    if intent == "maintenance":
        case_ids = CASE_ID.findall(fact_blob)
        if case_ids and not any(case_id in text for case_id in case_ids):
            issues.append("Maintenance reply should name the open repair case.")

    return {"passed": not issues, "issues": issues}


def audit_reply_tool(args: dict[str, Any]) -> dict[str, Any]:
    result = audit_reply(
        draft=str(args.get("draft") or ""),
        facts=list(args.get("facts") or []),
        intent=str(args.get("intent") or ""),
    )
    return {"ok": True, **result}


def summarise(name: str, result: dict[str, Any]) -> str:
    if not result.get("ok", True):
        return str(result.get("error", "failed"))
    if name == "match_intent_hints":
        matches = result.get("matches") or []
        if not matches:
            return "no intent hints"
        parts = []
        for match in matches:
            phrases = ", ".join(match["phrases"])
            parts.append(f"{match['intent']} ({phrases})")
        return "matched " + "; ".join(parts)
    if name == "search_listings":
        listings = result.get("listings") or []
        if not listings:
            return "no listings"
        return "; ".join(f"{item['listing_id']} {item['title']} ({item['status']})" for item in listings)
    if name == "get_listing":
        listing = result["listing"]
        return f"{listing['listing_id']}, rent £{listing['rent_pcm_gbp']:,} pcm"
    if name == "get_viewing_slots":
        slots = result.get("slots") or []
        return "; ".join(slots) if slots else "no slots held"
    if name == "find_repair":
        case = result.get("case")
        if not case:
            return result.get("text") or "no open repair"
        return f"{case['case_id']} at {case['address']}, window {case['window']}"
    if name == "get_policy_snippet":
        return f"topic {result['topic']}"
    if name == "audit_reply":
        if result.get("passed"):
            return "passed"
        count = len(result.get("issues") or [])
        return f"failed, {count} issues"
    if name == "record_decision":
        path = result.get("path")
        return f"stored {path}" if path else "stored in memory"
    return "ok"


def facts_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build facts from successful tool payloads. Model prose is not a fact source."""
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(fact: dict[str, Any]) -> None:
        key = (str(fact.get("source")), str(fact.get("text")))
        if key in seen:
            return
        seen.add(key)
        facts.append(fact)

    for item in tool_results:
        result = item["result"]
        if not result.get("ok"):
            continue
        name = item["name"]
        if name == "get_listing":
            listing = result["listing"]
            add(
                {
                    "source": "get_listing",
                    "listing_id": listing["listing_id"],
                    "address": listing["address"],
                    "bedrooms": listing["bedrooms"],
                    "rent_pcm_gbp": listing["rent_pcm_gbp"],
                    "available_display": listing["available_display"],
                    "pets": listing["pets"],
                    "status": listing["status"],
                    "text": describe_listing(listing),
                }
            )
        elif name == "get_viewing_slots":
            slots = list(result.get("slots") or [])
            text = (
                "Viewing slots: " + "; ".join(slots)
                if slots
                else "No viewing slots are held on the listing."
            )
            add(
                {
                    "source": "get_viewing_slots",
                    "listing_id": result.get("listing_id"),
                    "slots": slots,
                    "text": text,
                }
            )
        elif name == "find_repair":
            case = result.get("case")
            if case is None:
                text = str(result.get("text") or "No open repair case for this email address.")
                add({"source": "find_repair", "case_id": None, "text": text})
            else:
                text = (
                    f"Repair {case['case_id']} for {case['issue']} at {case['address']}. "
                    f"Booked window {case['window']}."
                )
                add(
                    {
                        "source": "find_repair",
                        "case_id": case["case_id"],
                        "issue": case["issue"],
                        "address": case["address"],
                        "window": case["window"],
                        "text": text,
                    }
                )
    return facts


def default_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "match_intent_hints",
            "Find intent phrases in the enquiry text. Does not decide the intent by itself.",
            match_intent_hints,
        ),
        ToolSpec(
            "search_listings",
            "Find listing ids and titles. Does not return the rent.",
            search_listings,
        ),
        ToolSpec(
            "get_listing",
            "Return one listing, including the rent. Unknown ids return ok false.",
            get_listing,
        ),
        ToolSpec(
            "get_viewing_slots",
            "Return viewing times already held for a listing id.",
            get_viewing_slots,
        ),
        ToolSpec(
            "find_repair",
            "Look up an open repair by the sender email. Read only.",
            find_repair,
        ),
        ToolSpec(
            "get_policy_snippet",
            "Return the desk policy text for a topic.",
            get_policy_snippet,
        ),
        ToolSpec(
            "audit_reply",
            "Check a draft against facts and banned wording. The passed field is the decision.",
            audit_reply_tool,
        ),
    ]


class ToolRegistry:
    """Run a tool and retry transient failures inside the same node.

    A clean not-found result is not a transient failure. The agent sees it and
    can choose another tool. Unexpected exceptions are returned as a failed
    result so one bad call does not throw away the rest of the workflow.
    """

    def __init__(
        self,
        specs: list[ToolSpec],
        *,
        max_attempts: int = 3,
    ) -> None:
        self.max_attempts = max_attempts
        self._specs = {spec.name: spec for spec in specs}

    def catalog(self) -> list[dict[str, str]]:
        return [{"name": spec.name, "description": spec.description} for spec in self._specs.values()]

    def call(self, agent: str, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        spec = self._specs.get(name)
        safe_args = dict(args)
        if spec is None:
            result = {"ok": False, "error": f"Unknown tool {name!r}."}
            return result, [self._event(agent, name, safe_args, ok=False, attempt=1, detail=result["error"])]

        logs: list[dict[str, Any]] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = spec.handler(dict(safe_args))
            except TransientToolError as exc:
                detail = str(exc)
                logs.append(self._event(agent, name, safe_args, ok=False, attempt=attempt, detail=detail))
                logger.info("tool %s attempt %s failed: %s", name, attempt, detail)
                if attempt == self.max_attempts:
                    return {"ok": False, "error": detail}, logs
                continue
            except Exception as exc:  # noqa: BLE001  A tool bug becomes a failed result.
                detail = public_error(exc)
                logs.append(self._event(agent, name, safe_args, ok=False, attempt=attempt, detail=detail))
                return {"ok": False, "error": detail}, logs

            detail = summarise(name, result)
            business_ok = bool(result.get("ok", True))
            logs.append(
                self._event(agent, name, safe_args, ok=business_ok, attempt=attempt, detail=detail)
            )
            return result, logs
        raise RuntimeError(f"Tool {name} ended without a result.")

    @staticmethod
    def _event(
        agent: str,
        name: str,
        args: dict[str, Any],
        *,
        ok: bool,
        attempt: int,
        detail: str,
    ) -> dict[str, Any]:
        return {
            "agent": agent,
            "name": name,
            "ok": ok,
            "attempt": attempt,
            "detail": detail,
            "args": args,
        }


def make_record_decision(log_dir: Path | None) -> ToolSpec:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        document = dict(args["document"])
        document["decided_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        enquiry_id = str(document["enquiry_id"])
        if log_dir is None:
            return {"ok": True, "path": None, "document": document}
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", enquiry_id)
        if not safe_name or ".." in safe_name:
            return {"ok": False, "error": "enquiry_id is not a safe file name."}
        path = log_dir / f"{safe_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": True, "path": str(path)}

    return ToolSpec(
        "record_decision",
        "Write the decision, draft, trace, and token totals to the local run log.",
        handler,
    )


def make_registry(
    log_dir: Path | None = None,
    *,
    wrappers: dict[str, Callable[[Handler], Handler]] | None = None,
    max_attempts: int = 3,
) -> ToolRegistry:
    specs = default_tool_specs()
    if wrappers:
        wrapped: list[ToolSpec] = []
        for spec in specs:
            wrapper = wrappers.get(spec.name)
            handler = wrapper(spec.handler) if wrapper else spec.handler
            wrapped.append(ToolSpec(spec.name, spec.description, handler))
        specs = wrapped
    specs.append(make_record_decision(log_dir))
    return ToolRegistry(specs, max_attempts=max_attempts)
