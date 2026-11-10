"""Supervisor policy, independent of the model."""

from pathlib import Path

from enquiry_desk.io import load_enquiry
from enquiry_desk.state import initial_state
from enquiry_desk.workflow import decide, recommend, resolve_outcome

ROOT = Path(__file__).resolve().parents[1]


def _state(**changes):
    enquiry = load_enquiry(ROOT / "examples" / "viewing.json")
    state = initial_state(enquiry, model_name="stub")
    state.update(changes)
    return state


def test_a_new_enquiry_goes_to_the_router():
    assert decide(_state(), max_review_rounds=2) == "router"


def test_research_runs_before_a_draft_for_a_viewing():
    state = _state(intent="viewing", research_status="pending")
    assert decide(state, max_review_rounds=2) == "research"


def test_failed_review_returns_to_the_drafter_until_the_round_limit():
    failed = _state(
        intent="application",
        research_status="ok",
        draft_version=1,
        reviewed_version=1,
        review_passed=False,
    )
    assert decide(failed, max_review_rounds=2) == "drafter"
    failed["draft_version"] = 2
    failed["reviewed_version"] = 1
    assert decide(failed, max_review_rounds=2) == "reviewer"
    failed["reviewed_version"] = 2
    assert decide(failed, max_review_rounds=2) == "approval"


def test_a_fatal_error_goes_to_a_person_even_with_no_intent():
    state = _state(fatal_error="router failed: bad json")
    assert decide(state, max_review_rounds=2) == "approval"
    assert recommend(state) == "escalate"


def test_outcomes_only_release_a_passing_send():
    assert resolve_outcome("send", "approve") == "approved_to_send"
    assert resolve_outcome("send", "reject") == "withheld"
    assert resolve_outcome("archive", "approve") == "archived"
    assert resolve_outcome("escalate", "approve") == "escalated"
    assert resolve_outcome("send", "approve") != "withheld"
