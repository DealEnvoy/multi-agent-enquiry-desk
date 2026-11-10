"""Fictional catalogue, repair book, and desk policy for Hale & Quay Lettings.

The agency, people, and listings are made up for this demo. The fee notes are
a short teaching summary of the England rules the reviewer enforces. They are
not legal advice.
"""

from __future__ import annotations

import re

INTENTS = ("viewing", "maintenance", "application", "spam", "unknown")
RESEARCH_INTENTS = frozenset({"viewing", "maintenance", "application"})

# First match wins. Spam and repairs are checked before a viewing phrase so a
# sales email that says "view our work" is not treated as a customer viewing.
HINT_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spam", ("seo", "backlink", "crypto", "marketing agency", "rate card")),
    ("maintenance", ("boiler", "leak", "repair", "broken", "heating", "freezing")),
    ("application", ("apply", "application", "holding deposit", "waive", "deposit", "reference")),
    ("viewing", ("viewing", "view", "available", "look around")),
)

URGENT_PHRASES = frozenset({"boiler", "leak", "urgent", "freezing", "no heating"})

INTENT_PRIORITY = ("spam", "maintenance", "application", "viewing")

LISTINGS: tuple[dict, ...] = (
    {
        "listing_id": "QS-14-2",
        "title": "Flat 2, 14 Queen Square",
        "address": "Flat 2, 14 Queen Square, Bristol, BS1 4QS",
        "bedrooms": 2,
        "rent_pcm_gbp": 1450,
        "available_display": "1 December 2026",
        "status": "available",
        "furnished": True,
        "pets": False,
        "slots": ("Thu 12 Nov 2026 16:30", "Sat 14 Nov 2026 11:00"),
    },
    {
        "listing_id": "RS-8-1",
        "title": "8 Redland Street",
        "address": "8 Redland Street, Bristol, BS6 6AP",
        "bedrooms": 1,
        "rent_pcm_gbp": 1100,
        "available_display": None,
        "status": "occupied",
        "furnished": True,
        "pets": False,
        "slots": (),
    },
    {
        "listing_id": "CL-3-4",
        "title": "3 Clifton Park",
        "address": "3 Clifton Park, Bristol, BS8 3HN",
        "bedrooms": 3,
        "rent_pcm_gbp": 2200,
        "available_display": None,
        "status": "let_agreed",
        "furnished": True,
        "pets": False,
        "slots": (),
    },
)

REPAIRS: tuple[dict, ...] = (
    {
        "case_id": "R-204",
        "sender_email": "jamie.cole@example.com",
        "listing_id": "RS-8-1",
        "issue": "boiler not heating the flat",
        "priority": "urgent",
        "window": "11 Nov 2026, 08:00 to 12:00",
        "status": "contractor_booked",
    },
)

POLICIES: dict[str, str] = {
    "viewing": (
        "Viewings are free. Do not promise a tenancy at a viewing. "
        "Do not invent a rent or a time that is not in the listing tools."
    ),
    "application": (
        "In England the Tenant Fees Act 2019 bans most fees charged to tenants. "
        "Do not charge an admin fee or a referencing fee. A holding deposit is capped "
        "at one week's rent. Do not waive the tenancy deposit. Referencing and a "
        "Right to Rent check come before any offer of a tenancy."
    ),
    "repairs": (
        "Confirm only the appointment already written on the repair case. "
        "Do not promise an earlier visit."
    ),
    "unsolicited": "Do not reply to unsolicited sales. Archive the message.",
    "holding": (
        "If the tools did not return a listing or a repair case, do not invent "
        "an address, a rent, a case id, or a time. Say a colleague will check and reply."
    ),
}

TOPIC_FOR_INTENT = {
    "viewing": "viewing",
    "application": "application",
    "maintenance": "repairs",
    "spam": "unsolicited",
    "unknown": "holding",
}

LISTING_QUERIES = (
    ("Queen Square", "queen square"),
    ("Redland Street", "redland"),
    ("Clifton Park", "clifton"),
)


def money(amount: int) -> str:
    return f"£{amount:,}"


def preferred_intent(matches: list[dict]) -> str:
    found = {match["intent"] for match in matches}
    for intent in INTENT_PRIORITY:
        if intent in found:
            return intent
    return "unknown"


def listing_query_from_text(text: str) -> str:
    lowered = text.lower()
    for label, needle in LISTING_QUERIES:
        if needle in lowered:
            return label
    return ""


def slugify_listing_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def describe_listing(listing: dict) -> str:
    pets = "Pets are not accepted." if not listing["pets"] else "Pets may be considered."
    furnished = "Furnished." if listing["furnished"] else "Unfurnished."
    if listing["available_display"]:
        available = f"Available {listing['available_display']}."
    else:
        available = "No available date is listed."
    status = str(listing["status"]).replace("_", " ")
    return (
        f"{listing['listing_id']}: {listing['address']}. "
        f"{listing['bedrooms']} bedrooms. Rent {money(listing['rent_pcm_gbp'])} pcm. "
        f"Status {status}. {available} {furnished} {pets}"
    )


def find_listing(listing_id: str) -> dict | None:
    for listing in LISTINGS:
        if listing["listing_id"] == listing_id:
            return listing
    return None
