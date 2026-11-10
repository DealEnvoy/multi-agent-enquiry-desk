"""Cost maths, offline default, and enquiry file checks."""

import json

import pytest

from enquiry_desk.cost import add_usage, empty_usage, estimate_cost_usd
from enquiry_desk.errors import ConfigError, public_error
from enquiry_desk.io import load_enquiry
from enquiry_desk.models import build_model


def test_example_rates_and_stub_rates():
    example, example_label = estimate_cost_usd("example", 1_000_000, 2_000_000)
    assert example == 33.0
    assert example_label == "example"
    stub, stub_label = estimate_cost_usd("stub", 500, 100)
    assert stub == 0
    assert stub_label == "stub"
    live, live_label = estimate_cost_usd("claude-sonnet-4-5", 1_000_000, 0)
    assert live == 3.0
    assert live_label == "example"


def test_usage_reducer_adds_calls():
    total = add_usage(empty_usage(), {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "estimated_cost_usd": 0.1})
    total = add_usage(total, {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4, "estimated_cost_usd": 0.2})
    assert total["prompt_tokens"] == 13
    assert total["completion_tokens"] == 5
    assert total["total_tokens"] == 18
    assert total["estimated_cost_usd"] == 0.3


def test_build_model_defaults_and_refuses_a_live_call_without_a_key():
    assert build_model({}).name == "stub"
    with pytest.raises(ConfigError):
        build_model({"ENQUIRY_DESK_MODEL": "anthropic"})
    with pytest.raises(ConfigError):
        build_model({"ENQUIRY_DESK_MODEL": "openai"})
    model = build_model(
        {
            "ENQUIRY_DESK_MODEL": "anthropic",
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_MODEL": "claude-sonnet-4-5",
        }
    )
    assert model.name == "claude-sonnet-4-5"
    assert model.provider == "anthropic"


def test_public_error_redacts_a_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    text = public_error(RuntimeError("request failed for sk-test-secret-value"))
    assert "sk-test-secret-value" not in text
    assert "[redacted]" in text


def test_enquiry_id_cannot_escape_the_log_directory(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "enquiry_id": "../secret",
                "received_at": "2026-11-10T00:00:00Z",
                "sender_name": "A",
                "sender_email": "a@example.com",
                "channel": "web_form",
                "subject": "Hi",
                "body": "Hello",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_enquiry(path)
