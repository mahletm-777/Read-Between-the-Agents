"""Append-only raw-reply sidecar for the OBJ-1.0 generation probe.

Why a separate file: :mod:`apart.provenance` records the call *lifecycle* and
deliberately does not store raw model text (``provenance.py`` says so). A quality
feasibility probe needs the actual replies - including replies that fail parsing, are
refusals, or exceed the output bound - otherwise the only surviving evidence is a
derived action label.

This sidecar is **not** experiment input:

* it is never read by the extractor, the labeler, a monitor, or the E-label code
  path, and it is not ground truth;
* it is written next to the run's provenance, refuses to overwrite, and is fsynced
  per record (append-only JSONL);
* it applies the same secret discipline as provenance: a record whose keys or values
  look like a credential, authorization header or bearer token is **refused**, not
  scrubbed, so nothing sensitive is ever written.

``received`` is the honest flag: when a transport failure happens there is no reply
text, so ``text`` stays ``null`` and the failure is recorded as such. Reply text is
never invented, and a stage that was never dispatched produces no record.

Per-attempt parse status lives in the per-call provenance record
(``failure_category``/``status``), which is keyed by the same ``call_id``/``attempt``
pair, so the two logs can be joined without duplicating text.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from apart.provenance import _reject_secrets

RAW_REPLY_SCHEMA_VERSION = "raw-replies/1.0"

REQUIRED_FIELDS = (
    "schema_version", "run_id", "trace_id", "stage", "call_id", "attempt",
    "received", "status", "text", "ts",
)


class RawReplyError(ValueError):
    """The record is malformed, or would store material that must not be stored."""


def split_call_id(call_id: Any) -> tuple[str, str, int]:
    """``ext-0007:s2:p1`` -> ``("ext-0007", "s2", 1)``.

    Raises :class:`RawReplyError` for anything else, so a record can never be filed
    against an unparseable call identity.
    """
    if not isinstance(call_id, str) or call_id.count(":") != 2:
        raise RawReplyError(f"unparseable call_id {call_id!r}")
    trace_id, stage, attempt_part = call_id.split(":")
    if not trace_id or not stage or not attempt_part.startswith("p"):
        raise RawReplyError(f"unparseable call_id {call_id!r}")
    try:
        attempt = int(attempt_part[1:])
    except ValueError as exc:
        raise RawReplyError(f"unparseable call_id {call_id!r}") from exc
    return trace_id, stage, attempt


class RawReplyLog:
    """Append-only JSONL sidecar. A record is durable before :meth:`record` returns."""

    def __init__(self, path: Path, *, run_id: str, append: bool = False) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.schema_version = RAW_REPLY_SCHEMA_VERSION
        self._count = 0
        if self.path.exists():
            if self.path.is_dir():
                raise RawReplyError(f"raw reply log path {self.path} is a directory")
            if not append:
                raise RawReplyError(
                    f"raw reply log {self.path} already exists; refusing to overwrite")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def count(self) -> int:
        return self._count

    def record(self, *, call_id: str, received: bool, text: Optional[str], status: str,
               error: Optional[str] = None, response_id: Optional[str] = None,
               served_model: Optional[str] = None,
               served_provider: Optional[str] = None,
               usage_status: Optional[str] = None, ts: str) -> dict:
        """Validate, append and fsync one raw reply. Nothing is written if invalid."""
        trace_id, stage, attempt = split_call_id(call_id)
        if not isinstance(received, bool):
            raise RawReplyError("received must be a boolean")
        if not isinstance(status, str) or not status:
            raise RawReplyError("status must be a non-empty string")
        if received:
            if not isinstance(text, str):
                raise RawReplyError(
                    "a received reply must carry its text (an empty reply is '')")
            if error is not None:
                raise RawReplyError("a received reply must not carry an error")
        else:
            if text is not None:
                raise RawReplyError(
                    "no reply was received, so text must stay null: "
                    "reply text is never invented")
            if not isinstance(error, str) or not error:
                raise RawReplyError("a missing reply must record why (error)")

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "trace_id": trace_id,
            "stage": stage,
            "call_id": call_id,
            "attempt": attempt,
            "received": received,
            "status": status,
            "text": text,
            "text_sha256": None if text is None else hashlib.sha256(
                text.encode("utf-8")).hexdigest(),
            "text_bytes": None if text is None else len(text.encode("utf-8")),
            "response_id": response_id,
            "served_model": served_model,
            "served_provider": served_provider,
            "usage_status": usage_status,
            "error": error,
            "ts": ts,
        }
        missing = [name for name in REQUIRED_FIELDS if name not in payload]
        if missing:  # pragma: no cover - guarded by construction above
            raise RawReplyError(f"raw reply record is missing {missing}")
        _reject_secrets(payload)
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        handle = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(handle, line.encode("utf-8"))
            os.fsync(handle)
        finally:
            os.close(handle)
        self._count += 1
        return payload

    def records(self) -> list[dict]:
        """Read back every record, in write order."""
        if not self.path.exists():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["RAW_REPLY_SCHEMA_VERSION", "REQUIRED_FIELDS", "RawReplyError",
           "RawReplyLog", "split_call_id"]

