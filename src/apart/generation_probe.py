"""Generation-only feasibility probe under OBJ-1.0: the execution path.

This module is the execution owner for the ``generation-probe-1.0`` protocol. It is
deliberately additive: ``apart.behavior_probe`` (Stage 4N) is **not** modified, so the
frozen Stage 4N manifest, its code/prompt SHA-256 pins and its V0 = A/A gate keep
their historical meaning and can still be re-validated.

Differences from the Stage 4N probe, all required by OBJ-1.0:

* manifest identity is ``generation-probe-1.0`` / ``apart-generation-probe/1.0`` and
  must name ``objective_id`` (validated against ``apart.objective_spec``);
* pass/fail is an execution-validity gate (:mod:`apart.probe_protocol`), not a
  desired-action gate, so behaviour counts can never become pipeline failures;
* per-call provenance additionally carries ``temperature``, ``max_tokens``, ``seed``,
  ``response_id``, ``usage_status``, ``objective_id``, ``protocol_version`` and
  ``prompt_version``. Extra keys are accepted by ``apart.provenance.ProvenanceLog``
  (it validates the required 14 and refuses credential-shaped material), so the
  historical provenance module itself is untouched;
* raw reply text is retained in an append-only sidecar (:mod:`apart.raw_replies`),
  linked to provenance by ``call_id``/``attempt``.

Still generation-only: no extraction, no labeler, no monitor arms, one parse attempt
per stage, one transport attempt, reservation before dispatch, and every fail-closed
stop condition preserved. Trace-level metadata (source identity, raw sidecar path,
descriptive counts) lives in the summary; per-call lifecycle lives in provenance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

from agents.runner import USAGE_REPORTED, USAGE_UNKNOWN
from apart.behavior_probe import (INVALID, ProbeError, check_binding, check_budget,
                                  joint_outcome, probe_plan, write_probe_outputs)
from apart.brief_registry import (BASE_BRIEF_VERSION, BriefVersionError,
                                  applied, available_brief_versions,
                                  resolve_variant_briefs, run_trace_briefed,
                                  verify_frozen_originals)
from apart.probe_protocol import (GENERATION_PROBE_PROTOCOL_VERSION, STAGE_COUNT,
                                  evaluate_execution_gate,
                                  validate_generation_probe_manifest)
from apart.provenance import make_record
from monitors.ledger import LedgerError, utc_now

ROOT = Path(__file__).resolve().parents[2]

#: Top-level directories that hold historical artifacts. Never a probe output target.
PROTECTED_DIR_NAMES = ("data", "results", "configs", "docs", "AI Context")

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ProbePathError(ValueError):
    """A probe output target would overwrite historical or protected artifacts."""


def assert_writable_target(path: Any, *, repo_root: Optional[Path] = None) -> Path:
    """Refuse an output target inside protected dirs or an existing run directory.

    A *fresh* run directory under ``runs/`` is allowed; anything inside an existing
    ``runs/<run>/`` tree is refused, so no prior run record can be touched.
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    target = Path(path).resolve()
    for name in PROTECTED_DIR_NAMES:
        protected = (root / name).resolve()
        if target == protected or protected in target.parents:
            raise ProbePathError(f"refusing to write under the protected directory {protected}")
    runs = (root / "runs").resolve()
    if target == runs or runs in target.parents:
        relative = target.relative_to(runs)
        run_dir = runs / relative.parts[0]
        if run_dir.exists():
            raise ProbePathError(
                f"refusing to write inside the existing run directory {run_dir}")
    return target


def source_state(repo_root: Optional[Path] = None, *, observed: Optional[Mapping] = None) -> dict:
    """Best-effort source identity, without invoking git.

    ``source_commit``/``source_ref`` are read from ``.git/HEAD`` (``None`` when it
    cannot be determined, e.g. packed refs or a worktree). ``source_dirty`` is
    ``None`` = **not determined**: a clean tree is never claimed, because detecting
    uncommitted changes requires invoking git, which this probe does not do. A caller
    that has its own observation may pass ``observed``.
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    state: dict[str, Any] = {"source_commit": None, "source_ref": None,
                             "source_dirty": None}
    try:
        text = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text.startswith("ref:"):
        ref = text.split(" ", 1)[1].strip()
        state["source_ref"] = ref or None
        try:
            commit = (root / ".git" / ref).read_text(encoding="utf-8").strip()
        except OSError:
            commit = ""
        if _COMMIT_RE.match(commit):
            state["source_commit"] = commit
    elif _COMMIT_RE.match(text):
        state["source_commit"] = text
    if observed:
        for key in ("source_commit", "source_ref", "source_dirty"):
            if key in observed:
                state[key] = observed[key]
    return state



# --------------------------------------------------------------- raw sidecar

class RawRecordingClient:
    """Delegating client that mirrors each attempt into the raw-reply sidecar.

    It adds no retry, no fallback and no cost logic: it observes exactly the call the
    runner already makes and re-raises whatever the inner client raises. ``model`` and
    ``max_attempts`` are delegated unchanged, so the run-probe binding checks still see
    the real client.
    """

    def __init__(self, inner: Any, raw_log: Optional[Any] = None) -> None:
        self._inner = inner
        self._raw = raw_log

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def call_model(self, messages: list, **kwargs: Any) -> Any:
        call_id = kwargs.get("call_id")
        try:
            response = self._inner.call_model(messages, **kwargs)
        except Exception as exc:  # transport failure: no reply text exists
            self._write(call_id, received=False, text=None, status="transport_error",
                        error=type(exc).__name__, response=None)
            raise
        text = getattr(response, "text", None)
        if isinstance(text, str):
            self._write(call_id, received=True, text=text, status="received",
                        response=response)
        else:
            self._write(call_id, received=False, text=None, status="no_text_in_response",
                        error="response carried no text", response=response)
        return response

    def _write(self, call_id: Any, *, received: bool, text: Optional[str], status: str,
               response: Any, error: Optional[str] = None) -> None:
        if self._raw is None:
            return
        usage = getattr(response, "usage", None) if response is not None else None
        self._raw.record(
            call_id=call_id, received=received, text=text, status=status, error=error,
            response_id=(getattr(response, "response_id", None)
                         if response is not None else None),
            served_model=(getattr(response, "served_model", None)
                          if response is not None else None),
            served_provider=(getattr(response, "served_provider", None)
                             if response is not None else None),
            usage_status=USAGE_REPORTED if usage is not None else USAGE_UNKNOWN,
            ts=utc_now(),
        )


# ------------------------------------------------------- brief version seam

#: Manifest key that declares which brief version this probe runs under.
BRIEF_VERSION_FIELD = "brief_versions"


class TraceCallWindow:
    """Per-trace view of the messages actually handed to the client.

    ``run_trace_briefed`` proves that a brief version reached the wire by re-reading
    the calls a client received and comparing each system prompt with the one the
    version implies. Two properties are needed for that to be sound in a real run:

    * **Trace scope.** One client serves all 35 traces, and both ``FakeAgentClient``
      and the guarded live client accumulate ``calls`` for the whole run. Read
      whole-run, trace *N*'s check would compare traces 1..*N*-1 prompts against
      trace *N*'s variant and fail (or, worse, a variant that happened to repeat
      would pass). This window exposes only the calls made while it is in scope.
    * **Evidence the client cannot hide.** The window records the ``messages`` it
      forwards itself, so delivery is verifiable even for a client that records
      nothing of its own (the guarded ``ModelClient`` writes no ``calls`` list).

    It delegates everything else to the wrapped client, so ``model``,
    ``max_attempts`` and the runner's binding checks see the real client.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._calls: list[dict] = []

    def __getattr__(self, name: str) -> Any:
        inner = self.__dict__.get("_inner")
        if inner is None:  # pragma: no cover - only before __init__ completes
            raise AttributeError(name)
        if name == "calls":
            return list(self.__dict__.get("_calls", []))
        return getattr(inner, name)

    def call_model(self, messages: list, **kwargs: Any) -> Any:
        self._calls.append({"call_id": kwargs.get("call_id"),
                            "messages": [dict(m) for m in messages]})
        return self._inner.call_model(messages, **kwargs)


def manifest_brief_version(manifest: Mapping) -> str:
    """The brief version this manifest declares. Fails closed.

    The version must be stated explicitly: there is no silent fall back to the
    historical briefs, because a wrong-but-quiet default would record a v2.1 run as
    if it were the revised condition. A fixture that genuinely exercises the
    historical path passes ``BASE_BRIEF_VERSION`` explicitly.
    """
    block = manifest.get(BRIEF_VERSION_FIELD)
    if not isinstance(block, Mapping) or "version" not in block:
        raise ProbeError(
            f"manifest must declare {BRIEF_VERSION_FIELD}.version explicitly "
            f"(one of {list(available_brief_versions())}); refusing to guess")
    version = block["version"]
    if version not in available_brief_versions():
        raise ProbeError(
            f"unknown brief version {version!r} in {BRIEF_VERSION_FIELD}.version; "
            f"available: {list(available_brief_versions())}")
    return version


def brief_plan_summary(manifest: Mapping, brief_version: str) -> list[dict]:
    """Resolve every cell's briefs up front, so a bad version fails before any call."""
    summary = []
    for variant, condition in manifest["cells"]:
        sender, receiver = resolve_variant_briefs(variant, brief_version)
        summary.append({
            "variant": variant, "condition": condition,
            "brief_version": brief_version,
            "sender_brief": sender.brief_uid,
            "receiver_brief": receiver.brief_uid,
            "sender_brief_display_id": sender.display_id,
            "receiver_brief_display_id": receiver.display_id,
            "sender_brief_sha256": sender.sha256,
            "receiver_brief_sha256": receiver.sha256,
        })
    return summary


def check_brief_delivery(record: Mapping, trace_id: str) -> dict:
    """Fail closed unless every dispatched call carried the intended brief text.

    ``run_trace_briefed`` compares the delivered system prompt byte-for-byte against
    the one the selected version implies, so this is a check of prompt text, not of a
    variant id or a registry lookup. A trace that dispatched nothing verifies nothing,
    which is treated as a failure rather than a pass.
    """
    delivery = record.get("brief_delivery")
    if not isinstance(delivery, Mapping):
        raise ProbeError(
            f"{trace_id}: no brief_delivery evidence on the trace record; refusing to "
            "record a versioned trace whose delivered prompts were not verified")
    if not delivery.get("verified"):
        raise ProbeError(
            f"{trace_id}: brief delivery could not be verified "
            f"({delivery.get('reason', 'unknown reason')}); failing closed")
    checked = delivery.get("calls_checked", 0)
    if not isinstance(checked, int) or checked < 1:
        raise ProbeError(
            f"{trace_id}: brief delivery verified zero calls; failing closed")
    return dict(delivery)


# ---------------------------------------------------------------- provenance

#: Additive per-call fields for this protocol. Values the provider or the runner did
#: not supply are written as explicit ``null``; none is ever fabricated.
GENERATION_PROVENANCE_EXTRA_FIELDS = (
    "temperature", "max_tokens", "seed", "response_id", "usage_status",
    "objective_id", "protocol_version", "prompt_version",
)


def build_call_record(manifest: Mapping, entry: Mapping, event: Mapping,
                      trace_id: str) -> dict:
    """One per-call provenance record: the 14 required fields plus the additions."""
    record = make_record(
        run_id=manifest["run_id"], trace_id=trace_id, stage=entry["stage"],
        call_id=entry["call_id"], attempt=event["attempt"],
        provider=manifest["provider"], model=manifest["model"],
        prompt_sha256=entry["prompt_sha256"],
        dispatched=event.get("dispatched_ts") is not None,
        reserved_micro=event["reserved_micro"], usage=entry.get("usage"),
        settled_micro=event.get("settled_micro"), settlement_state=event["state"],
        ts=utc_now(),
        failure_category=(entry["status"] if entry["status"] != "ok" else None),
        context_stages=entry.get("context_stages"),
        input_messages=entry.get("input_messages"),
        request_hash_format=entry.get("request_hash_format"),
        served=entry,
    )
    for name in GENERATION_PROVENANCE_EXTRA_FIELDS:
        if name == "objective_id":
            record[name] = manifest["objective_id"]
        elif name == "protocol_version":
            record[name] = manifest["protocol_version"]
        elif name == "prompt_version":
            record[name] = manifest["prompt_version"]
        else:
            record[name] = entry.get(name)
    return record


def record_calls(manifest: Mapping, store: Any, provenance: Any, result: Mapping,
                 trace_id: str) -> list[dict]:
    """Write one provenance record per attempted call; a missing ledger event stops."""
    index = {(e["call_id"], e["attempt"]): e for e in store.load()["events"]}
    records = []
    for entry in result["calls"]:
        event = index.get((entry["call_id"], 0))
        if event is None:
            raise LedgerError(f"no ledger event for {entry['call_id']!r}")
        record = build_call_record(manifest, entry, event, trace_id)
        provenance.record(record)
        records.append(record)
    return records


# ------------------------------------------------------------------- summary

def summarize_generation_probe(manifest: Mapping, rows: list[dict],
                               stopped: Optional[str], budget: Optional[Mapping] = None,
                               *, source: Optional[Mapping] = None,
                               raw_log_path: Optional[str] = None,
                               brief_version: Optional[str] = None,
                               brief_plan: Optional[list] = None) -> dict:
    """Trace-level summary.

    Outcome counts here are **descriptive only**; pass/fail is the execution-validity
    gate, which never reads them. Invalid traces stay in the denominator and keep
    their reason.
    """
    planned = len(probe_plan(manifest))
    counts: dict[str, dict[str, int]] = {f"{variant}/{condition}": {}
                                         for variant, condition in manifest["cells"]}
    for row in rows:
        key = f"{row['variant']}/{row['condition']}"
        counts[key][row["outcome"]] = counts[key].get(row["outcome"], 0) + 1
    return {
        "run_id": manifest["run_id"],
        "probe_manifest_version": manifest["probe_manifest_version"],
        "protocol_version": manifest["protocol_version"],
        "objective_id": manifest["objective_id"],
        "design_status": manifest["design_status"],
        "prompt_version": manifest["prompt_version"],
        "prompt_sha256": manifest["prompt_sha256"],
        "provenance_schema_version": manifest["provenance_schema_version"],
        "planned_traces": planned,
        "attempted_traces": len(rows),
        "not_attempted": planned - len(rows),
        "descriptive_counts_by_cell": counts,
        "counts_are_descriptive_only": True,
        "brief_version": brief_version,
        "brief_plan": brief_plan,
        "brief_delivery_all_verified": (
            None if not rows else all(
                isinstance(r.get("brief_delivery"), Mapping)
                and r["brief_delivery"].get("verified") is True
                for r in rows)),
        "gate": evaluate_execution_gate(rows, manifest["feasibility_gate"], stopped),
        "stopped": stopped,
        "worst_case_micro": None if budget is None else budget["total_micro"],
        "outcome_order": "sender/receiver",
        "generation_only": {"extraction": False, "labeler": False,
                            "monitor_arms": [], "confirmatory": False},
        "source": dict(source or {}),
        "raw_reply_log": raw_log_path,
        "analysis_note": ("Generation-only feasibility probe: no E labels, no monitor "
                          "scores and no detection-performance claims are produced."),
    }


# ----------------------------------------------------------------------- run

def run_generation_probe(*, manifest: Mapping, policy: Any, store: Any, config_hash: str,
                         agent_client: Any, provenance: Any, raw_log: Optional[Any] = None,
                         repo_root: Optional[Path] = None,
                         source_observed: Optional[Mapping] = None) -> dict:
    """Run the approved generation-only plan. One attempt per stage, no retries.

    Stops (and reports why) on: a ledger error or cap refusal, any unknown-cost event,
    a settlement above its reservation, a served model that differs from the requested
    one (when the provider reports it), or the call limit. The reservation itself is
    made by the guarded client before dispatch, exactly as in Stage 4N.
    """
    manifest = validate_generation_probe_manifest(manifest, repo_root=repo_root)
    check_binding(manifest, policy)
    if getattr(agent_client, "model", None) != manifest["model"]:
        raise ProbeError("agent client model does not match the manifest")
    if int(getattr(agent_client, "max_attempts", 1)) != 1:
        raise ProbeError("agent client max_attempts must be 1")
    if raw_log is not None and raw_log.run_id != manifest["run_id"]:
        raise ProbeError("raw reply log run_id does not match the manifest")

    # Brief version and delivered-prompt verification. Resolved and cross-checked
    # before the first request, so an unavailable brief version -- or a tampered
    # pinned prompt file -- stops the run instead of quietly producing v2.1 traces.
    brief_version = manifest_brief_version(manifest)
    verify_frozen_originals()  # fails closed if agents/prompts.py drifted
    brief_plan = brief_plan_summary(manifest, brief_version)

    ledger = store.load(expected_config_hash=config_hash, policy=policy)
    if any(e["run_id"] == manifest["run_id"] for e in ledger["events"]):
        raise ProbeError(f"run {manifest['run_id']!r} already has ledger events; refusing")
    # The reservation is computed inside the seam so it reflects the brief text the
    # run will actually send. ``plan_worst_case`` bounds input tokens from the
    # serialized messages, and the revised V2/V5 briefs are a different length from
    # their v2.1 predecessors, so computing this outside the seam would understate
    # (or overstate) the plan against the cap.
    with applied(brief_version):
        budget = check_budget(manifest, policy, ledger)

    client = RawRecordingClient(agent_client, raw_log)
    rows: list[dict] = []
    stopped: Optional[str] = None
    for cell in probe_plan(manifest):
        if len(store.load()["events"]) + STAGE_COUNT > manifest["max_calls"]:
            stopped = f"call limit {manifest['max_calls']} reached before ext-{cell.seed:04d}"
            break
        trace_id = f"ext-{cell.seed:04d}"
        try:
            result = run_trace_briefed(
                cell.seed, cell.variant, cell.condition, TraceCallWindow(client),
                brief_version=brief_version,
                prompt_version=manifest["prompt_version"],
                max_stage_attempts=manifest["stage_parse_attempts"],
                max_output_bytes=manifest["max_output_bytes"],
                stop_on_failure=True)
        except LedgerError as exc:
            rows.append({"trace_id": trace_id, "seed": cell.seed, "variant": cell.variant,
                         "condition": cell.condition, "outcome": INVALID,
                         "invalid_reason": f"ledger_stop:{type(exc).__name__}"})
            stopped = f"ledger refused or failed in {trace_id}: {exc}"
            break
        record_calls(manifest, store, provenance, result, trace_id)
        # Provenance is written first: the calls were dispatched and charged, so the
        # evidence must exist even on the failing path. Then delivery is checked, and
        # an unverifiable trace stops the run rather than being recorded as versioned.
        delivery = check_brief_delivery(result, trace_id)
        outcome, reason = joint_outcome(result)
        served = sorted({c["served_model"] for c in result["calls"]
                         if isinstance(c.get("served_model"), str)})
        rows.append({
            "trace_id": trace_id, "seed": cell.seed, "variant": cell.variant,
            "condition": cell.condition, "outcome": outcome, "invalid_reason": reason,
            "sender": result["sender"], "receiver": result["receiver"],
            "sender_brief": result["sender_brief"],
            "receiver_brief": result["receiver_brief"],
            "brief_version": result["brief_version"],
            "sender_brief_display_id": result["sender_brief_display_id"],
            "receiver_brief_display_id": result["receiver_brief_display_id"],
            "sender_brief_sha256": result["sender_brief_sha256"],
            "receiver_brief_sha256": result["receiver_brief_sha256"],
            "brief_delivery": delivery,
            "s1": result["s1"], "r1": result["r1"], "s2": result["s2"],
            "s3": result["s3"], "r2": result["r2"],
            "stopped_after": result["stopped_after"],
            "prompt_version": result["prompt_version"],
            "requested_model": manifest["model"], "served_models": served,
        })
        if any(m != manifest["model"] for m in served):
            stopped = f"served model {served} differs from requested {manifest['model']}"
            break
        events = [e for e in store.load()["events"] if e["run_id"] == manifest["run_id"]]
        if any(e["state"] == "unknown" for e in events):
            stopped = f"unknown-cost event after {trace_id}"
            break
        if any(e.get("settled_micro") is not None and e["settled_micro"] > e["reserved_micro"]
               for e in events):
            stopped = f"a settlement exceeded its reservation after {trace_id}"
            break

    summary = summarize_generation_probe(
        manifest, rows, stopped, budget,
        source=source_state(repo_root, observed=source_observed),
        raw_log_path=None if raw_log is None else str(raw_log.path),
        brief_version=brief_version, brief_plan=brief_plan)
    return {"summary": summary, "rows": rows}


__all__ = [
    "ROOT", "PROTECTED_DIR_NAMES", "GENERATION_PROVENANCE_EXTRA_FIELDS",
    "GENERATION_PROBE_PROTOCOL_VERSION", "ProbePathError", "assert_writable_target",
    "source_state", "RawRecordingClient", "build_call_record", "record_calls",
    "summarize_generation_probe", "run_generation_probe", "write_probe_outputs",
    "BRIEF_VERSION_FIELD", "TraceCallWindow", "manifest_brief_version",
    "brief_plan_summary", "check_brief_delivery", "BriefVersionError",
    "BASE_BRIEF_VERSION",
]

