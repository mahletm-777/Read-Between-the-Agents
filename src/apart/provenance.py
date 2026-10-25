"""Durable per-call provenance for a live run (extension_spec.md §7.2, §16.9).

One JSON line per billable call, appended and fsynced before the caller moves on,
so a crash can never leave a dispatched call unrecorded. The log is closed to
secret material: a record whose key names or values look like a credential,
an authorization header or a bearer token is refused rather than written.

Raw model output is deliberately NOT stored here. The trace record already keeps
the outputs the experiment needs; the provenance log keeps the call lifecycle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Every field the run manifest promises to be able to reconstruct.
REQUIRED_FIELDS = (
    "run_id", "trace_id", "stage", "call_id", "attempt", "provider", "model",
    "prompt_sha256", "dispatched", "reserved_micro", "usage", "settled_micro",
    "settlement_state", "ts",
)

FORBIDDEN_KEY_PARTS = ("api_key", "apikey", "authorization", "auth_header", "secret",
                       "password", "bearer", "credential")

_SECRET_VALUE_MARKERS = ("sk-", "Bearer ", "APART_API_KEY")


class ProvenanceError(ValueError):
    """The record is incomplete, or would store material that must not be stored."""


def _reject_secrets(record: Mapping[str, Any]) -> None:
    for key, value in record.items():
        lowered = str(key).lower()
        if lowered in {"key", "token"} or any(part in lowered for part in FORBIDDEN_KEY_PARTS):
            raise ProvenanceError(f"field {key!r} may not be written to the provenance log")
        if isinstance(value, str):
            for marker in _SECRET_VALUE_MARKERS:
                if marker in value:
                    raise ProvenanceError(
                        f"field {key!r} looks like it carries a credential"
                    )


def make_record(*, run_id: str, trace_id: str, stage: str, call_id: str, attempt: int,
                provider: str, model: str, prompt_sha256: str, dispatched: bool,
                reserved_micro: int, usage: Optional[Mapping[str, Any]],
                settled_micro: Optional[int], settlement_state: str, ts: str,
                failure_category: Optional[str] = None,
                context_stages: Optional[Sequence[str]] = None,
                input_messages: Optional[int] = None,
                request_hash_format: Optional[str] = None,
                served: Optional[Mapping[str, Any]] = None) -> dict:
    """Build one provenance record. Missing pieces stay ``None``, never invented.

    ``model`` and ``provider`` are what was REQUESTED (the frozen manifest).
    ``served``, when given, records what the provider REPORTED as
    ``served_model``/``served_provider``; a value it did not report is stored as
    ``None``, never copied from the request.

    ``context_stages`` (earlier stages carried into this call), ``input_messages``
    (chat messages sent, counting the system message and any retry turns) and
    ``request_hash_format`` (how ``prompt_sha256`` was computed) are Stage 4M/4N
    additions. They are optional so that a record without them stays
    byte-identical to before.
    """
    record = {
        "run_id": run_id,
        "trace_id": trace_id,
        "stage": stage,
        "call_id": call_id,
        "attempt": attempt,
        "provider": provider,
        "model": model,
        "prompt_sha256": prompt_sha256,
        "dispatched": bool(dispatched),
        "reserved_micro": reserved_micro,
        "usage": dict(usage) if isinstance(usage, Mapping) else None,
        "settled_micro": settled_micro,
        "settlement_state": settlement_state,
        "ts": ts,
    }
    if failure_category is not None:
        record["failure_category"] = failure_category
    if context_stages is not None:
        record["context_stages"] = [str(name) for name in context_stages]
    if input_messages is not None:
        record["input_messages"] = int(input_messages)
    if request_hash_format is not None:
        record["request_hash_format"] = str(request_hash_format)
    if served is not None:
        for key in ("served_model", "served_provider"):
            value = served.get(key)
            record[key] = value if isinstance(value, str) and value.strip() else None
    return record


class ProvenanceLog:
    """Append-only JSONL log. A record is durable before ``record`` returns."""

    def __init__(self, path: Path, *, run_id: str, append: bool = False) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._count = 0
        if self.path.exists() and not append:
            raise ProvenanceError(
                f"provenance log {self.path} already exists; refusing to overwrite"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def count(self) -> int:
        return self._count

    def record(self, record: Mapping[str, Any]) -> dict:
        """Validate, append and fsync one record. Nothing is written if invalid."""
        payload = dict(record)
        if payload.get("run_id") != self.run_id:
            raise ProvenanceError("record run_id does not match this log")
        missing = [name for name in REQUIRED_FIELDS if name not in payload]
        if missing:
            raise ProvenanceError(f"provenance record is missing {missing}")
        if isinstance(payload["attempt"], bool) or not isinstance(payload["attempt"], int):
            raise ProvenanceError("attempt must be an integer")
        reserved = payload["reserved_micro"]
        if isinstance(reserved, bool) or not isinstance(reserved, int) or reserved < 0:
            raise ProvenanceError("reserved_micro must be a non-negative integer")
        settled = payload["settled_micro"]
        if settled is not None and (isinstance(settled, bool) or not isinstance(settled, int)
                                    or settled < 0):
            raise ProvenanceError("settled_micro must be null or a non-negative integer")
        _reject_secrets(payload)
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        handle = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
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
