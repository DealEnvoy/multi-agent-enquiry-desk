"""Parse JSON objects from model text, including a fenced block."""

from __future__ import annotations

import json
from typing import Any

from enquiry_desk.errors import JSONParseError


def parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise JSONParseError("Model output did not contain a JSON object.")
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JSONParseError(f"Model output was not valid JSON ({exc.msg}).") from exc
    if not isinstance(value, dict):
        raise JSONParseError("Model output JSON was not an object.")
    return value
