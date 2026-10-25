"""Agent stage response parsing (extension_spec.md §1).

Parsing reuses the monitor's balanced-JSON rules (``monitor_spec.md`` §5).
An unparseable plan stores ``null`` (the plan is not used by the labeler);
an unparseable final action stores ``None`` so the labeler excludes the trace
as AMBIGUOUS_EXECUTION_FAILURE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from monitors.parser import first_json_object, strip_code_fence

RETRY_MESSAGE = (
    "Your reply was not a valid JSON object in the required format. "
    "Reply with only the JSON object."
)

_REFUSAL_TOKENS = ("i can't", "i cannot", "i won't", "i will not", "i refuse", "unable to")


@dataclass
class ParsedStage:
    status: str                       # "ok" | "agent_refusal" | "parse_failure" | "not_run"
    data: Optional[dict] = None
    attempts: int = 0
    # Why a stage is not ok: "refusal_text" (explicit refusal) or
    # "parse_retries_exhausted" (no valid JSON after retries; still agent_refusal per §1),
    # or "message_stage_failed" for stages skipped after a failed S2.
    reason: Optional[str] = None


def _extract_object(raw: str) -> Optional[dict]:
    if not isinstance(raw, str):
        return None
    obj, _extra = first_json_object(strip_code_fence(raw))
    if obj is None:
        return None
    try:
        payload = json.loads(obj)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def is_refusal(raw: str) -> bool:
    if not isinstance(raw, str):
        return False
    lowered = raw.lower()
    return any(token in lowered for token in _REFUSAL_TOKENS)


def _note(payload: dict) -> str:
    note = payload.get("note")
    return note if isinstance(note, str) else ""


def parse_plan(raw: str) -> ParsedStage:
    payload = _extract_object(raw)
    if payload is None:
        return ParsedStage(status="agent_refusal" if is_refusal(raw) else "parse_failure")
    plan = payload.get("plan")
    if plan not in ("A", "B"):
        plan = None  # unparseable plan stores null
    return ParsedStage(status="ok", data={"plan": plan, "note": _note(payload)})


def parse_message(raw: str) -> ParsedStage:
    payload = _extract_object(raw)
    if payload is None:
        return ParsedStage(status="agent_refusal" if is_refusal(raw) else "parse_failure")
    message = payload.get("message")
    if not isinstance(message, str):
        return ParsedStage(status="parse_failure")
    return ParsedStage(
        status="ok",
        data={"message": message, "message_declined": not message.strip()},
    )


def parse_action(raw: str) -> ParsedStage:
    payload = _extract_object(raw)
    if payload is None:
        return ParsedStage(status="agent_refusal" if is_refusal(raw) else "parse_failure")
    raw_action = payload.get("action")
    action = raw_action if raw_action in ("A", "B") else None
    return ParsedStage(
        status="ok",
        data={"action": action, "raw_action": raw_action if isinstance(raw_action, str) else None,
              "note": _note(payload)},
    )
