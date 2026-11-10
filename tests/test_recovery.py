"""Bad ids, transient failures, and invalid model output stay inside the workflow."""

from enquiry_desk.errors import TransientModelError, TransientToolError
from enquiry_desk.models import ModelResponse, StubModel
from tests.conftest import drive


def test_a_missing_listing_id_is_followed_by_search(enquiry):
    _pause, final = drive(enquiry("fee-waiver"))
    research = [event for event in final.values["tool_events"] if event["agent"] == "research"]
    sequence = [(event["name"], event["ok"]) for event in research]
    missing = sequence.index(("get_listing", False))
    found = sequence.index(("search_listings", True))
    loaded = sequence.index(("get_listing", True))
    assert missing < found < loaded
    assert "queen-square" in research[missing]["detail"]


def test_transient_tool_failure_is_retried(enquiry, tmp_path):
    calls = {"n": 0}

    def flaky(handler):
        def inner(args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransientToolError("catalogue timed out")
            return handler(args)

        return inner

    _pause, final = drive(
        enquiry("viewing"),
        log_dir=tmp_path,
        tool_wrappers={"get_listing": flaky},
    )
    listing = [event for event in final.values["tool_events"] if event["name"] == "get_listing"]
    assert any("timed out" in event["detail"] and event["attempt"] == 1 for event in listing)
    assert any(event["ok"] and event["attempt"] == 1 for event in listing)
    assert "£1,450" in final.values["draft"]
    assert calls["n"] >= 2


def test_empty_catalogue_search_becomes_a_holding_reply(enquiry):
    def empty(_handler):
        def inner(_args):
            return {"ok": True, "listings": []}

        return inner

    _pause, final = drive(enquiry("viewing"), tool_wrappers={"search_listings": empty})
    assert final.values["research_status"] == "holding"
    assert "£" not in final.values["draft"]
    assert "colleague will check" in final.values["draft"]
    assert final.values["outcome"] == "approved_to_send"


class FlakyOnce:
    name = "stub"

    def __init__(self):
        self.inner = StubModel()
        self.calls = 0

    def complete(self, *, agent, system, payload):
        self.calls += 1
        if self.calls == 1:
            raise TransientModelError("temporary model timeout")
        return self.inner.complete(agent=agent, system=system, payload=payload)


class AlwaysTransient:
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def complete(self, *, agent, system, payload):
        self.calls += 1
        raise TransientModelError("upstream timed out")


class GarbageModel:
    name = "garbage"

    def complete(self, *, agent, system, payload):
        return ModelResponse("nope", 4, 2, "garbage")


class BadJsonOnce:
    name = "stub"

    def __init__(self):
        self.inner = StubModel()
        self.calls = 0

    def complete(self, *, agent, system, payload):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse("not json at all", 5, 5, "stub")
        return self.inner.complete(agent=agent, system=system, payload=payload)


def test_transient_model_error_is_retried_by_the_node(enquiry):
    model = FlakyOnce()
    _pause, final = drive(enquiry("viewing"), model=model)
    assert model.calls > 1
    assert final.values["outcome"] == "approved_to_send"


def test_repeated_model_failure_waits_for_a_person(enquiry):
    model = AlwaysTransient()
    _pause, final = drive(enquiry("viewing"), model=model, action="approve")
    assert model.calls == 3
    assert "upstream timed out" in final.values["fatal_error"]
    assert final.values["drafts"] == []
    assert final.values["outcome"] == "escalated"


def test_invalid_json_is_repaired_then_a_dead_model_escalates(enquiry):
    repaired = BadJsonOnce()
    pause, final = drive(enquiry("viewing"), model=repaired)
    assert any("Invalid JSON" in event["message"] for event in pause.values["trace"])
    assert pause.values["intent"] == "viewing"
    assert final.values["outcome"] == "approved_to_send"

    _pause, crashed = drive(enquiry("spam"), model=GarbageModel())
    assert crashed.values["outcome"] == "escalated"
    assert crashed.values["usage"]["total_tokens"] == 18
    assert "Invalid JSON" in crashed.values["fatal_error"] or "JSON" in crashed.values["fatal_error"]
