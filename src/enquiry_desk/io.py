"""Load an enquiry file."""

from __future__ import annotations

import json
from pathlib import Path

from enquiry_desk.state import Enquiry

REQUIRED = (
    "enquiry_id",
    "received_at",
    "sender_name",
    "sender_email",
    "channel",
    "subject",
    "body",
)


def load_enquiry(path: Path | str) -> Enquiry:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is not valid JSON ({exc.msg}).") from exc
    if not isinstance(data, dict):
        # ValueError so the CLI can report a bad file without a traceback.
        raise ValueError(f"{source} must contain a JSON object.")  # noqa: TRY004
    missing = [
        key for key in REQUIRED if not isinstance(data.get(key), str) or not str(data.get(key)).strip()
    ]
    if missing:
        raise ValueError(f"{source} is missing: {', '.join(missing)}.")
    enquiry_id = str(data["enquiry_id"]).strip()
    if "/" in enquiry_id or "\\" in enquiry_id or ".." in enquiry_id or len(enquiry_id) > 80:
        raise ValueError("enquiry_id is not a safe file name.")
    return {key: str(data[key]).strip() for key in REQUIRED}  # type: ignore[return-value]
