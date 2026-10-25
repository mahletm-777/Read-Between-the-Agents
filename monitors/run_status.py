"""Run status record for monitor runs: one small JSON file per run.

Why this exists
---------------
Before this module a monitor run left three kinds of trace: the score/id-map/raw
files, the ledger (live runs only), and a process exit code. Nothing on disk said
whether a run was planned, still running, completed, stopped by the sanity rule,
or failed -- so a partial run (for example one that stopped on a ledger error
after writing raw replies) was indistinguishable from a run that never started.

Scope
-----
* one JSON file per run, written next to that run's outputs;
* records only values that already exist at run time, and never a secret;
* created exclusively: an existing status file is never overwritten, so a second
  run cannot silently replace the first run's status;
* updated by atomic replace, so a reader never sees a half-written record;
* ``completed`` is refused unless the required outputs exist and the written
  record count equals the expected count. A partial run is therefore marked
  ``stopped``/``failed`` with ``partial=True`` rather than reported as complete.

This is an operational record. It is not a scientific artifact, it makes no
claim about spend or caps, and it is not the frozen cross-workstream manifest
schema (which remains an open integration decision).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

STATUS_SCHEMA = "apart-run-status/1"

STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_STOPPED, STATUS_FAILED)
STATUSES = (STATUS_PLANNED, STATUS_RUNNING) + TERMINAL_STATUSES

DEFAULT_ATTEMPT_POLICY = {"parse_attempts_max": 3, "http_attempts_max": 1}


class RunStatusError(ValueError):
    """The status record is malformed or the requested transition is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    """Digest of one input file, for reproducibility metadata."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_status_path(out_path: Path) -> Path:
    """Status path beside a run's score file, derived from it (never a fixed path)."""
    out_path = Path(out_path)
    return out_path.with_name(out_path.name + ".status.json")


def read_status(path: Path) -> dict:
    """Read and validate a status record; never repairs a malformed file."""
    path = Path(path)
    if not path.exists():
        raise RunStatusError(f"status file {path} does not exist")
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RunStatusError(f"status file {path} is unreadable or not valid JSON") from exc
    if not isinstance(data, dict):
        raise RunStatusError(f"status file {path} must contain a JSON object")
    if data.get("schema") != STATUS_SCHEMA:
        raise RunStatusError(f"status file {path} has unexpected schema {data.get('schema')!r}")
    if data.get("status") not in STATUSES:
        raise RunStatusError(f"status file {path} has unknown status {data.get('status')!r}")
    return data


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    """Create ``path`` exclusively; an existing file is never replaced.

    Raises :class:`FileExistsError` (the same collision signal as the score,
    id-map and raw writers) so that a run never overwrites an existing run's
    artifact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(payload), sort_keys=True, indent=2) + "\n")
    except FileExistsError as exc:
        raise FileExistsError(
            f"status file {path} already exists; refusing to overwrite a previous run's "
            f"status (use a fresh --out/--status path per run)"
        ) from exc


def _replace(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace an existing status file (atomic rename), never a partial write."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(dict(payload), sort_keys=True, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RunStatusError(f"status file {path} could not be written: {exc}") from exc


class RunStatus:
    """One run's status record: planning metadata plus a validated state machine.

    ``planned -> running -> completed | stopped | failed``, with no transitions out
    of a terminal state.
    """

    def __init__(self, path: Path, data: dict) -> None:
        self.path = Path(path)
        self.data = dict(data)

    @classmethod
    def begin(cls, path: Path, *, run_id: str, metadata: Optional[Mapping[str, Any]] = None,
              counts: Optional[Mapping[str, Any]] = None,
              outputs: Optional[Mapping[str, Any]] = None,
              attempt_policy: Optional[Mapping[str, Any]] = None) -> "RunStatus":
        """Create the record in state ``planned``.

        Raises :class:`FileExistsError` if the status file already exists, so a
        new run cannot overwrite a previous run's status.
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise RunStatusError("run_id must be a non-empty string")
        now = utc_now()
        payload: dict[str, Any] = {
            "schema": STATUS_SCHEMA,
            "run_id": run_id.strip(),
            "status": STATUS_PLANNED,
            "partial": False,
            "created_utc": now,
            "updated_utc": now,
            "attempt_policy": dict(attempt_policy or DEFAULT_ATTEMPT_POLICY),
        }
        for key, value in (("metadata", metadata), ("counts", counts), ("outputs", outputs)):
            if value is not None:
                payload[key] = dict(value)
        _write_new(path, payload)
        return cls(path, payload)

    def _transition(self, status: str, **extra: Any) -> dict:
        if status not in STATUSES:
            raise RunStatusError(f"unknown status {status!r}")
        current = self.data.get("status")
        if current in TERMINAL_STATUSES:
            raise RunStatusError(
                f"run {self.data.get('run_id')!r} is already {current!r}; "
                f"refusing to record {status!r}"
            )
        payload = dict(self.data)
        payload.update(extra)
        payload["status"] = status
        payload["updated_utc"] = utc_now()
        _replace(self.path, payload)
        self.data = payload
        return payload

    def running(self) -> dict:
        """Mark the run as started (inputs validated, execution beginning)."""
        if self.data.get("status") != STATUS_PLANNED:
            raise RunStatusError(
                f"run must be 'planned' before 'running', got {self.data.get('status')!r}"
            )
        return self._transition(STATUS_RUNNING)

    def completed(self, *, records_written: int,
                  required_outputs: Sequence[Path]) -> dict:
        """Mark ``completed`` only when every required output exists and counts agree."""
        missing = [str(path) for path in required_outputs if not Path(path).exists()]
        if missing:
            raise RunStatusError(
                f"refusing to mark run {self.data.get('run_id')!r} completed: "
                f"required output(s) missing: {missing}"
            )
        expected = (self.data.get("counts") or {}).get("records_expected")
        if expected is not None and int(records_written) != int(expected):
            raise RunStatusError(
                f"refusing to mark run {self.data.get('run_id')!r} completed: wrote "
                f"{records_written} record(s), expected {expected}"
            )
        counts = dict(self.data.get("counts") or {})
        counts["records_written"] = int(records_written)
        return self._transition(STATUS_COMPLETED, partial=False, counts=counts, exit_code=0)

    def stopped(self, *, reason: str, records_written: Optional[int] = None,
                exit_code: int = 2) -> dict:
        """Terminal state for a run ended early by a rule: ``partial=True``."""
        if not isinstance(reason, str) or not reason.strip():
            raise RunStatusError("a stopped run needs a non-empty reason")
        counts = dict(self.data.get("counts") or {})
        if records_written is not None:
            counts["records_written"] = int(records_written)
        return self._transition(STATUS_STOPPED, partial=True, stop_reason=reason.strip(),
                                counts=counts, exit_code=int(exit_code))

    def failed(self, *, error: str, records_written: Optional[int] = None,
               exit_code: int = 1) -> dict:
        """Terminal state for a failed run: explicitly not successful."""
        if not isinstance(error, str) or not error.strip():
            raise RunStatusError("a failed run needs a non-empty error description")
        counts = dict(self.data.get("counts") or {})
        if records_written is not None:
            counts["records_written"] = int(records_written)
        return self._transition(STATUS_FAILED, partial=True, error=error.strip(),
                                counts=counts, exit_code=int(exit_code))


def build_metadata(*, traces_path: Path, prompt_version: str, system_prompt_sha256: str,
                   views: Iterable[str], dry_run: bool, model: Optional[str] = None,
                   provider: Optional[str] = None, base_url: Optional[str] = None,
                   config_sha256: Optional[str] = None, allocation: Optional[str] = None,
                   phase: Optional[str] = None) -> dict:
    """Reproducibility metadata built only from values already known at run time.

    No secrets: the API key is never read or recorded. No cap is stated here,
    because this module does not enforce one.
    """
    metadata: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "traces_path": str(traces_path),
        "traces_sha256": sha256_file(traces_path),
        "prompt_version": prompt_version,
        "system_prompt_sha256": system_prompt_sha256,
        "views": list(views),
    }
    if dry_run:
        metadata["model"] = {"mode": "fake-client"}
    else:
        metadata["model"] = {"provider": provider, "model": model, "base_url": base_url}
        metadata["budget"] = {
            "config_sha256": config_sha256,
            "allocation": allocation,
            "phase": phase,
            "note": "ledger-bound run metadata only; this record states no cap",
        }
    return metadata


__all__ = [
    "STATUS_SCHEMA", "STATUS_PLANNED", "STATUS_RUNNING", "STATUS_COMPLETED",
    "STATUS_STOPPED", "STATUS_FAILED", "STATUSES", "TERMINAL_STATUSES",
    "DEFAULT_ATTEMPT_POLICY", "RunStatusError", "RunStatus", "utc_now", "sha256_file",
    "default_status_path", "read_status", "build_metadata",
]
