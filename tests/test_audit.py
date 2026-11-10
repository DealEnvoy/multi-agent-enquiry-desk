"""The reviewer tool, not the model, decides whether a draft may proceed."""

from enquiry_desk.parsing import parse_json_object
from enquiry_desk.tools import audit_reply

FACTS = [
    {
        "source": "get_listing",
        "text": "QS-14-2: Flat 2, 14 Queen Square. Rent £1,450 pcm.",
    },
    {
        "source": "get_viewing_slots",
        "text": "Viewing slots: Thu 12 Nov 2026 16:30; Sat 14 Nov 2026 11:00",
    },
]


def test_waiver_without_a_refusal_fails():
    result = audit_reply(
        "We can waive the deposit so you can move in without one.",
        FACTS,
        "application",
    )
    assert result["passed"] is False
    assert any("we can waive" in issue for issue in result["issues"])


def test_clear_refusal_is_allowed():
    draft = (
        "Thank you for your interest in Flat 2, 14 Queen Square. "
        "The rent is £1,450 pcm. We cannot waive the deposit. "
        "You can view it at 16:30."
    )
    result = audit_reply(draft, FACTS, "application")
    assert result["passed"] is True
    assert result["issues"] == []


def test_negated_fee_mention_is_allowed_and_a_charge_is_not():
    allowed = audit_reply("We do not charge an admin fee.", [], "application")
    blocked = audit_reply("The admin fee is £50.", FACTS, "application")
    assert allowed["passed"] is True
    assert blocked["passed"] is False


def test_invented_rent_or_time_fails():
    rent = audit_reply("The rent is £9,999 pcm.", FACTS, "viewing")
    clock = audit_reply("Come at 09:15. The open slot 16:30 still stands. Rent £1,450.", FACTS, "viewing")
    assert any("9999" in issue for issue in rent["issues"])
    assert any("09:15" in issue for issue in clock["issues"])


def test_viewing_must_offer_a_real_slot_and_spam_stays_internal():
    missing = audit_reply("The rent is £1,450 pcm. We will be in touch.", FACTS, "viewing")
    sales = audit_reply("Hello, we are not interested.", [], "spam")
    assert any("slot" in issue for issue in missing["issues"])
    assert sales["passed"] is False


def test_parser_accepts_a_fenced_object():
    parsed = parse_json_object('```json\n{"intent": "spam"}\n```')
    assert parsed == {"intent": "spam"}
