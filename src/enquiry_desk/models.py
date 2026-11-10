"""Model adapters.

The default model is a deterministic stub. It returns the same JSON the live
models are asked to return, so the graph does not grow a second code path.
Set ENQUIRY_DESK_MODEL to anthropic or openai to use a live model. Keys in the
environment do nothing until that switch is set.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from enquiry_desk.cost import estimate_cost_usd, estimate_tokens
from enquiry_desk.data import (
    TOPIC_FOR_INTENT,
    URGENT_PHRASES,
    listing_query_from_text,
    money,
    preferred_intent,
    slugify_listing_id,
)
from enquiry_desk.errors import ConfigError


@dataclass
class ModelResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model_name: str


class AgentModel(Protocol):
    name: str

    def complete(self, *, agent: str, system: str, payload: dict[str, Any]) -> ModelResponse:
        """Return one model reply for an agent."""


def _response(model_name: str, system: str, payload: dict[str, Any], body: dict[str, Any]) -> ModelResponse:
    text = json.dumps(body, ensure_ascii=False)
    prompt = f"{system}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    return ModelResponse(
        text=text,
        prompt_tokens=estimate_tokens(prompt),
        completion_tokens=estimate_tokens(text),
        model_name=model_name,
    )


def policy_topic(payload: dict[str, Any]) -> str:
    intent = str(payload.get("intent") or "unknown")
    if payload.get("research_status") == "holding" and intent in {"viewing", "application", "maintenance"}:
        return "holding"
    return TOPIC_FOR_INTENT.get(intent, "holding")


class StubModel:
    """Offline stand-in for the four agent calls.

    The first application draft agrees to waive a deposit when the sender asks
    and no review issues exist yet. That mistake is scripted so the review loop
    can be seen without an API key. The second pass, which receives the review
    issues, refuses the waiver. A live model is not scripted this way. The
    audit tool still blocks the bad wording.
    """

    name = "stub"

    def complete(self, *, agent: str, system: str, payload: dict[str, Any]) -> ModelResponse:
        if agent == "router":
            body = self._route(payload)
        elif agent == "research":
            body = self._research(payload)
        elif agent == "drafter":
            body = self._draft(payload)
        elif agent == "reviewer":
            body = self._review(payload)
        else:
            raise ConfigError(f"No offline behaviour for agent {agent!r}.")
        return _response(self.name, system, payload, body)

    def _route(self, payload: dict[str, Any]) -> dict[str, Any]:
        matches = list(payload.get("hints") or [])
        intent = preferred_intent(matches)
        phrases = [phrase for match in matches for phrase in match.get("phrases", [])]
        urgency = "urgent" if any(phrase in URGENT_PHRASES for phrase in phrases) else "normal"
        enquiry = payload["enquiry"]
        text = f"{enquiry['subject']}\n{enquiry['body']}"
        if intent == "unknown":
            reasoning = "The hint tool found no customer, repair, or sales phrase."
        else:
            reasoning = f"The hint tool matched {intent} from: {', '.join(phrases)}."
        return {
            "intent": intent,
            "urgency": urgency,
            "listing_query": listing_query_from_text(text),
            "reasoning": reasoning,
        }

    def _research(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = payload.get("intent")
        results = list(payload.get("tool_results") or [])
        enquiry = payload["enquiry"]

        def ran(name: str, *, ok: bool | None = None) -> bool:
            for item in results:
                if item["name"] != name:
                    continue
                if ok is None or bool(item["result"].get("ok")) is ok:
                    return True
            return False

        if intent == "maintenance":
            if not ran("find_repair"):
                return {
                    "tool_calls": [
                        {"name": "find_repair", "args": {"email": enquiry["sender_email"]}},
                    ],
                    "notes": "Look up the open repair before saying anything about a visit.",
                }
            return {"tool_calls": [], "notes": "The repair lookup is in the tool results."}

        if intent not in {"viewing", "application"}:
            return {"tool_calls": [], "notes": "This intent does not need a listing lookup."}

        if not ran("get_listing") and not ran("search_listings"):
            guessed = slugify_listing_id(str(payload.get("listing_query") or "unknown"))
            return {
                "tool_calls": [{"name": "get_listing", "args": {"listing_id": guessed}}],
                "notes": "Try the property hint as a listing id.",
            }

        if ran("get_listing", ok=False) and not ran("search_listings"):
            query = str(payload.get("listing_query") or enquiry["subject"])
            return {
                "tool_calls": [{"name": "search_listings", "args": {"query": query}}],
                "notes": "That listing id was not found. Search the catalogue.",
            }

        listings: list[dict[str, Any]] = []
        for item in results:
            if item["name"] == "search_listings" and item["result"].get("ok"):
                listings = list(item["result"].get("listings") or [])
        if not listings:
            return {"tool_calls": [], "notes": "The catalogue search returned no listing."}

        listing_id = listings[0]["listing_id"]
        calls: list[dict[str, Any]] = []
        if not ran("get_listing", ok=True):
            calls.append({"name": "get_listing", "args": {"listing_id": listing_id}})
        if intent == "viewing" and not ran("get_viewing_slots", ok=True):
            calls.append({"name": "get_viewing_slots", "args": {"listing_id": listing_id}})
        if calls:
            return {"tool_calls": calls, "notes": "Load the catalogue records this reply is allowed to use."}
        return {"tool_calls": [], "notes": "The catalogue records this reply needs are loaded."}

    def _draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        results = list(payload.get("tool_results") or [])
        loaded = any(
            item["name"] == "get_policy_snippet" and item["result"].get("ok") for item in results
        )
        if not loaded:
            return {
                "tool_calls": [
                    {"name": "get_policy_snippet", "args": {"topic": policy_topic(payload)}},
                ],
                "draft": "",
                "notes": "Fetch the policy snippet before writing.",
            }
        draft, notes = render_draft(payload)
        return {"tool_calls": [], "draft": draft, "notes": notes}

    def _review(self, payload: dict[str, Any]) -> dict[str, Any]:
        audit = payload.get("audit") or {}
        issues = list(audit.get("issues") or [])
        if audit.get("passed"):
            note = "The audit found no issues."
        else:
            note = f"The audit found {len(issues)} issue(s)."
        return {"note": note}


def render_draft(payload: dict[str, Any]) -> tuple[str, str]:
    enquiry = payload["enquiry"]
    intent = str(payload.get("intent") or "unknown")
    name = enquiry["sender_name"]
    facts = list(payload.get("facts") or [])
    review_issues = list(payload.get("review_issues") or [])
    body = enquiry["body"].lower()

    if intent == "spam":
        return (
            "Internal note: do not reply. The message is unsolicited sales, not a customer enquiry. Archive it.",
            "Internal note only.",
        )
    if intent == "unknown":
        return (
            (
                "Internal note: the enquiry does not say what the sender needs. "
                "Do not guess. A person should ask them to clarify or close the message."
            ),
            "Internal note only.",
        )
    if payload.get("research_status") == "holding" or _missing_customer_facts(intent, facts):
        return (
            (
                f"Hello {name},\n\n"
                "Thank you for your message. A colleague will check the details and reply. "
                "This note does not confirm a rent, a viewing time, a repair window, or a tenancy.\n\n"
                "Kind regards,\n"
                "Hale & Quay Lettings"
            ),
            "Holding reply, because the tool facts are missing.",
        )
    if intent == "application" and "waive" in body and not review_issues:
        return (
            (
                f"Hello {name},\n\n"
                "We can waive the deposit so you can move in without one.\n\n"
                "Kind regards,\n"
                "Hale & Quay Lettings"
            ),
            (
                "Offline stub: this first draft deliberately agrees to a deposit waiver "
                "so the review loop is visible without an API key."
            ),
        )
    if intent == "application":
        listing = _listing_fact(facts)
        return (
            (
                f"Hello {name},\n\n"
                f"Thank you for your interest in {listing['address']}. "
                f"The rent is {money(listing['rent_pcm_gbp'])} pcm. "
                + (
                    f"It is available {listing['available_display']}. "
                    if listing.get("available_display")
                    else ""
                )
                + "We cannot waive the deposit. A holding deposit is capped at one week's rent. "
                "Referencing and a Right to Rent check come before any tenancy is offered.\n\n"
                "Kind regards,\n"
                "Hale & Quay Lettings"
            ),
            "Refused the deposit waiver and used the listing rent.",
        )
    if intent == "maintenance":
        repair = next((fact for fact in facts if fact.get("source") == "find_repair" and fact.get("case_id")), None)
        if repair is None:
            return (
                (
                    f"Hello {name},\n\n"
                    "Thank you for reporting this. We have logged the repair and a colleague will "
                    "confirm any appointment. No visit time is booked yet.\n\n"
                    "Kind regards,\n"
                    "Hale & Quay Lettings"
                ),
                "No open repair case was on file.",
            )
        return (
            (
                f"Hello {name},\n\n"
                f"We have repair {repair['case_id']} open ({repair['issue']}) at {repair['address']}. "
                f"The booked window is {repair['window']}. "
                "We cannot offer an earlier visit from this desk.\n\n"
                "Kind regards,\n"
                "Hale & Quay Lettings"
            ),
            "Confirmed the booked repair window and did not offer an earlier visit.",
        )
    if intent == "viewing":
        listing = _listing_fact(facts)
        slots = next((fact for fact in facts if fact.get("source") == "get_viewing_slots" and fact.get("slots")), None)
        if slots:
            slot_sentence = "The open slots are " + " and ".join(slots["slots"]) + "."
        else:
            slot_sentence = "We will confirm a viewing time separately."
        pets = "Pets are not accepted. " if listing.get("pets") is False else ""
        available = (
            f" It is available {listing['available_display']}."
            if listing.get("available_display")
            else ""
        )
        return (
            (
                f"Hello {name},\n\n"
                f"Thank you for asking about {listing['address']}. "
                f"It has {listing['bedrooms']} bedrooms and the rent is {money(listing['rent_pcm_gbp'])} pcm."
                f"{available} {slot_sentence} Viewings are free. {pets}"
                "A viewing is not an offer of a tenancy.\n\n"
                "Kind regards,\n"
                "Hale & Quay Lettings"
            ),
            "Used the listing rent and the slots from the tools.",
        )
    return (
        (
            f"Hello {name},\n\n"
            "Thank you for your message. A colleague will reply shortly.\n\n"
            "Kind regards,\n"
            "Hale & Quay Lettings"
        ),
        "No specialised template matched.",
    )


def _listing_fact(facts: list[dict[str, Any]]) -> dict[str, Any]:
    for fact in facts:
        if fact.get("source") == "get_listing":
            return fact
    raise KeyError("listing fact")


def _missing_customer_facts(intent: str, facts: list[dict[str, Any]]) -> bool:
    if intent in {"viewing", "application"}:
        return not any(fact.get("source") == "get_listing" for fact in facts)
    if intent == "maintenance":
        return not any(fact.get("source") == "find_repair" for fact in facts)
    return False


class LiveModel:
    """Thin adapter over the Anthropic or OpenAI SDK.

    The SDK is imported on the first call so the default install stays small
    and tests never open a connection.
    """

    def __init__(self, *, provider: str, model_name: str, api_key: str) -> None:
        self.provider = provider
        self.name = model_name
        self.api_key = api_key

    def complete(self, *, agent: str, system: str, payload: dict[str, Any]) -> ModelResponse:
        user = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if self.provider == "anthropic":
            return self._anthropic(system, user)
        if self.provider == "openai":
            return self._openai(system, user)
        raise ConfigError(f"Unknown live provider {self.provider!r}.")

    def _anthropic(self, system: str, user: str) -> ModelResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise ConfigError(
                "The anthropic package is not installed. Install it with: pip install -e '.[live]'"
            ) from exc
        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.name,
            max_tokens=1200,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        usage = message.usage
        return ModelResponse(
            text="".join(parts),
            prompt_tokens=int(usage.input_tokens),
            completion_tokens=int(usage.output_tokens),
            model_name=self.name,
        )

    def _openai(self, system: str, user: str) -> ModelResponse:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigError(
                "The openai package is not installed. Install it with: pip install -e '.[live]'"
            ) from exc
        client = OpenAI(api_key=self.api_key)
        completion = client.chat.completions.create(
            model=self.name,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = completion.choices[0].message.content or ""
        usage = completion.usage
        return ModelResponse(
            text=text,
            prompt_tokens=int(usage.prompt_tokens or 0),
            completion_tokens=int(usage.completion_tokens or 0),
            model_name=self.name,
        )


def build_model(env: Mapping[str, str] | None = None) -> AgentModel:
    source = os.environ if env is None else env
    provider = source.get("ENQUIRY_DESK_MODEL", "stub").strip().lower() or "stub"
    if provider == "stub":
        return StubModel()
    if provider == "anthropic":
        key = source.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is missing. Set it, or leave ENQUIRY_DESK_MODEL unset to use the stub."
            )
        model_name = source.get("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
        return LiveModel(provider="anthropic", model_name=model_name, api_key=key)
    if provider == "openai":
        key = source.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ConfigError(
                "OPENAI_API_KEY is missing. Set it, or leave ENQUIRY_DESK_MODEL unset to use the stub."
            )
        model_name = source.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
        return LiveModel(provider="openai", model_name=model_name, api_key=key)
    raise ConfigError("ENQUIRY_DESK_MODEL must be stub, anthropic, or openai.")


def priced(response: ModelResponse) -> tuple[float, str]:
    return estimate_cost_usd(response.model_name, response.prompt_tokens, response.completion_tokens)
