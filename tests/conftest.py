"""Shared fixtures. Tests stay on the stub even if a key is present in the environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from enquiry_desk.graph import build_graph, invoke_until_pause, resume
from enquiry_desk.io import load_enquiry
from enquiry_desk.models import StubModel
from enquiry_desk.state import initial_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENQUIRY_DESK_MODEL", "stub")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def enquiry():
    def load(name: str):
        return load_enquiry(ROOT / "examples" / f"{name}.json")

    return load


def drive(enquiry_data, *, action="approve", model=None, log_dir=None, **graph_kwargs):
    """Run until the human checkpoint, then resume. Fails if the gate is skipped."""
    model = model or StubModel()
    graph = build_graph(model, log_dir=log_dir, **graph_kwargs)
    state = initial_state(enquiry_data, model_name=model.name)
    _config, pause = invoke_until_pause(graph, state, thread_id=enquiry_data["enquiry_id"])
    assert pause.next == ("approval",)
    assert pause.values["outcome"] is None
    assert pause.values["status"] == "awaiting_approval"
    assert pause.interrupts
    final = resume(graph, _config, {"action": action, "note": ""})
    assert final.next == ()
    assert final.values["status"] == "closed"
    return pause, final
