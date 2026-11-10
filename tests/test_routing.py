"""The router classifies each example and the supervisor sends it to the right worker."""

from tests.conftest import drive


def test_viewing_is_researched(enquiry):
    pause, final = drive(enquiry("viewing"))
    assert pause.values["intent"] == "viewing"
    assert pause.values["urgency"] == "normal"
    names = [event["name"] for event in pause.values["tool_events"]]
    assert "match_intent_hints" in names
    assert "search_listings" in names
    assert "get_viewing_slots" in names
    assert final.values["outcome"] == "approved_to_send"


def test_maintenance_uses_the_repair_book(enquiry):
    pause, final = drive(enquiry("maintenance"))
    assert pause.values["intent"] == "maintenance"
    assert pause.values["urgency"] == "urgent"
    repairs = [event for event in pause.values["tool_events"] if event["name"] == "find_repair"]
    assert repairs and repairs[0]["ok"]
    assert "jamie.cole@example.com" in repairs[0]["args"]["email"]
    assert "R-204" in final.values["draft"]
    assert "08:00" in final.values["draft"]
    assert "cannot offer an earlier visit" in final.values["draft"]
    assert final.values["outcome"] == "approved_to_send"


def test_spam_skips_the_catalogue_and_is_archived(enquiry):
    pause, final = drive(enquiry("spam"))
    assert pause.values["intent"] == "spam"
    assert pause.values["research_status"] == "skipped"
    assert not any(event["name"] == "search_listings" for event in pause.values["tool_events"])
    assert pause.values["draft"].startswith("Internal note:")
    assert final.values["outcome"] == "archived"


def test_unclear_mail_is_escalated(enquiry):
    pause, final = drive(enquiry("unclear"))
    assert pause.values["intent"] == "unknown"
    assert pause.values["research_status"] == "skipped"
    assert final.values["outcome"] == "escalated"
    assert final.values["draft"].startswith("Internal note:")
