"""Nothing is marked ready to send until a person resumes the graph."""

import json
from pathlib import Path

from tests.conftest import drive


def test_reject_withholds_a_good_reply(enquiry, tmp_path):
    _pause, final = drive(enquiry("viewing"), action="reject", log_dir=tmp_path)
    assert final.values["outcome"] == "withheld"
    assert final.values["approval"]["action"] == "reject"
    log = json.loads(Path(final.values["log_path"]).read_text(encoding="utf-8"))
    assert log["outcome"] == "withheld"
    assert log["usage"]["total_tokens"] == final.values["usage"]["total_tokens"]
    assert "decided_at" in log


def test_a_failed_review_cannot_be_released(enquiry):
    _pause, final = drive(enquiry("fee-waiver"), action="approve", max_review_rounds=1)
    assert len(final.values["drafts"]) == 1
    assert final.values["review_passed"] is False
    assert "we can waive the deposit" in final.values["drafts"][0].lower()
    assert final.values["outcome"] == "escalated"


def test_usage_events_match_the_total(enquiry, tmp_path):
    _pause, final = drive(enquiry("viewing"), log_dir=tmp_path)
    usage = final.values["usage"]
    events = final.values["usage_events"]
    assert usage["prompt_tokens"] == sum(event["prompt_tokens"] for event in events)
    assert usage["completion_tokens"] == sum(event["completion_tokens"] for event in events)
    assert usage["total_tokens"] > 0
    assert usage["estimated_cost_usd"] == 0
    assert final.values["outcome"] == "approved_to_send"
    assert Path(final.values["log_path"]).name == "enq-1042.json"
