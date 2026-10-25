#!/usr/bin/env python3
"""E1 message-factorial experiment runner (offline by default).

Modes
-----
``--analyze-run-dir DIR``
    Analyse exactly one finished E1 run directory. Writes ``analysis.json`` and
    ``analysis.txt`` beside the run, refusing to overwrite either. No network, no
    ledger, no model call.

``--dry-run`` (default behaviour)
    Execute the plan against an in-process fake receiver keyed by cell id. Zero
    provider calls and zero spend-config / ledger access: no spend config or
    ledger file is opened and no guarded client is constructed. (The
    ``monitors.client`` module is imported transitively by ``agents.runner``, but
    nothing in it is instantiated on this path.)

``--confirm-live``
    Execute the plan against the real guarded client for one approved E1 arm.
    Requires a separate, explicit authorisation (``--i-have-live-authorization``),
    an explicit ``--base-url``, and it must name an approved arm. The endpoint is
    authorised by exact match against :data:`E1_APPROVED_ENDPOINT`; see the
    endpoint-authorisation bullet below and ``docs/open_questions.md (retired register; item text in git history)`` §K.7.

Approved E1 arms (allowlist; the CLI cannot point E1 anywhere else)
------------------------------------------------------------------
| arm alias   | model                          | spend config                  | E1 ledger                                  |
|-------------|--------------------------------|-------------------------------|--------------------------------------------|
| ``sonnet46``| ``openrouter/anthropic/claude-sonnet-4.6`` | ``configs/spend.json``        | ``results/spend_e1/sonnet46/ledger.json``  |
| ``gpt54mini``| ``openrouter/openai/gpt-5.4-mini`` | ``configs/spend_gpt54mini.json`` | ``results/spend_e1/gpt54mini/ledger.json`` |

Both arms use ``allocation="pilot_audit"``, ``phase="pilot"``, whose configured
effective cap is **$2.00 per ledger** (the binding minimum of the $14.00
allocation cap, the $2.00 pilot phase cap and the $37.34 local backstop). The
serviceable E1 aggregate is therefore **$4.00** across the two ledgers.

Safety properties implemented here
----------------------------------
* **Project-level single writer.** ``results/spend_e1/e1_run.lock`` is created
  exclusively and held across the whole spend-bearing critical section:
  preflight, aggregate-budget check, ledger run opening, every reservation and
  dispatch, per-trial persistence, and the terminal status write. A second E1
  run — different model, different output directory — fails closed while it is
  held. The per-run-directory lease is a *separate*, weaker mechanism and is not
  a substitute.
* **Stale locks are never removed automatically.** A lock left by a hard kill is
  reported with its holder payload; recovery is a deliberate, documented manual
  step (``STALE_LOCK_RECOVERY`` below). No automatic expiry, no ``--force``.
* **Aggregate budget preflight.** Both approved ledgers are read read-only, their
  commitments are summed (each exactly once, stale/``unknown`` reservations
  retained at their worst case), and the run's own worst-case reservation is
  added. The run is refused if the total would exceed the $4.00 approved ceiling.
  **This is a point-in-time sum of two independently-enforced ledgers, not an
  atomic cross-ledger budget controller.** The single-writer lock is what
  prevents a concurrent run from racing it. It is **not** a provider-side billing
  ceiling.
* **Exclusive output-directory creation** (``exist_ok=False``): a finished run is
  never overwritten; a replacement run uses a new id and records
  ``supersedes_run_id``/``supersession_reason``.
* **Append-per-record journaling** with ``flush`` + ``fsync`` before the next
  trial starts, so an interruption cannot lose an already-completed result.
* Explicit ``interrupted`` / ``failed`` / ``not_attempted`` statuses, so an
  incomplete run is never reported as complete and nothing is imputed.
* **No re-dispatch** of a trial that has already been attempted, and
  deterministic ``call_id`` per ``(run_id, scenario_id, cell_id)`` so a restart
  reproduces the same reservation key instead of paying twice.
* **Served-model pricing guard:** if the provider reports a served model that
  cannot be resolved to a configured price, the run stops before dispatching
  anything further, and the trial is recorded as ``failed`` rather than being
  settled as a success.
* **Endpoint authorisation.** Each approved arm pins the one provider endpoint it
  may ever be run against (``E1Arm.endpoint`` == ``E1_APPROVED_ENDPOINT``).
  Authorisation is an exact match on the normalised ``scheme://host:port/path``,
  enforced **twice**: in ``live_strategy_resolvers`` (after ordinary URL
  validation, before the project lock) and again in ``build_e1_client`` — the only
  place E1 constructs a live client — so bypassing the resolver cannot reach the
  transport. Both checks live in this E1-owned script; ``monitors/client.py`` is
  deliberately left unmodified because it is SHA-256-pinned by the frozen
  ``experiments/generation_probe_obj10_v1/manifest.json``.
  An arbitrary host, an IP literal, an unexpected port, an alternate path, a query
  string or embedded credentials all fail closed. HTTPS syntax alone is never
  sufficient, and there is no substring or suffix matching. ``--base-url`` is
  mandatory for ``--confirm-live``, so the CLI/environment agreement check (a
  separate, weaker control) can never be skipped.

This script does not modify the shared ledger, the spend configuration, the
caps, or any Phase A / Phase B artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agents.client import FakeAgentClient  # noqa: E402
from agents.parser import RETRY_MESSAGE  # noqa: E402
from agents.prompts import MAX_OUTPUT_TOKENS, receiver_system  # noqa: E402
from agents.runner import STATUS_API_ERROR, _call_stage  # noqa: E402

from apart.experiment_e1 import (  # noqa: E402
    ANALYSIS_JSON_FILENAME,
    ANALYSIS_TEXT_FILENAME,
    BOOT_SEED,
    CELL_PAYOFF,
    CELL_TARGET,
    CELLS,
    E1InputError,
    E1LiveBlocked,
    E1TrialResult,
    EXEC_DRY_RUN_FAKE,
    EXEC_FAILED,
    EXEC_INTERRUPTED,
    EXEC_LIVE,
    EXEC_NOT_ATTEMPTED,
    EXPERIMENT,
    N_BOOT,
    N_CELLS,
    N_SCENARIOS,
    PLAN_DIGEST_FILENAME,
    PLAN_FILENAME,
    PLAN_VERSION,
    RECEIVER_BRIEF,
    RECEIVER_MAX_ATTEMPTS,
    RUN_NAMESPACE,
    STATUS_FILENAME,
    SUMMARY_FILENAME,
    TRIALS_FILENAME,
    analyze_run_dir,
    build_plan,
    call_id_for,
    dumps,
    message_for,
    message_id_for,
    plan_bytes,
    plan_sha256,
    planned_trials,
    provider_seed,
    read_jsonl,
    receiver_prompt,
    render_table,
    scenario_index,
    trial_id_for,
    utc_now,
    validate_plan,
)
from apart.experiment_e1_adapter import (  # noqa: E402
    convert_many,
    conversion_report,
)

RUN_LOCK_FILENAME = "run.lock"

#: Deliberate, manual recovery procedure for a lock left by a hard kill.
#: Never applied automatically: no expiry, no ``--force``, no auto-unlink.
STALE_LOCK_RECOVERY = (
    "A stale E1 run lock is NEVER removed automatically. To recover deliberately:\n"
    "  1. read the holder payload in results/spend_e1/e1_run.lock (run_id, arm, pid,\n"
    "     started_utc);\n"
    "  2. confirm no process for that pid is still running, and that the run's output\n"
    "     directory has reached a terminal status in run_status.json;\n"
    "  3. only then delete the lock file by hand.\n"
    "Recovery never releases ledger reservations and never re-dispatches a call that\n"
    "may already have reached the provider: reservations stay committed at their\n"
    "worst case, and a replacement run must use a new run id and a new directory."
)

#: Default wall-clock-free run id: namespaced, model-tagged, versioned, unique.
RUN_ID_FMT = RUN_NAMESPACE + "-{model_alias}-v1-{stamp}"

# ------------------------------------------------------- approved E1 arms

#: The two approved per-ledger effective caps: (pilot_audit, pilot) is $2.00,
#: i.e. the binding minimum of the $14.00 allocation cap, the $2.00 pilot phase
#: cap and the $37.34 local backstop. Their sum is the E1 aggregate ceiling.
E1_APPROVED_PAIR_CAP_MICRO = 2_000_000
E1_AGGREGATE_CEILING_MICRO = 2 * E1_APPROVED_PAIR_CAP_MICRO          # $4.00

E1_LEDGER_ROOT = ROOT / "results" / "spend_e1"
CANONICAL_LEDGER_PATH = ROOT / "results" / "spend" / "ledger.json"
E1_GLOBAL_LOCK_PATH = E1_LEDGER_ROOT / "e1_run.lock"

#: The single approved E1 provider endpoint, pinned here rather than read from the
#: environment. Authorisation is by exact match on the normalised
#: ``scheme://host:port/path`` (see ``monitors.client.normalize_endpoint``): an
#: arbitrary host, an IP literal, an unexpected port, an alternate path, a query
#: string or embedded credentials all fail closed. There is deliberately no
#: substring or suffix matching.
E1_APPROVED_ENDPOINT = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class E1Arm:
    """One approved receiver arm: alias -> (provider, model, endpoint, config, ledger, pair).

    ``endpoint`` binds the arm to the one provider endpoint it may ever be run
    against, so endpoint authorisation does not depend on whatever value happens
    to be sitting in ``APART_BASE_URL``.
    """

    alias: str
    provider: str
    model: str
    endpoint: str
    spend_config: Path
    ledger_path: Path
    allocation: str = "pilot_audit"
    phase: str = "pilot"


APPROVED_E1_ARMS: dict[str, E1Arm] = {
    "sonnet46": E1Arm(
        alias="sonnet46",
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        endpoint=E1_APPROVED_ENDPOINT,
        spend_config=ROOT / "configs" / "spend.json",
        ledger_path=E1_LEDGER_ROOT / "sonnet46" / "ledger.json",
    ),
    "gpt54mini": E1Arm(
        alias="gpt54mini",
        provider="openrouter",
        model="openai/gpt-5.4-mini",
        endpoint=E1_APPROVED_ENDPOINT,
        spend_config=ROOT / "configs" / "spend_gpt54mini.json",
        ledger_path=E1_LEDGER_ROOT / "gpt54mini" / "ledger.json",
    ),
}


def resolve_arm(model_alias: Any) -> E1Arm:
    """The approved arm for ``model_alias``; fail closed for anything else."""
    arm = APPROVED_E1_ARMS.get(str(model_alias))
    if arm is None:
        raise E1LiveBlocked(
            f"unknown_e1_model_alias: {model_alias!r} is not an approved E1 arm; "
            f"approved arms are {sorted(APPROVED_E1_ARMS)}")
    return arm


def normalize_endpoint(url: str) -> str:
    """Canonical ``scheme://host:port/path`` for **exact** endpoint comparison.

    Materialises the scheme's default port and lower-cases the scheme and host, so
    ``https://example.com:443/api`` and ``https://example.com/api`` compare equal.
    ``monitors.client.validate_base_url`` is applied first, so non-HTTPS URLs,
    credentials and query strings are rejected here too; fragments are rejected as
    well.

    This is a *normaliser*, not an authorisation check — see
    :func:`require_approved_e1_endpoint`. An IP literal normalises fine, which is
    precisely why an IP can never match a hostname allowlist entry.

    It lives here, in the E1 runner, rather than in ``monitors/client.py``,
    deliberately: ``monitors/client.py`` is SHA-256-pinned by the frozen
    ``experiments/generation_probe_obj10_v1/manifest.json``, so editing it would
    invalidate another owner-approved experiment's integrity record.
    """
    from monitors.client import SpendConfigError, validate_base_url

    parsed = urllib.parse.urlparse(validate_base_url(url))
    if parsed.fragment:
        raise SpendConfigError("base URL must not contain a fragment")
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        raise SpendConfigError(f"base URL must contain a host (got {url!r})")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SpendConfigError(f"base URL has an invalid port (got {url!r})") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port}{parsed.path}"


def build_e1_client(*, guard: Any, base_url: str, arm: E1Arm) -> Any:
    """The **only** place E1 constructs a live client — and the second enforcement point.

    Re-authorises the endpoint immediately before construction, then re-checks the
    constructed client's endpoint, so no E1 caller can reach the transport with an
    endpoint that is not the one approved for the arm — even a caller that skipped
    :func:`live_strategy_resolvers`.

    ``max_attempts=1`` is pinned deliberately: each HTTP attempt is a separate
    ledger reservation, so the client default of 4 would quadruple the worst case.
    (This is enforced here rather than in ``monitors/client.py`` because that file
    is SHA-256-pinned by the frozen
    ``experiments/generation_probe_obj10_v1/manifest.json``.)
    """
    from monitors.client import ModelClient, SpendConfigError

    require_approved_e1_endpoint(base_url, arm)
    try:
        client = ModelClient(guard=guard, base_url=base_url, load_env=False, max_attempts=1)
    except SpendConfigError as exc:
        raise E1LiveBlocked(f"endpoint_not_approved: {exc}") from exc
    if normalize_endpoint(client.base_url) != normalize_endpoint(arm.endpoint):
        raise E1LiveBlocked(
            "endpoint_not_approved: the constructed client points at "
            f"{normalize_endpoint(client.base_url)}, not this arm's approved endpoint "
            f"{normalize_endpoint(arm.endpoint)}")
    return client


def require_approved_e1_endpoint(url: str, arm: E1Arm) -> str:
    """Fail closed unless ``url`` is exactly the endpoint approved for ``arm``.

    The approval is bound to the **arm** (provider + model), not to whatever value
    happens to be in ``APART_BASE_URL``, so a stale, empty or tampered environment
    cannot redirect an E1 run to a different endpoint. Comparison is exact on the
    normalised ``scheme://host:port/path``: an arbitrary host, an IP literal, an
    unexpected port, an alternate path, a query string or embedded credentials all
    fail. There is deliberately no substring, suffix or prefix matching.

    Returns the normalised endpoint on success. Enforced at **two** boundaries:
    :func:`live_strategy_resolvers` (read-only resolution) and
    :func:`build_e1_client` (client construction).
    """
    try:
        normalized = normalize_endpoint(url)
    except Exception as exc:
        raise E1LiveBlocked(
            f"endpoint_not_approved: E1 arm {arm.alias!r} is approved only for "
            f"{arm.endpoint!r}; the supplied endpoint is not a usable URL: {exc}") from exc
    approved = normalize_endpoint(arm.endpoint)
    if normalized != approved:
        raise E1LiveBlocked(
            f"endpoint_not_approved: E1 arm {arm.alias!r} is approved only for {approved}; "
            f"got {normalized}. An endpoint that is not the approved one for this arm is "
            "never authorised, however it was supplied.")
    return normalized


# ------------------------------------------------- request token upper bound


#: Deliberate over-estimate of the reply carried into the parse-retry request.
#: The provider is capped at ``MAX_OUTPUT_TOKENS == 150`` tokens; 1200 bytes is a
#: generous <= 8 bytes/token allowance, so the retry bound stays an upper bound
#: without needing the real reply text at preflight time.
RETRY_REPLY_BYTE_BOUND = 1200


def request_input_token_bound(messages: Sequence[Mapping[str, Any]]) -> int:
    """Conservative token upper bound for a request's messages.

    Mirrors ``monitors.client._input_token_bound`` exactly: the UTF-8 byte length
    of each message as JSON plus 64. It is duplicated rather than imported so the
    E1 runner depends only on public APIs; ``test_e1_token_bound_matches_production``
    pins the two together so drift is caught.
    """
    total = 0
    for message in messages:
        try:
            total += len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            total += 1024
    return total + 64


def e1_max_input_bounds(plan: Mapping[str, Any]) -> tuple[int, int]:
    """``(first-attempt bound, parse-retry bound)`` over every planned trial.

    Computed from the frozen plan, so it tracks the messages automatically.
    """
    system_message = {"role": "system", "content": receiver_system(RECEIVER_BRIEF)}
    lookup = scenario_index(plan)
    first_bound = retry_bound = 0
    for scenario_id, cell_id in planned_trials(plan):
        variant_index = lookup[scenario_id]["variant_index"]
        base = [system_message,
                {"role": "user", "content": receiver_prompt(message_for(cell_id, variant_index))}]
        first_bound = max(first_bound, request_input_token_bound(base))
        retry_bound = max(retry_bound, request_input_token_bound(
            base + [{"role": "assistant", "content": "X" * RETRY_REPLY_BYTE_BOUND},
                    {"role": "user", "content": RETRY_MESSAGE}]))
    return first_bound, retry_bound


def worst_case_run_micro(policy: Any, plan: Optional[Mapping[str, Any]] = None) -> int:
    """Total worst-case reservation for the whole E1 run under one receiver model.

    ``N_SCENARIOS * N_CELLS`` trials, each allowed one parse retry. The caller must
    supply the *policy of the arm being run*, because the reservation depends on
    that arm's configured price.
    """
    plan = plan if plan is not None else build_plan()
    first_bound, retry_bound = e1_max_input_bounds(plan)
    per_trial = (policy.worst_case_micro(first_bound, MAX_OUTPUT_TOKENS)
                 + policy.worst_case_micro(retry_bound, MAX_OUTPUT_TOKENS))
    return per_trial * N_SCENARIOS * N_CELLS


#: ``error`` value that marks a trial as run-stopping rather than merely failed.
FATAL_SERVED_MODEL_UNPRICED = "served_model_price_unresolved"


def resolve_served_price(policy: Any, served_model: Any, served_provider: Any):
    """The configured price for what the provider says it served, or ``None``.

    Mirrors the resolution order in ``monitors.ledger.Policy.cost_micro``: the
    reporting provider is tried first, then the configured provider, each against
    the reported served model. ``None`` means the call cannot be priced, which the
    ledger would record as ``unknown`` — the E1 runner treats that as a
    run-stopping condition instead of continuing to dispatch.
    """
    provider, model = policy.require_model()
    served = served_model if isinstance(served_model, str) and served_model.strip() else model
    if isinstance(served_provider, str) and served_provider.strip():
        price = policy.prices.get(f"{served_provider}/{served}")
        if price is not None:
            return price
    return policy.prices.get(f"{provider}/{served}")


# --------------------------------------------------------------- dry-run fake


class CellKeyedFakeResponder:
    """Deterministic E1 fake receiver keyed by **cell id**, never by message text.

    The runner calls :meth:`set_trial` immediately before each trial, so the
    reply depends on structured plan metadata rather than on string matching. As a
    self-check the responder additionally requires the request's user prompt to
    equal, byte-for-byte, the prompt the runner expected to build for that
    cell/variant; a mismatch raises instead of returning a plausible answer.

    This class performs no I/O and holds no client: it is structurally incapable
    of reaching a provider.
    """

    def __init__(self) -> None:
        self.cell_id: Optional[str] = None
        self.expected_prompt: Optional[str] = None
        self.calls: list[dict] = []

    def set_trial(self, cell_id: str, expected_prompt: str) -> None:
        if cell_id not in CELLS:
            raise E1InputError(f"fake responder got unknown cell {cell_id!r}")
        self.cell_id = cell_id
        self.expected_prompt = expected_prompt

    def __call__(self, messages: Sequence[Mapping[str, Any]], temperature: float,
                 seed: Optional[int], index: int) -> str:
        if self.cell_id is None:
            raise E1InputError("fake responder used before set_trial")
        if not messages:
            raise E1InputError("fake responder received no messages")
        prompt = messages[-1].get("content")
        if prompt != self.expected_prompt:
            raise E1InputError(
                "fake responder received a prompt that is not the expected exact "
                "text for this cell/variant")
        self.calls.append({"cell_id": self.cell_id, "seed": seed, "index": index})
        action = "B" if CELL_TARGET[self.cell_id] == "B" else "A"
        return json.dumps({"action": action, "note": "e1-fake"})


# ------------------------------------------------------------------- run lease


class ExecutionLease:
    """Exclusive, run-directory-local execution lease. Fails closed.

    Mechanism only. It serialises access to **one E1 run directory**; it is not a
    project-wide single-writer policy, it does not lock the shared spend ledger,
    and its existence is not evidence that concurrent E1 runs are prevented. The
    ledger's own advisory ``flock`` is a separate mechanism and is not touched
    here.
    """

    def __init__(self, run_dir: Any, payload: Optional[Mapping[str, Any]] = None) -> None:
        self.path = Path(run_dir) / RUN_LOCK_FILENAME
        self.payload = dict(payload or {})
        self._held = False

    def acquire(self) -> "ExecutionLease":
        if self._held:
            return self
        try:
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise E1LiveBlocked(
                f"another E1 run holds the execution lease at {self.path}; refusing to "
                "start a second writer") from exc
        try:
            os.write(descriptor, (json.dumps(self.payload, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._held = True
        return self

    def release(self) -> None:
        if self._held and self.path.exists():
            self.path.unlink()
        self._held = False

    def __enter__(self) -> "ExecutionLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


# ------------------------------------------------------- project-level E1 lock


class E1ProjectLock:
    """Project-level single-writer lock for E1 spend-bearing runs.

    Exactly one E1 live run may be in flight, **across both receiver arms and all
    output directories**. The lock is a fixed path created with ``O_CREAT|O_EXCL``
    and is held for the whole spend-bearing critical section: preflight, the
    aggregate-budget check, opening the ledger run, every reservation and
    dispatch, per-trial durable persistence, and the terminal status write.

    This is deliberately *not* the same thing as
    :class:`ExecutionLease` (which only prevents two writers in one run
    directory) and not the same thing as the per-ledger advisory ``flock``
    (which only serialises one ledger file). Those mechanisms cannot prevent two
    concurrent E1 runs from being admitted against two different ledgers, which
    is exactly what the aggregate-budget check needs.

    Stale locks are never removed automatically. See :data:`STALE_LOCK_RECOVERY`.
    """

    def __init__(self, path: Any = None, payload: Optional[Mapping[str, Any]] = None) -> None:
        self.path = Path(path) if path is not None else E1_GLOBAL_LOCK_PATH
        self.payload = dict(payload or {})
        self._held = False

    def acquire(self) -> "E1ProjectLock":
        if self._held:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            holder = describe_lock(self.path)
            raise E1LiveBlocked(
                "e1_run_lock_held: another E1 spend-bearing run holds the project lock "
                f"at {self.path}; refusing to start a second run.\n"
                f"holder: {json.dumps(holder, sort_keys=True)}\n"
                f"{STALE_LOCK_RECOVERY}") from exc
        except OSError as exc:
            raise E1LiveBlocked(f"e1_run_lock_unavailable: cannot create {self.path}: {exc}") from exc
        try:
            os.write(descriptor, (json.dumps(self.payload, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._held = True
        return self

    def release(self) -> None:
        """Release only a lock this instance acquired. Never used for recovery."""
        if self._held and self.path.exists():
            self.path.unlink()
        self._held = False

    def __enter__(self) -> "E1ProjectLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def describe_lock(path: Any = None) -> dict:
    """Read-only description of a lock file, for a deliberate manual recovery.

    Returns the holder payload plus the file's mtime, or ``{"present": False}``.
    Never removes, truncates or rewrites the file.
    """
    target = Path(path) if path is not None else E1_GLOBAL_LOCK_PATH
    if not target.exists():
        return {"present": False, "path": str(target)}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    info: dict = {"present": True, "path": str(target)}
    try:
        info["mtime_utc"] = datetime.fromtimestamp(
            target.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        pass
    if isinstance(payload, dict):
        info["holder"] = payload
        info["pid_alive"] = _pid_alive(payload.get("pid"))
    return info


def _pid_alive(pid: Any) -> Optional[bool]:
    """Best-effort liveness check for a recorded holder. ``None`` if unknown."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return None
    return True


# ---------------------------------------------------------------- persistence


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write ``payload`` to ``path`` via a temp file + ``os.replace`` + fsync."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")
    descriptor = os.open(str(temporary), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def write_new_file(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively and durably. Refuses to overwrite."""
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TrialJournal:
    """Append-only trial journal with durability stronger than the call cadence.

    Every completed trial is written, ``flush``ed and ``fsync``ed **before** the
    next trial is constructed, so an interruption (``KeyboardInterrupt``, a
    transport error, a serialization error, a kill) cannot discard a result that
    has already been paid for. The run status file is replaced atomically, so a
    reader never sees a half-written record.
    """

    def __init__(self, run_dir: Any) -> None:
        self.run_dir = Path(run_dir)
        self.trials_path = self.run_dir / TRIALS_FILENAME
        self.status_path = self.run_dir / STATUS_FILENAME
        self.n_durable = 0
        self._handle = None

    def open(self) -> "TrialJournal":
        if self._handle is None:
            self._handle = open(self.trials_path, "a", encoding="utf-8")
        return self

    def append(self, record: Mapping[str, Any]) -> None:
        """Append one record and make it durable. Never buffers silently."""
        if self._handle is None:
            raise RuntimeError("TrialJournal.append() before open()")
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
        self._handle.write(line + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.n_durable += 1

    def write_status(self, payload: Mapping[str, Any]) -> None:
        _atomic_write_json(self.status_path, payload)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "TrialJournal":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class E1DispatchGate:
    """Refuses a second dispatch of the same ``(call_id, attempt)`` in one process.

    The authoritative cross-process guarantee against re-paying a settled call is
    the ledger's ``DuplicateReservation`` check, which is keyed on the same
    deterministic ``call_id``. This gate is a local, offline-testable backstop
    against an accidental double-dispatch inside the loop; it does not replace the
    ledger mechanism and makes no claim about concurrent processes.
    """

    def __init__(self) -> None:
        self.seen: set[tuple[str, int]] = set()
        self.dispatches: list[tuple[str, int]] = []

    def check(self, call_id: str, attempt: int) -> None:
        key = (call_id, attempt)
        if key in self.seen:
            raise E1LiveBlocked(
                f"refusing to re-dispatch call {call_id!r} attempt {attempt}: already "
                "dispatched in this run")
        self.seen.add(key)
        self.dispatches.append(key)

    def __len__(self) -> int:
        return len(self.dispatches)


# ------------------------------------------------------------ trial execution


def build_record(plan: Mapping[str, Any], scenario: Mapping[str, Any], cell_id: str,
                 run_id: str, run_mode: str, plan_digest: str, status: str, *,
                 client: Any = None, supersedes_run_id: Optional[str] = None,
                 supersession_reason: Optional[str] = None) -> E1TrialResult:
    """Build a schema-complete E1 record for one planned trial.

    Every provenance field is filled from the plan, so a record can only be
    incomplete if the plan itself is, and ``validate_record`` will catch that.
    """
    variant_index = scenario["variant_index"]
    message = message_for(cell_id, variant_index)
    record = E1TrialResult(
        run_id=run_id,
        plan_version=plan["plan_version"],
        plan_sha256=plan_digest,
        scenario_id=scenario["scenario_id"],
        cell_id=cell_id,
        target=CELL_TARGET[cell_id],
        payoff_mention=CELL_PAYOFF[cell_id],
        variant_index=variant_index,
        variant_label=scenario["variant_label"],
        message_id=message_id_for(cell_id, variant_index),
        message_text=message,
        message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
        sender_action=scenario["sender_action"],
        trial_id=trial_id_for(scenario["scenario_id"], cell_id),
        run_mode=run_mode,
        execution_status=status,
        timestamp_utc=utc_now(),
        agent_prompt_version=plan["agent_prompt_version"],
        agent_system_prompt_sha256=plan["agent_system_prompt_sha256"],
        requested_model=getattr(client, "model", None) if client is not None else None,
        run_seed=provider_seed(scenario["scenario_id"], cell_id),
        supersedes_run_id=supersedes_run_id,
        supersession_reason=supersession_reason,
    )
    if status != EXEC_NOT_ATTEMPTED:
        record.call_id = call_id_for(run_id, scenario["scenario_id"], cell_id)
    return record


def execute_trial(plan: Mapping[str, Any], scenario: Mapping[str, Any], cell_id: str,
                  client: Any, run_id: str, run_mode: str, plan_digest: str, *,
                  supersedes_run_id: Optional[str] = None,
                  supersession_reason: Optional[str] = None,
                  price_policy: Any = None) -> E1TrialResult:
    """Run one trial. Receiver failures are recorded, never imputed.

    Transport/ledger/persistence errors propagate so the caller can stop the run;
    a reply that is unparseable, refused, or names no A/B action is recorded with
    an explicit non-valid validity and is never re-run inside this call beyond the
    single permitted parse retry.

    ``price_policy`` (the arm's ``Policy``) enables the **served-model pricing
    guard**: if the provider reports a served model that cannot be resolved to a
    configured price, the trial is recorded as ``failed`` with
    ``error == FATAL_SERVED_MODEL_UNPRICED`` and the caller stops the run. The
    ledger would otherwise settle it as ``unknown``; that is conservative but it
    silently widens the worst case, so E1 refuses to keep dispatching instead.
    """
    record = build_record(plan, scenario, cell_id, run_id, run_mode, plan_digest,
                          run_mode, client=client,
                          supersedes_run_id=supersedes_run_id,
                          supersession_reason=supersession_reason)
    parsed, response, log = _call_stage(
        client, receiver_system(RECEIVER_BRIEF), receiver_prompt(record.message_text),
        "action", provider_seed(scenario["scenario_id"], cell_id),
        call_id=record.call_id, max_attempts=RECEIVER_MAX_ATTEMPTS,
    )
    record.call_log = log
    record.attempt = parsed.attempts
    record.receiver_raw_output = None if response is None else getattr(response, "text", None)
    record.served_model = getattr(response, "served_model", None) if response is not None else None
    record.served_provider = getattr(response, "served_provider", None) if response is not None else None

    if price_policy is not None and response is not None:
        if resolve_served_price(price_policy, record.served_model, record.served_provider) is None:
            # Run-stopping: the reply arrived but cannot be priced. No validity
            # verdict is recorded, and the caller must stop rather than dispatch
            # further calls.
            record.execution_status = EXEC_FAILED
            record.error = FATAL_SERVED_MODEL_UNPRICED
            record.error_stage = "receiver"
            record.validity = None
            record.parsed_action = None
            return record

    if parsed.status == STATUS_API_ERROR:
        # Transport failure: no validity verdict exists, so none is recorded.
        record.execution_status = EXEC_FAILED
        record.error = "api_error"
        record.error_stage = "receiver"
    elif parsed.status != "ok":
        record.validity = "invalid"
        record.reason = parsed.reason or parsed.status
    elif parsed.data is None or parsed.data.get("action") is None:
        record.validity = "ambiguous"
        record.reason = "non_ab_action"
    else:
        record.validity = "valid"
        record.parsed_action = parsed.data["action"]
    return record


# ---------------------------------------------------------------- run driver


@dataclass
class RunOutcome:
    """Outcome of one E1 run. ``status`` is one of completed/interrupted/failed."""

    run_id: str
    run_mode: str
    status: str
    n_planned: int
    n_attempted: int
    n_failed: int
    n_not_attempted: int
    stop_reason: Optional[str] = None
    error: Optional[str] = None
    exit_code: int = 0

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id, "run_mode": self.run_mode, "status": self.status,
            "n_planned": self.n_planned, "n_attempted": self.n_attempted,
            "n_failed": self.n_failed, "n_not_attempted": self.n_not_attempted,
            "stop_reason": self.stop_reason, "error": self.error,
            "exit_code": self.exit_code,
        }


def run_plan(plan: Mapping[str, Any], client: Any, run_id: str, run_mode: str,
             out_dir: Any, *, responder: Optional[CellKeyedFakeResponder] = None,
             supersedes_run_id: Optional[str] = None,
             supersession_reason: Optional[str] = None,
             gate: Optional[E1DispatchGate] = None,
             lease: Optional[ExecutionLease] = None,
             model_alias: Optional[str] = None,
             price_policy: Any = None) -> RunOutcome:
    """Execute every planned trial once, journalling each result before the next.

    Never re-dispatches a trial that has already been attempted, and never resumes
    an incomplete run in place: an interruption marks the remaining planned trials
    ``not_attempted`` and returns ``interrupted``/``failed``; a replacement run
    must use a new ``run_id`` in a new directory.

    ``price_policy`` enables the served-model pricing guard (see
    :func:`execute_trial`). When it fires the run stops immediately: the remaining
    planned trials are recorded ``not_attempted`` and the run status is ``failed``.
    """
    validate_plan(plan)
    out_dir = Path(out_dir)
    plan_digest = plan_sha256(plan)
    scenarios = scenario_index(plan)
    planned = planned_trials(plan)
    gate = gate if gate is not None else E1DispatchGate()
    lease = lease if lease is not None else ExecutionLease(
        out_dir, {"run_id": run_id, "run_mode": run_mode})
    started = utc_now()

    counters = {"n_attempted": 0, "n_failed": 0}

    def status_payload(current_status: str, not_attempted: int) -> dict:
        return {
            "schema": "apart-e1-run-status/1",
            "experiment": EXPERIMENT,
            "run_id": run_id,
            "run_mode": run_mode,
            "status": current_status,
            "plan_version": plan["plan_version"],
            "plan_sha256": plan_digest,
            "model_alias": model_alias,
            "requested_model": getattr(client, "model", None) if client is not None else None,
            "supersedes_run_id": supersedes_run_id,
            "supersession_reason": supersession_reason,
            "n_planned_trials": len(planned),
            "n_attempted": counters["n_attempted"],
            "n_failed": counters["n_failed"],
            "n_not_attempted": not_attempted,
            "dispatched_call_ids": [f"{call}:p{attempt}" for call, attempt in gate.dispatches],
            "started_utc": started,
            "updated_utc": utc_now(),
            "notes": [
                "status is an operational record; it makes no claim about spend",
                "eligibility is decided by complete-case rules at analysis time",
            ],
        }

    status: str = "completed"
    stop_reason: Optional[str] = None
    error_text: Optional[str] = None
    index = 0

    def interrupted_record(scenario_id: str, cell_id: str) -> dict:
        return build_record(plan, scenarios[scenario_id], cell_id, run_id, run_mode,
                            plan_digest, EXEC_INTERRUPTED, client=client,
                            supersedes_run_id=supersedes_run_id,
                            supersession_reason=supersession_reason).as_record()

    with lease:
        with TrialJournal(out_dir) as journal:
            journal.write_status(status_payload("running", len(planned)))
            try:
                while index < len(planned):
                    scenario_id, cell_id = planned[index]
                    scenario = scenarios[scenario_id]
                    call_id = call_id_for(run_id, scenario_id, cell_id)
                    if responder is not None:
                        responder.set_trial(
                            cell_id,
                            receiver_prompt(message_for(cell_id, scenario["variant_index"])))
                    # Refuse a second dispatch of the same logical call.
                    gate.check(call_id, 1)
                    record = execute_trial(
                        plan, scenario, cell_id, client, run_id, run_mode, plan_digest,
                        supersedes_run_id=supersedes_run_id,
                        supersession_reason=supersession_reason,
                        price_policy=price_policy)
                    journal.append(record.as_record())
                    counters["n_attempted"] += 1
                    if record.execution_status == EXEC_FAILED:
                        counters["n_failed"] += 1
                    index += 1
                    journal.write_status(status_payload("running", len(planned) - index))
                    if record.error == FATAL_SERVED_MODEL_UNPRICED:
                        # The reply arrived but cannot be priced. Stopping here is
                        # deliberate: further dispatches would settle as `unknown`
                        # and silently widen the worst case.
                        status = "failed"
                        stop_reason = FATAL_SERVED_MODEL_UNPRICED
                        error_text = (
                            f"{FATAL_SERVED_MODEL_UNPRICED}: provider reported served model "
                            f"{record.served_model!r} / provider {record.served_provider!r}, "
                            "which has no configured price in this arm's spend config")
                        break
            except KeyboardInterrupt:
                # The in-flight trial may have reached the provider, so it is
                # recorded as interrupted, never as not_attempted.
                journal.append(interrupted_record(*planned[index]))
                index += 1
                status, stop_reason = "interrupted", "keyboard_interrupt"
            except Exception as exc:  # noqa: BLE001 - a paid run must not lose records
                journal.append(interrupted_record(*planned[index]))
                index += 1
                status = "failed"
                stop_reason = type(exc).__name__
                error_text = f"{type(exc).__name__}: {exc}"

            # Every planned trial that was never reached is recorded explicitly.
            for scenario_id, cell_id in planned[index:]:
                journal.append(build_record(
                    plan, scenarios[scenario_id], cell_id, run_id, run_mode, plan_digest,
                    EXEC_NOT_ATTEMPTED, client=client,
                    supersedes_run_id=supersedes_run_id,
                    supersession_reason=supersession_reason).as_record())

            n_not_attempted = len(planned) - index
            final = status_payload(status, n_not_attempted)
            if stop_reason:
                final["stop_reason"] = stop_reason
            if error_text:
                final["error"] = error_text
            journal.write_status(final)

    return RunOutcome(
        run_id=run_id, run_mode=run_mode, status=status, n_planned=len(planned),
        n_attempted=counters["n_attempted"], n_failed=counters["n_failed"],
        n_not_attempted=max(0, len(planned) - index), stop_reason=stop_reason,
        error=error_text, exit_code=0 if status == "completed" else 2)


# ----------------------------------------------------------------- live gating


@dataclass(frozen=True)
class LiveStrategy:
    """The facts a live E1 run needs, as supplied by the operator.

    The operator names an **approved arm alias**; every other value is either
    derived from that arm or checked against it. Providing a config/ledger path
    that differs from the approved mapping is refused rather than honoured, so the
    CLI cannot point E1 at an unapproved configuration or ledger.
    """

    model_alias: str
    spend_config: Path
    ledger_path: Path
    allocation: str
    phase: str
    base_url: str
    approved: bool = False
    max_spend_micro: Optional[int] = None


def preflight_live(strategy: LiveStrategy, *, resolve_price: Callable[[], Any],
                   headroom_micro: Callable[[], int],
                   worst_case_micro: Callable[[], int]) -> dict:
    """Fail-closed live admission check. Pure: no filesystem, no network.

    Callers inject the three lookups, so the whole check is offline-testable. A
    missing approval, an unset allocation/phase pair, a missing endpoint, an
    unpriced model, a non-positive reservation, or headroom below the run's
    worst-case reservation each block dispatch **before** any request is built.

    ``worst_case_micro`` must return the total worst-case reservation for the
    whole run, not one call.
    """
    if not strategy.approved:
        raise E1LiveBlocked(
            "live_execution_not_approved: E1 live runs require a separate, explicit "
            "authorisation naming the ledger strategy, the allocation/phase pair and "
            "a maximum spend")
    if not str(strategy.allocation).strip() or not str(strategy.phase).strip():
        raise E1LiveBlocked(
            "live_allocation_phase_unresolved: no approved allocation/phase pair is "
            "configured for E1")
    if not str(strategy.base_url).strip():
        raise E1LiveBlocked("live_base_url_missing: refusing to dispatch without an endpoint")

    resolved = resolve_price()
    if not resolved:
        raise E1LiveBlocked(
            "live_model_price_unresolved: refusing to dispatch a model with no approved price")

    worst = int(worst_case_micro())
    headroom = int(headroom_micro())
    if worst <= 0:
        raise E1LiveBlocked("live_worst_case_reservation_not_positive")
    if worst > headroom:
        raise E1LiveBlocked(
            f"live_budget_insufficient: worst-case reservation {worst} µ$ exceeds "
            f"available headroom {headroom} µ$")
    if strategy.max_spend_micro is not None and worst > int(strategy.max_spend_micro):
        raise E1LiveBlocked(
            f"live_spend_ceiling_exceeded: worst-case reservation {worst} µ$ exceeds the "
            f"approved ceiling {int(strategy.max_spend_micro)} µ$")
    return {"approved": True, "worst_case_micro": worst, "headroom_micro": headroom,
            "resolved_price": resolved}


def _require_approved_selection(strategy: LiveStrategy, arm: E1Arm) -> None:
    """Refuse any config/ledger path that is not the arm's approved mapping."""
    if Path(strategy.spend_config) != arm.spend_config:
        raise E1LiveBlocked(
            f"spend_config_not_approved: E1 arm {arm.alias!r} is bound to {arm.spend_config}; "
            f"got {strategy.spend_config}")
    if Path(strategy.ledger_path) != arm.ledger_path:
        raise E1LiveBlocked(
            f"ledger_path_not_approved: E1 arm {arm.alias!r} is bound to {arm.ledger_path}; "
            f"got {strategy.ledger_path}")
    if Path(strategy.ledger_path).resolve() == CANONICAL_LEDGER_PATH.resolve():
        raise E1LiveBlocked(
            "canonical_ledger_refused: E1 must never run against the canonical shared ledger "
            f"({CANONICAL_LEDGER_PATH})")


@dataclass(frozen=True)
class E1LiveContext:
    """A resolved, validated, read-only view of one approved E1 arm.

    Building this context performs **no ledger mutation** and no network access.
    The mutating step is :meth:`open_run`, which the caller invokes only after
    preflight and the aggregate-budget check have both passed.
    """

    arm: E1Arm
    strategy: LiveStrategy
    policy: Any
    store: Any
    config_hash: str
    provider: str
    model: str
    base_url: str
    open_run_fn: Any

    def resolve_price(self) -> Optional[dict]:
        price = self.policy.prices.get(f"{self.provider}/{self.model}")
        if price is None:
            return None
        return {"provider": self.provider, "model": self.model,
                "input_per_million": str(price[0]), "output_per_million": str(price[1])}

    def headroom_micro(self) -> int:
        from monitors.ledger import headroom
        ledger = self.store.load(expected_config_hash=self.config_hash)
        return int(headroom(ledger, self.policy, self.arm.allocation, self.arm.phase)["headroom"])

    def worst_case_micro(self) -> int:
        return worst_case_run_micro(self.policy)

    @property
    def preflight_strategy(self) -> "LiveStrategy":
        """The strategy carrying the environment-resolved endpoint.

        ``preflight_live`` checks ``strategy.base_url``; the authoritative endpoint
        is the one validated from the environment inside
        :func:`live_strategy_resolvers`, so the two are reconciled here rather than
        left as an unchecked pair.
        """
        from dataclasses import replace as _replace
        return _replace(self.strategy, base_url=self.base_url)

    def open_run(self, run_id: str, plan_digest: str, by: str) -> tuple[Any, list]:
        """Mutating step: open the ledger run, sweep stale reservations, build the client."""
        return self.open_run_fn(run_id, plan_digest, by)


def aggregate_budget_preflight(*, worst_case_micro: int,
                               ceiling_micro: int = E1_AGGREGATE_CEILING_MICRO,
                               ledger_root: Any = None,
                               approved: Optional[Mapping[str, E1Arm]] = None,
                               ) -> dict:
    """Read-only aggregate check across the approved E1 ledgers.

    Verifies that exactly the approved ledger set exists, loads each one read-only
    against its own config binding, sums the commitments (each counted once,
    including ``reserved`` and ``unknown`` reservations at their worst case — stale
    reservations are **not** released), adds this run's worst-case reservation, and
    refuses if the total would exceed ``ceiling_micro``.

    **Limitation, stated plainly:** this is a point-in-time sum of two independently
    enforced per-ledger caps, *not* an atomic cross-ledger budget controller. It
    cannot, by itself, prevent two concurrent runs from both passing. The
    project-level E1 run lock is what closes that hole. It is also **not** a
    provider-side billing ceiling.
    """
    from monitors.ledger import LedgerStore, Policy, commitments, config_sha256

    approved = dict(approved if approved is not None else APPROVED_E1_ARMS)
    root = Path(ledger_root) if ledger_root is not None else E1_LEDGER_ROOT
    approved_paths = {arm.ledger_path.resolve() for arm in approved.values()}

    if root.is_dir():
        found = {path.resolve() for path in root.glob("*/ledger.json")}
        extras = sorted(str(path) for path in found - approved_paths)
        if extras:
            raise E1LiveBlocked(
                f"unapproved_e1_ledger_present: {extras}; the approved E1 ledger set is "
                f"{sorted(str(path) for path in approved_paths)}. Refusing, because a third "
                "E1 ledger would widen the aggregate ceiling beyond the approved sum.")

    committed_by_arm: dict[str, int] = {}
    for alias in sorted(approved):
        arm = approved[alias]
        if not arm.ledger_path.exists():
            raise E1LiveBlocked(
                f"approved_e1_ledger_missing: {arm.ledger_path} does not exist; refusing to "
                "initialise it implicitly (aggregate budget cannot be established)")
        config_bytes = Path(arm.spend_config).read_bytes()
        policy = Policy.from_config(json.loads(config_bytes.decode("utf-8")))
        ledger = LedgerStore(arm.ledger_path).load(
            expected_config_hash=config_sha256(config_bytes), policy=policy)
        committed_by_arm[alias] = int(commitments(ledger)["total"])

    committed = sum(committed_by_arm.values())
    worst = int(worst_case_micro)
    projected = committed + worst
    if projected > int(ceiling_micro):
        raise E1LiveBlocked(
            f"aggregate_budget_insufficient: committed {committed} µ$ + this run's worst case "
            f"{worst} µ$ = {projected} µ$ exceeds the approved E1 aggregate ceiling "
            f"{int(ceiling_micro)} µ$")
    return {
        "committed_by_arm_micro": committed_by_arm,
        "committed_micro": committed,
        "worst_case_micro": worst,
        "projected_micro": projected,
        "ceiling_micro": int(ceiling_micro),
        "atomic_across_ledgers": False,
        "note": ("Sum of the approved per-ledger caps, checked once, read-only, before "
                 "dispatch. Not an atomic cross-ledger budget controller and not a "
                 "provider-side billing ceiling; the project E1 run lock is what prevents a "
                 "concurrent run from racing this check."),
    }


def live_strategy_resolvers(strategy: LiveStrategy) -> E1LiveContext:
    """Production wiring for one approved E1 arm. Reads files; mutates nothing.

    Mirrors the established production pattern in
    ``scripts/run_phase_a_live.py:open_live_client`` but splits the read-only
    validation from the ledger mutation: this function resolves and validates the
    arm, config, pair, environment, ledger binding and client-construction plan,
    and returns an :class:`E1LiveContext`. The caller performs
    :meth:`E1LiveContext.open_run` only after preflight and the aggregate-budget
    check have both passed, so a refused run never opens a ledger run.

    Every failure mode fails closed before any request could be built: unapproved
    execution, unknown alias, unapproved config/ledger path, unreadable or invalid
    config, model/config mismatch, unapproved or reconfigured pair cap, missing
    environment values, missing ledger, and a ledger bound to a different config
    hash. No secret is read, printed or written.
    """
    if not strategy.approved:
        raise E1LiveBlocked(
            "live_execution_not_approved: E1 live runs require a separate, explicit "
            "authorisation naming the ledger strategy, the allocation/phase pair and a "
            "maximum spend")

    arm = resolve_arm(strategy.model_alias)
    _require_approved_selection(strategy, arm)

    from monitors.client import LedgerGuard, load_apart_env, validate_base_url
    from monitors.ledger import (LedgerStore, Policy, config_sha256, make_run_header,
                                 open_run, sweep_stale)

    config_path = Path(strategy.spend_config)
    try:
        config_bytes = config_path.read_bytes()
        config_payload = json.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise E1LiveBlocked(f"spend_config_unreadable: {config_path}: {exc}") from exc
    config_hash = config_sha256(config_bytes)
    try:
        policy = Policy.from_config(config_payload)
        provider, model = policy.require_model()
    except Exception as exc:  # LedgerError and friends: fail closed, never guess
        raise E1LiveBlocked(f"spend_config_invalid: {config_path}: {exc}") from exc

    if (provider, model) != (arm.provider, arm.model):
        raise E1LiveBlocked(
            f"arm_config_mismatch: E1 arm {arm.alias!r} expects {arm.provider}/{arm.model}, "
            f"but {config_path} names {provider}/{model}")

    if strategy.allocation != arm.allocation or strategy.phase != arm.phase:
        raise E1LiveBlocked(
            f"allocation_phase_not_approved: E1 uses ({arm.allocation!r}, {arm.phase!r}); "
            f"got ({strategy.allocation!r}, {strategy.phase!r})")

    try:
        pair_cap = policy.pair_cap(arm.allocation, arm.phase)
    except Exception as exc:
        raise E1LiveBlocked(
            f"allocation_phase_not_configured: ({arm.allocation!r}, {arm.phase!r}) is not a "
            f"configured pair in {config_path}: {exc}") from exc
    if pair_cap != E1_APPROVED_PAIR_CAP_MICRO:
        raise E1LiveBlocked(
            f"pair_cap_changed: ({arm.allocation!r}, {arm.phase!r}) is {pair_cap} µ$ in "
            f"{config_path}, but the approved E1 cap is {E1_APPROVED_PAIR_CAP_MICRO} µ$ "
            "(2.00 USD); refusing to run against a different ceiling")

    try:
        load_apart_env(ROOT / ".env")
        if not os.environ.get("APART_API_KEY"):
            raise E1LiveBlocked(
                "api_key_missing: APART_API_KEY is not set (its value is never read or printed)")
        if not os.environ.get("APART_BASE_URL"):
            raise E1LiveBlocked("base_url_missing: APART_BASE_URL is not set")
        base_url = validate_base_url(os.environ["APART_BASE_URL"])
    except E1LiveBlocked:
        raise
    except Exception as exc:
        raise E1LiveBlocked(f"endpoint_not_approved: {exc}") from exc

    # Endpoint authorisation: exact match against the endpoint pinned to THIS ARM.
    # This is the authorisation control. The CLI/environment agreement check below
    # is a separate, weaker control layered on top of it, not a substitute.
    require_approved_e1_endpoint(base_url, arm)

    # An explicitly supplied --base-url must agree with the environment endpoint;
    # otherwise the preflight would check a different endpoint from the one used.
    if str(strategy.base_url).strip():
        try:
            declared = validate_base_url(str(strategy.base_url).strip())
        except Exception as exc:
            raise E1LiveBlocked(f"endpoint_not_approved: {exc}") from exc
        if declared != base_url:
            raise E1LiveBlocked(
                f"base_url_mismatch: --base-url {declared} does not match APART_BASE_URL "
                f"{base_url}; refusing to run against an endpoint that was not preflight-checked")

    if not arm.ledger_path.exists():
        raise E1LiveBlocked(
            f"ledger_missing: {arm.ledger_path} does not exist; refusing to initialise an E1 "
            "ledger implicitly. Create it with the documented initialisation step first.")
    store = LedgerStore(arm.ledger_path)
    try:
        store.load(expected_config_hash=config_hash, policy=policy)
    except Exception as exc:
        raise E1LiveBlocked(
            f"ledger_binding_mismatch: {arm.ledger_path} is not bound to {config_path} "
            f"(sha256 {config_hash[:12]}...): {exc}") from exc

    def open_run_fn(run_id: str, plan_digest: str, by: str) -> tuple[Any, list]:
        header = make_run_header(
            run_id, arm.allocation, arm.phase, config_hash,
            {"plan_sha256": plan_digest, "plan_version": PLAN_VERSION},
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            provenance={"model": f"{provider}/{model}", "base_url": base_url,
                        "spend_config_path": str(config_path),
                        "ledger_path": str(arm.ledger_path), "e1_arm": arm.alias},
        )
        open_run(store, policy, header, config_hash, by)
        swept = sweep_stale(store, config_hash, policy.stale_after_s)
        guard = LedgerGuard(store, policy, config_hash, run_id)
        # The client is built through the E1-owned factory, which re-authorises the
        # endpoint at the client-construction boundary (second enforcement point).
        client = build_e1_client(guard=guard, base_url=base_url, arm=arm)
        if client.guard is not guard or client.provider_order:
            raise E1LiveBlocked("client_not_bound_to_run_guard: refusing to dispatch")
        return client, swept

    return E1LiveContext(arm=arm, strategy=strategy, policy=policy, store=store,
                         config_hash=config_hash, provider=provider, model=model,
                         base_url=base_url, open_run_fn=open_run_fn)


# ---------------------------------------------------------------------- CLI


def _usd_to_micro(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    from decimal import Decimal, ROUND_CEILING
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E1 message-factorial experiment runner (offline by default).")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="new run directory; must not already exist")
    parser.add_argument("--dry-run", action="store_true",
                        help="offline fake receiver; no provider call, no ledger access")
    parser.add_argument("--confirm-live", action="store_true",
                        help="billable guarded run; not authorised (see docs/open_questions.md (retired register; item text in git history))")
    parser.add_argument("--analyze-run-dir", type=Path, default=None,
                        help="analyse exactly one finished E1 run directory")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-alias", default="fake",
                        help="used only for the run id and directory name")
    parser.add_argument("--supersedes", default=None,
                        help="run id this run replaces (a replacement never resumes in place)")
    parser.add_argument("--supersession-reason", default=None)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    parser.add_argument("--boot-seed", type=int, default=BOOT_SEED)
    # live-only inputs; none of these is approved for E1 yet
    parser.add_argument("--spend-config", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--allocation", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--base-url", default=None,
                        help="REQUIRED for --confirm-live; must be the approved E1 endpoint")
    parser.add_argument("--max-spend-usd", default=None,
                        help="optional tighter aggregate ceiling; may not exceed the approved $4.00")
    parser.add_argument("--by", default="MM", help="operator recorded in the ledger run header")
    parser.add_argument("--i-have-live-authorization", action="store_true",
                        help="assert that a separate approval covers this live run")
    parser.add_argument("--init-ledgers", action="store_true",
                        help="create ONLY the two approved E1 ledgers; refuses if either exists")
    return parser.parse_args(argv)


def _analyze(args: argparse.Namespace) -> int:
    run_dir = Path(args.analyze_run_dir)
    try:
        analysis = analyze_run_dir(run_dir, n_boot=args.n_boot, seed=args.boot_seed)
    except (E1InputError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    json_path = run_dir / ANALYSIS_JSON_FILENAME
    text_path = run_dir / ANALYSIS_TEXT_FILENAME
    try:
        write_new_file(json_path, dumps(analysis).encode("utf-8"))
        write_new_file(text_path, render_table(analysis).encode("utf-8"))
    except FileExistsError:
        print(f"error: refusing to overwrite {json_path} / {text_path}", file=sys.stderr)
        return 4
    print(render_table(analysis))
    return 0


def _live_strategy_from_args(args: argparse.Namespace) -> LiveStrategy:
    """Build the live strategy from the approved arm, refusing overrides.

    An omitted path/pair is taken from the approved arm; a supplied one is passed
    through so ``_require_approved_selection`` can reject any mismatch. This keeps
    the CLI unable to point E1 at an unapproved config or ledger.
    """
    arm = resolve_arm(args.model_alias)
    return LiveStrategy(
        model_alias=arm.alias,
        spend_config=Path(args.spend_config) if args.spend_config else arm.spend_config,
        ledger_path=Path(args.ledger) if args.ledger else arm.ledger_path,
        allocation=args.allocation or arm.allocation,
        phase=args.phase or arm.phase,
        base_url=args.base_url or "",
        approved=bool(args.i_have_live_authorization),
        max_spend_micro=_usd_to_micro(args.max_spend_usd),
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)
    write_new_file(path, payload.encode("utf-8"))


def _prepare_run_dir(args: argparse.Namespace, plan: Mapping[str, Any],
                     plan_digest: str) -> tuple[Path, str]:
    """Create the output directory exclusively and write the frozen plan."""
    out_dir = Path(args.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise E1LiveBlocked(
            f"output_directory_exists: {out_dir} already exists; refusing to overwrite") from None
    write_new_file(out_dir / PLAN_FILENAME, plan_bytes(plan))
    write_new_file(out_dir / PLAN_DIGEST_FILENAME, (plan_digest + "\n").encode("utf-8"))
    run_id = args.run_id or RUN_ID_FMT.format(
        model_alias=args.model_alias,
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return out_dir, run_id


def _finish_run(args: argparse.Namespace, out_dir: Path, run_id: str, plan: Mapping[str, Any],
                plan_digest: str, run_mode: str, outcome: RunOutcome,
                extra: Optional[Mapping[str, Any]] = None) -> int:
    """Convert the trial journal and write the terminal summary. Durable before return."""
    records = read_jsonl(out_dir / TRIALS_FILENAME)
    traces, converted = convert_many(records)
    report = conversion_report(converted)
    _write_jsonl(out_dir / "conversion.jsonl", [
        {"trial_id": item.provenance.trial_id if item.provenance else None,
         "scenario_id": item.provenance.scenario_id if item.provenance else None,
         "cell_id": item.provenance.cell_id if item.provenance else None,
         "status": item.status, "failures": list(item.failures),
         "trace_id": item.trace.trace_id if item.trace is not None else None}
        for item in converted])
    summary = {
        "experiment": EXPERIMENT,
        "plan_version": plan["plan_version"],
        "plan_sha256": plan_digest,
        "run_id": run_id,
        "run_mode": run_mode,
        "model_alias": args.model_alias,
        "supersedes_run_id": args.supersedes,
        "supersession_reason": args.supersession_reason,
        "n_valid_traces": len(traces),
        "not_monitor_evaluation": True,
        "no_pooling": True,
        **outcome.as_dict(),
        **report,
    }
    if extra:
        summary.update(dict(extra))
    write_new_file(out_dir / SUMMARY_FILENAME, dumps(summary).encode("utf-8"))
    print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True))
    return outcome.exit_code


def _dry_run(args: argparse.Namespace) -> int:
    """Offline execution. Takes no project lock: nothing here is spend-bearing."""
    plan = build_plan()
    validate_plan(plan)
    plan_digest = plan_sha256(plan)
    try:
        out_dir, run_id = _prepare_run_dir(args, plan, plan_digest)
    except E1LiveBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    responder = CellKeyedFakeResponder()
    client = FakeAgentClient(responder=responder, model="fake-e1-receiver")
    try:
        outcome = run_plan(plan, client, run_id, EXEC_DRY_RUN_FAKE, out_dir,
                           responder=responder,
                           supersedes_run_id=args.supersedes,
                           supersession_reason=args.supersession_reason,
                           model_alias=args.model_alias)
    except E1LiveBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    return _finish_run(args, out_dir, run_id, plan, plan_digest, EXEC_DRY_RUN_FAKE, outcome)


def _resolve_live_context(args: argparse.Namespace) -> E1LiveContext:
    """Read-only live resolution: arm, config, pair, ledger binding, endpoint.

    Mutates nothing — no output directory, no project lock, no ledger write, no
    client. **Endpoint authorisation happens here** (``endpoint_not_approved``),
    which is why ``main`` calls this *before* acquiring the project lock: an
    endpoint that is not authorised for this arm must not even transiently acquire
    the single-writer lock.
    """
    return live_strategy_resolvers(_live_strategy_from_args(args))


def _live_run(args: argparse.Namespace, context: E1LiveContext) -> int:
    """Guarded live execution. Caller holds the project-level E1 lock.

    Ordering is deliberate and is the safety property: preflight -> aggregate
    budget (read-only) -> create the output directory -> open the ledger run ->
    dispatch. Nothing is opened in the ledger and nothing is dispatched until
    every read-only check has passed. Resolution and endpoint authorisation have
    already happened (see :func:`_resolve_live_context`).
    """
    strategy = context.strategy

    # Preflight the strategy carrying the *environment-resolved* endpoint.
    preflight_live(context.preflight_strategy, resolve_price=context.resolve_price,
                   headroom_micro=context.headroom_micro,
                   worst_case_micro=context.worst_case_micro)

    worst_case = context.worst_case_micro()
    ceiling = E1_AGGREGATE_CEILING_MICRO
    if strategy.max_spend_micro is not None:
        if strategy.max_spend_micro > E1_AGGREGATE_CEILING_MICRO:
            raise E1LiveBlocked(
                f"max_spend_micro_exceeds_approved: --max-spend-usd is "
                f"{strategy.max_spend_micro} µ$, above the approved E1 aggregate ceiling "
                f"{E1_AGGREGATE_CEILING_MICRO} µ$; refusing to widen the approved ceiling")
        ceiling = strategy.max_spend_micro
    aggregate = aggregate_budget_preflight(worst_case_micro=worst_case, ceiling_micro=ceiling)

    plan = build_plan()
    validate_plan(plan)
    plan_digest = plan_sha256(plan)
    out_dir, run_id = _prepare_run_dir(args, plan, plan_digest)
    client, swept = context.open_run(run_id, plan_digest, args.by)
    if swept:
        print(f"note: recovered {len(swept)} stale reservation(s) as unknown "
              "(worst-case commitment retained)", file=sys.stderr)

    outcome = run_plan(plan, client, run_id, EXEC_LIVE, out_dir,
                       supersedes_run_id=args.supersedes,
                       supersession_reason=args.supersession_reason,
                       model_alias=args.model_alias,
                       price_policy=context.policy)
    return _finish_run(args, out_dir, run_id, plan, plan_digest, EXEC_LIVE, outcome, extra={
        "e1_arm": context.arm.alias,
        "ledger_path": str(context.arm.ledger_path),
        "spend_config_path": str(context.arm.spend_config),
        "aggregate_budget": aggregate,
    })


def _init_e1_ledgers() -> int:
    """Create **only** the two approved E1 ledgers, via ``LedgerStore.init``.

    Refuses if either destination already exists — no overwrite, no rebind, no
    automatic repair. Never constructs a client, never reads a key, never contacts
    a provider.
    """
    from monitors.ledger import LedgerStore, config_sha256

    planned = []
    for alias in sorted(APPROVED_E1_ARMS):
        arm = APPROVED_E1_ARMS[alias]
        if arm.ledger_path.exists():
            print(f"error: {arm.ledger_path} already exists; refusing to create or modify it",
                  file=sys.stderr)
            return 6
        if not arm.spend_config.exists():
            print(f"error: {arm.spend_config} does not exist", file=sys.stderr)
            return 6
        planned.append((alias, arm))

    for alias, arm in planned:
        arm.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        config_bytes = arm.spend_config.read_bytes()
        LedgerStore(arm.ledger_path).init(config_bytes)
        print(f"initialised E1 ledger for arm {alias}: {arm.ledger_path} "
              f"(bound to {arm.spend_config}, sha256 {config_sha256(config_bytes)[:12]}...)")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    modes = [args.analyze_run_dir is not None, bool(args.dry_run), bool(args.confirm_live),
             bool(args.init_ledgers)]
    if sum(modes) != 1:
        print("error: pass exactly one of --analyze-run-dir, --dry-run, --confirm-live, "
              "--init-ledgers", file=sys.stderr)
        return 2
    if args.init_ledgers:
        return _init_e1_ledgers()
    if args.analyze_run_dir is not None:
        return _analyze(args)
    if args.out_dir is None:
        print("error: --out-dir is required for a run", file=sys.stderr)
        return 2
    if args.dry_run:
        return _dry_run(args)

    # Refuse a pre-existing output directory before taking the lock, so the exit
    # code matches the dry-run path. ``_prepare_run_dir`` still performs the
    # exclusive ``mkdir`` inside the lock as the race-safe authority.
    if Path(args.out_dir).exists():
        print(f"error: output directory {args.out_dir} already exists; refusing to overwrite",
              file=sys.stderr)
        return 2

    # ``--base-url`` is mandatory for a live run. The endpoint pinned to the arm is
    # the authorisation control, but requiring the operator to state the endpoint
    # explicitly means the CLI/environment agreement check can never be skipped.
    if not str(args.base_url or "").strip():
        print("error: --base-url is required for --confirm-live; it must be the approved E1 "
              f"endpoint ({E1_APPROVED_ENDPOINT})", file=sys.stderr)
        return 2

    # Read-only resolution and endpoint authorisation, BEFORE the project lock: an
    # endpoint that is not authorised for this arm must not even transiently
    # acquire the single-writer lock.
    try:
        context = _resolve_live_context(args)
    except E1LiveBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except (E1InputError, OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5

    # Live: hold the project-level single-writer lock across the entire
    # spend-bearing critical section (preflight -> terminal status).
    lock = E1ProjectLock(E1_GLOBAL_LOCK_PATH, payload={
        "experiment": EXPERIMENT, "model_alias": args.model_alias,
        "out_dir": str(args.out_dir), "pid": os.getpid(), "started_utc": utc_now()})
    try:
        with lock:
            return _live_run(args, context)
    except E1LiveBlocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except (E1InputError, OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())

