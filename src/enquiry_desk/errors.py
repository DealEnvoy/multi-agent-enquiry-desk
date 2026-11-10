"""Errors the desk expects to handle, plus a scrubber for logs."""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """The process is not configured to call a live model."""


class JSONParseError(ValueError):
    """A model reply was not a JSON object."""


class ModelOutputError(ValueError):
    """A model reply stayed invalid after the repair attempts."""

    def __init__(
        self,
        message: str,
        *,
        usage_events: list | None = None,
        trace: list | None = None,
    ) -> None:
        super().__init__(message)
        self.usage_events = list(usage_events or [])
        self.trace = list(trace or [])


class TransientModelError(Exception):
    """A model call failed in a way that is worth retrying.

    LangGraph retries this type on the agent nodes. ValueError and
    ModelOutputError are not retried: another call with the same input
    would fail the same way.
    """


class TransientToolError(Exception):
    """A tool failed in a way that is worth calling again."""


def public_error(exc: BaseException) -> str:
    """Return a short error string with API keys removed."""
    text = str(exc).strip() or exc.__class__.__name__
    for env_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        secret = os.environ.get(env_name, "").strip()
        if len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text[:500]
