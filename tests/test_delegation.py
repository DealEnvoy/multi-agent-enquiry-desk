"""Facts move through shared state from the research tools into the draft."""

from tests.conftest import drive


def test_viewing_draft_uses_tool_facts_the_sender_did_not_provide(enquiry):
    message = enquiry("viewing")
    pause, final = drive(message)
    draft = final.values["draft"]
    assert "1,450" not in message["body"]
    assert "16:30" not in message["body"]
    assert "14 Queen Square" not in message["body"]
    assert "£1,450" in draft
    assert "16:30" in draft
    assert "14 Queen Square" in draft
    assert any("£1,450" in fact["text"] for fact in final.values["facts"])
    agents = [event["agent"] for event in pause.values["trace"]]
    assert agents.index("research") < agents.index("drafter")
    assert agents.index("drafter") < agents.index("reviewer")
    assert pause.values["review_rounds"] == 1
    assert pause.values["review_passed"] is True


def test_fee_waiver_is_sent_back_and_then_corrected(enquiry):
    _pause, final = drive(enquiry("fee-waiver"))
    drafts = final.values["drafts"]
    assert len(drafts) == 2
    assert "we can waive the deposit" in drafts[0].lower()
    assert "we can waive the deposit" not in drafts[1].lower()
    assert "£1,450" in drafts[1]
    assert final.values["review_rounds"] == 2
    history = final.values["review_history"]
    assert history[0]["passed"] is False
    assert history[1]["passed"] is True
    assert final.values["outcome"] == "approved_to_send"
    assert any(event["name"] == "get_policy_snippet" for event in final.values["tool_events"])
