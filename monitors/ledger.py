"""Spend-ledger primitives and store (T3 design v4, steps S1 + S1b).

All amounts are integer microdollars (1 USD = 1_000_000 µ$). USD values are
converted only at the boundary: worst-case reservations and settlements round
up; caps and imported amounts must be exact to the microdollar.

Contribution of one event:
  settled             -> settled_micro
  reserved / unknown  -> reserved_micro, or the latest correction for that
                         event (which may be higher or lower). Never released
                         silently.
Admission: a new worst-case amount x is allowed only if x fits the smallest
headroom among the (allocation, phase) cap, the allocation cap and the local
backstop. Caps are never added together.

LedgerStore persists one ledger file. Every change happens inside one
transaction: thread lock + flock, load and validate, apply, seal, validate,
atomic write (fsync file, os.replace, fsync directory). A reservation is
returned only after it is durably on disk, so a failed write never leaves an
unrecorded request. A missing ledger is an error; creation is explicit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

try:  # POSIX only
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

LEDGER_VERSION = 2
MICRO_PER_USD = 1_000_000

VALID_PAIRS: dict[str, tuple[str, ...]] = {
    "pilot_audit": ("smoke", "gate", "pilot"),
    "sprint": ("smoke", "gate", "extension"),
}
ALLOCATIONS = tuple(VALID_PAIRS)

RESERVED, SETTLED, UNKNOWN = "reserved", "settled", "unknown"
# Stage 4N: a settlement above its reservation is a distinct terminal state, not an
# ordinary success. The real reported charge is kept in full in ``settled_micro``
# (never rewritten to fit the reservation), it is counted in every commitment, and
# its presence stops all further spend through this ledger.
OVERRUN = "overrun"
STATES = (RESERVED, SETTLED, UNKNOWN, OVERRUN)
LEGAL_TRANSITIONS = {(RESERVED, SETTLED), (RESERVED, UNKNOWN), (RESERVED, OVERRUN)}

AUDIT_TYPES = ("correction", "rebind", "replay_approval", "late_response", "restore",
               "backup", "budget_incident")


class LedgerError(ValueError):
    """The ledger, policy or a requested change is invalid; nothing may be sent."""


class AdmissionRefused(LedgerError):
    """A reservation would exceed a control. ``control`` names the binding one."""

    def __init__(self, control: str, headroom_micro: int, amount_micro: int) -> None:
        super().__init__(
            f"refused by {control}: headroom {headroom_micro} µ$ < requested {amount_micro} µ$"
        )
        self.control = control
        self.headroom_micro = headroom_micro
        self.amount_micro = amount_micro


class BudgetIncident(LedgerError):
    """A settlement exceeded its reservation; no further spend is authorized.

    The provider charge is real and is kept in full on the event and in the
    matching ``budget_incident`` audit record. This exception does not undo it; it
    only reports that the ledger can no longer authorize requests, because the
    reserved bound it was relying on has been shown to be too small.
    """

    def __init__(self, incident: Mapping) -> None:
        """``incident`` may be the over-run event or its ``budget_incident`` record."""
        reserved = int(incident["reserved_micro"])
        settled = int(incident["settled_micro"])
        overrun = settled - reserved
        super().__init__(
            f"settlement {settled} µ$ exceeded reservation {reserved} µ$ "
            f"for event {incident['event_id']!r} (overrun {overrun} µ$); "
            "no further requests are authorized"
        )
        self.incident = dict(incident)
        self.event_id = incident["event_id"]
        self.reserved_micro = reserved
        self.settled_micro = settled
        # Derived from the two authoritative amounts, never trusted from a field.
        self.overrun_micro = overrun


# ---------------------------------------------------------------- amounts

def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise LedgerError(f"{name} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise LedgerError(f"{name} must be finite")
    try:
        # str() of a float is its shortest repr, so 0.1 stays 0.1, not 0.1000000000000000055…
        amount = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError(f"{name} is malformed") from exc
    if not amount.is_finite():
        raise LedgerError(f"{name} must be finite")
    if amount < 0:
        raise LedgerError(f"{name} must be >= 0")
    return amount


def usd_to_micro(value: Any, name: str = "amount", *, exact: bool = False) -> int:
    """USD -> µ$. ``exact`` rejects sub-microdollar precision; otherwise round up."""
    micro = _decimal(value, name) * MICRO_PER_USD
    if exact:
        if micro != micro.to_integral_value():
            raise LedgerError(f"{name} has more precision than one microdollar")
        return int(micro)
    return int(micro.to_integral_value(rounding=ROUND_CEILING))


def micro_to_usd(micro: int) -> float:
    return micro / MICRO_PER_USD


def _micro(value: Any, name: str, *, positive: bool = False) -> int:
    """A stored amount: a non-negative (or positive) int, never bool or float."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(f"{name} must be an integer number of microdollars")
    if value < 0 or (positive and value == 0):
        raise LedgerError(f"{name} must be {'> 0' if positive else '>= 0'}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{name} must be a non-empty string")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- policy

@dataclass(frozen=True)
class Policy:
    """Internal budget policy parsed from the approved spend config (USD in, µ$ held)."""

    allocation_caps: Mapping[str, int]
    pair_caps: Mapping[tuple[str, str], int]
    backstop_micro: int
    stale_after_s: float
    provider: Optional[str] = None
    model: Optional[str] = None
    prices: Mapping[str, tuple[Decimal, Decimal]] = None  # "provider/model" -> USD per 1M (in, out)

    @classmethod
    def from_config(cls, data: Any) -> "Policy":
        if not isinstance(data, Mapping):
            raise LedgerError("spend config must be an object")
        allocations = data.get("allocations")
        if not isinstance(allocations, Mapping) or not allocations:
            raise LedgerError("config 'allocations' must be a non-empty object")
        allocation_caps: dict[str, int] = {}
        pair_caps: dict[tuple[str, str], int] = {}
        for allocation, spec in allocations.items():
            if allocation not in VALID_PAIRS:
                raise LedgerError(f"unknown allocation {allocation!r}")
            if not isinstance(spec, Mapping):
                raise LedgerError(f"allocation {allocation!r} must be an object")
            allocation_caps[allocation] = _cap(spec.get("cap_usd"), f"{allocation}.cap_usd")
            phases = spec.get("phase_caps_usd")
            if not isinstance(phases, Mapping) or not phases:
                raise LedgerError(f"{allocation}.phase_caps_usd must be a non-empty object")
            for phase, cap in phases.items():
                if phase not in VALID_PAIRS[allocation]:
                    raise LedgerError(f"invalid pair ({allocation!r}, {phase!r})")
                pair_caps[(allocation, phase)] = _cap(cap, f"{allocation}.phase_caps_usd.{phase}")
        backstop = _cap(data.get("backstop_usd"), "backstop_usd")
        stale = data.get("stale_after_s")
        if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not math.isfinite(stale) or stale <= 0:
            raise LedgerError("stale_after_s must be a finite number > 0")
        provider = data.get("provider")
        model = data.get("model")
        prices: dict[str, tuple[Decimal, Decimal]] = {}
        raw_prices = data.get("prices", {})
        if not isinstance(raw_prices, Mapping):
            raise LedgerError("prices must be an object")
        for key, price in raw_prices.items():
            if not isinstance(key, str) or "/" not in key or not isinstance(price, Mapping):
                raise LedgerError(f"price entry {key!r} must be 'provider/model': {{...}}")
            pin = _decimal(price.get("input_per_million"), f"{key}.input_per_million")
            pout = _decimal(price.get("output_per_million"), f"{key}.output_per_million")
            if pin <= 0 or pout <= 0:
                raise LedgerError(f"price for {key!r} must be > 0")
            prices[key] = (pin, pout)
        if provider is not None or model is not None:
            _text(provider, "provider")
            _text(model, "model")
            if f"{provider}/{model}" not in prices:
                raise LedgerError(f"the configured model {provider}/{model} has no price")
        return cls(allocation_caps, pair_caps, backstop, float(stale), provider, model, prices)

    def require_model(self) -> tuple[str, str]:
        """Provider and model for real requests; missing means fail closed."""
        if not self.provider or not self.model:
            raise LedgerError("spend config names no provider/model; refusing to send")
        return self.provider, self.model

    def worst_case_micro(self, input_tokens: int, max_tokens: int) -> int:
        provider, model = self.require_model()
        for value, name in ((input_tokens, "input_tokens"), (max_tokens, "max_tokens")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LedgerError(f"{name} must be a non-negative integer")
        pin, pout = self.prices[f"{provider}/{model}"]
        micro = int((Decimal(input_tokens) * pin + Decimal(max_tokens) * pout).to_integral_value(rounding=ROUND_CEILING))
        if micro <= 0:
            raise LedgerError("worst-case reservation must be positive")
        return micro

    def cost_micro(self, usage: Any, served_model: Optional[str],
                   served_provider: Optional[str]) -> Optional[int]:
        """Settled cost from provider usage, or None if usage or price is unknown.

        The served model is priced under the reporting provider if listed, else
        under the configured gateway provider (OpenRouter reports the upstream).
        """
        if not isinstance(usage, Mapping):
            return None
        tin = usage.get("prompt_tokens", usage.get("input_tokens"))
        tout = usage.get("completion_tokens", usage.get("output_tokens"))
        for value in (tin, tout):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
        provider, model = self.require_model()
        served = served_model if isinstance(served_model, str) and served_model.strip() else model
        price = None
        if isinstance(served_provider, str) and served_provider.strip():
            price = self.prices.get(f"{served_provider}/{served}")
        if price is None:
            price = self.prices.get(f"{provider}/{served}")
        if price is None:
            return None
        pin, pout = price
        return int((Decimal(tin) * pin + Decimal(tout) * pout).to_integral_value(rounding=ROUND_CEILING))

    def pair_cap(self, allocation: str, phase: str) -> int:
        """Missing configuration fails closed; caps are never copied between pairs."""
        check_pair(allocation, phase)
        if allocation not in self.allocation_caps:
            raise LedgerError(f"allocation {allocation!r} is not configured")
        cap = self.pair_caps.get((allocation, phase))
        if cap is None:
            raise LedgerError(f"no cap configured for ({allocation!r}, {phase!r})")
        return cap


def _cap(value: Any, name: str) -> int:
    micro = usd_to_micro(value, name, exact=True)
    if micro <= 0:
        raise LedgerError(f"{name} must be > 0")
    return micro


def config_sha256(config_bytes: bytes) -> str:
    """The ledger binds to the exact approved config bytes."""
    return hashlib.sha256(config_bytes).hexdigest()


def check_pair(allocation: Any, phase: Any) -> None:
    if allocation not in VALID_PAIRS:
        raise LedgerError(f"allocation must be one of {ALLOCATIONS}; got {allocation!r}")
    if phase not in VALID_PAIRS[allocation]:
        raise LedgerError(f"invalid pair ({allocation!r}, {phase!r})")


# ---------------------------------------------------------------- ledger structure

def empty_ledger(config_hash: str) -> dict:
    config_hash = _text(config_hash, "config_sha256")
    return {
        "version": LEDGER_VERSION,
        "initial_config_sha256": config_hash,
        "config_sha256": config_hash,
        "runs": {},
        "events": [],
        "audit": [],
        "imports": [],
        "missing_evidence": [],
    }


def canonical_bytes(ledger: Mapping) -> bytes:
    body = {k: v for k, v in ledger.items() if k != "checksum"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def seal(ledger: Mapping) -> dict:
    """Return a copy with ``checksum`` set. Detects corruption, not deliberate edits."""
    sealed = copy.deepcopy(dict(ledger))
    sealed["checksum"] = hashlib.sha256(canonical_bytes(sealed)).hexdigest()
    return sealed


def make_run_header(run_id: str, allocation_id: str, phase: str, config_sha256: str,
                    input_hashes: Mapping[str, str], created_utc: str,
                    provenance: Optional[Mapping[str, str]] = None) -> dict:
    check_pair(allocation_id, phase)
    if not isinstance(input_hashes, Mapping):
        raise LedgerError("input_hashes must be an object")
    if provenance is not None:
        if not isinstance(provenance, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in provenance.items()
        ):
            raise LedgerError("provenance must map strings to strings")
    header = {
        "run_id": _text(run_id, "run_id"),
        "allocation_id": allocation_id,
        "phase": phase,
        "config_sha256": _text(config_sha256, "config_sha256"),
        "input_hashes": dict(input_hashes),
        "created_utc": _text(created_utc, "created_utc"),
    }
    if provenance is not None:
        header["provenance"] = dict(provenance)
    return header


def add_run(ledger: dict, header: Mapping) -> dict:
    """Add a run header. An existing run_id is immutable: any difference is refused."""
    header = make_run_header(**header)
    if header["config_sha256"] != ledger["config_sha256"]:
        raise LedgerError("run config hash does not match the ledger binding")
    existing = ledger["runs"].get(header["run_id"])
    if existing is not None:
        if existing != header:
            raise LedgerError(f"run {header['run_id']!r} header is immutable")
        return ledger
    if any(i["run_id"] == header["run_id"] for i in ledger["imports"]):
        raise LedgerError(f"run_id {header['run_id']!r} is already an imported run")
    ledger["runs"][header["run_id"]] = header
    return ledger


# ---------------------------------------------------------------- events

def make_event(event_id: str, run_id: str, call_id: str, attempt: int, model: str,
               reserved_micro: int, ts_reserved: str, tags: Optional[Mapping] = None,
               pid: Optional[int] = None) -> dict:
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise LedgerError("attempt must be a non-negative integer")
    return {
        "event_id": _text(event_id, "event_id"),
        "run_id": _text(run_id, "run_id"),
        "call_id": _text(call_id, "call_id"),
        "attempt": attempt,
        "model": _text(model, "model"),
        "reserved_micro": _micro(reserved_micro, "reserved_micro", positive=True),
        "state": RESERVED,
        "settled_micro": None,
        "ts_reserved": _text(ts_reserved, "ts_reserved"),
        "ts_resolved": None,
        "dispatched_ts": None,
        "unknown_reason": None,
        "pid": pid,
        "tags": dict(tags or {}),
    }


def transition(event: Mapping, new_state: str, ts: str,
               settled_micro: Optional[int] = None, reason: Optional[str] = None) -> dict:
    """Return the event moved to ``new_state``.

    Legal moves are ``reserved -> settled | unknown | overrun``. ``settled`` and
    ``overrun`` both carry the real reported charge in ``settled_micro``; they
    differ only in whether that charge is within the reservation. ``settle_event``
    decides which one applies and ``validate`` re-checks the invariant, so a charge
    above the reservation can never be stored as an ordinary settlement.
    """
    old = event.get("state")
    if (old, new_state) not in LEGAL_TRANSITIONS:
        raise LedgerError(f"illegal transition {old!r} -> {new_state!r} for {event.get('event_id')!r}")
    updated = dict(event)
    updated["state"] = new_state
    updated["ts_resolved"] = _text(ts, "ts")
    if new_state in (SETTLED, OVERRUN):
        updated["settled_micro"] = _micro(settled_micro, "settled_micro")
    else:
        if settled_micro is not None:
            raise LedgerError("only a settled event carries settled_micro")
        updated["unknown_reason"] = reason
    return updated


def effective_corrections(ledger: Mapping) -> dict[str, int]:
    """event_id -> corrected amount from the latest correction record."""
    out: dict[str, int] = {}
    for record in ledger.get("audit", []):
        if record.get("type") == "correction":
            out[record["event_id"]] = record["corrected_micro"]
    return out


def contribution(event: Mapping, corrections: Mapping[str, int]) -> int:
    if event["state"] in (SETTLED, OVERRUN):
        # An overrun contributes its FULL reported charge, which is by construction
        # larger than the reservation, so the breach can never be hidden from a
        # commitment or headroom calculation.
        return event["settled_micro"]
    if event["state"] == UNKNOWN and event["event_id"] in corrections:
        return corrections[event["event_id"]]
    return event["reserved_micro"]


def budget_incidents(ledger: Mapping) -> list[dict]:
    """Every over-run settlement in the ledger, in event order.

    A non-empty list means the reserved bound was exceeded somewhere and the
    ledger refuses to authorize any further request.
    """
    return [event for event in ledger["events"] if event.get("state") == OVERRUN]


def make_budget_incident(event: Mapping) -> dict:
    """The audit record for one over-run settlement, keeping the real amounts."""
    reserved = _micro(event.get("reserved_micro"), "reserved_micro", positive=True)
    settled = _micro(event.get("settled_micro"), "settled_micro")
    if settled <= reserved:
        raise LedgerError("a budget incident requires a settlement above its reservation")
    return {
        "audit_id": str(uuid.uuid4()),
        "type": "budget_incident",
        "event_id": _text(event.get("event_id"), "event_id"),
        "run_id": _text(event.get("run_id"), "run_id"),
        "call_id": _text(event.get("call_id"), "call_id"),
        "reserved_micro": reserved,
        "settled_micro": settled,
        "overrun_micro": settled - reserved,
        "ts": utc_now(),
        "by": "ledger",
    }


def make_correction(ledger: Mapping, event_id: str, corrected_micro: int, reason: str,
                    evidence: str, by: str, ts: Optional[str] = None) -> dict:
    """Build a correction record; ``prior_micro`` is the event's current contribution."""
    event = next((e for e in ledger["events"] if e["event_id"] == event_id), None)
    if event is None:
        raise LedgerError(f"no event {event_id!r}")
    return {
        "audit_id": str(uuid.uuid4()),
        "type": "correction",
        "event_id": event_id,
        "prior_micro": contribution(event, effective_corrections(ledger)),
        "corrected_micro": _micro(corrected_micro, "corrected_micro"),
        "reason": _text(reason, "reason"),
        "evidence": _text(evidence, "evidence"),
        "by": _text(by, "by"),
        "ts": ts or utc_now(),
    }


# ---------------------------------------------------------------- validation

def validate(ledger: Any, expected_config_hash: Optional[str] = None,
             policy: Optional[Policy] = None) -> None:
    """Fail closed on any structural, checksum or consistency problem."""
    if not isinstance(ledger, Mapping):
        raise LedgerError("ledger must be an object")
    if ledger.get("version") != LEDGER_VERSION:
        raise LedgerError(f"unsupported ledger version {ledger.get('version')!r}")
    for key, kind in (("runs", dict), ("events", list), ("audit", list),
                      ("imports", list), ("missing_evidence", list)):
        if not isinstance(ledger.get(key), kind):
            raise LedgerError(f"ledger field {key!r} must be a {kind.__name__}")
    checksum = ledger.get("checksum")
    if not isinstance(checksum, str) or checksum != hashlib.sha256(canonical_bytes(ledger)).hexdigest():
        raise LedgerError("ledger checksum is missing or does not match")
    bound = _text(ledger.get("config_sha256"), "config_sha256")
    initial = _text(ledger.get("initial_config_sha256"), "initial_config_sha256")
    if expected_config_hash is not None and bound != expected_config_hash:
        raise LedgerError("spend config does not match the ledger binding")

    # Rebind chain: initial -> each rebind's new hash, in order, ending at the binding.
    chain = [initial]
    for record in ledger["audit"]:
        if isinstance(record, Mapping) and record.get("type") == "rebind":
            old = _text(record.get("old_config_sha256"), "rebind.old_config_sha256")
            new = _text(record.get("new_config_sha256"), "rebind.new_config_sha256")
            if old != chain[-1]:
                raise LedgerError("rebind chain is broken: old hash does not follow the previous binding")
            if new == old:
                raise LedgerError("rebind must change the config hash")
            chain.append(new)
    if chain[-1] != bound:
        raise LedgerError("current binding does not match the end of the rebind chain")
    known_bindings = set(chain)

    for run_id, header in ledger["runs"].items():
        if not isinstance(header, Mapping) or header.get("run_id") != run_id:
            raise LedgerError(f"run header {run_id!r} is malformed")
        check_pair(header.get("allocation_id"), header.get("phase"))
        if header.get("config_sha256") not in known_bindings:
            raise LedgerError(f"run {run_id!r} was bound to a config the ledger never used")

    imported = set()
    for record in ledger["imports"]:
        run_id = _text(record.get("run_id"), "imports.run_id")
        if run_id in imported or run_id in ledger["runs"]:
            raise LedgerError(f"run_id {run_id!r} is counted twice")
        imported.add(run_id)
        check_pair(record.get("allocation_id"), record.get("phase"))
        _micro(record.get("amount_micro"), "imports.amount_micro")

    event_ids, call_attempts, by_id = set(), set(), {}
    overrun_ids: set[str] = set()
    for event in ledger["events"]:
        event_id = _text(event.get("event_id"), "event_id")
        if event_id in event_ids:
            raise LedgerError(f"duplicate event_id {event_id!r}")
        event_ids.add(event_id)
        key = (event.get("call_id"), event.get("attempt"))
        if key in call_attempts:
            raise LedgerError(f"duplicate (call_id, attempt) {key!r}")
        call_attempts.add(key)
        if event.get("run_id") not in ledger["runs"]:
            raise LedgerError(f"event {event_id!r} references unknown run")
        state = event.get("state")
        if state not in STATES:
            raise LedgerError(f"event {event_id!r} has invalid state {state!r}")
        reserved_micro = _micro(event.get("reserved_micro"), "reserved_micro", positive=True)
        if state in (SETTLED, OVERRUN):
            settled_micro = _micro(event.get("settled_micro"), "settled_micro")
            # ``settled`` and ``overrun`` are exactly the two sides of the
            # reservation. A charge above it can never be stored as an ordinary
            # settlement, so it can never be missed by a later reader.
            if state == OVERRUN and settled_micro <= reserved_micro:
                raise LedgerError(
                    f"event {event_id!r} is an overrun but does not settle above its reservation"
                )
            if state == SETTLED and settled_micro > reserved_micro:
                raise LedgerError(
                    f"event {event_id!r} settles above its reservation and must be recorded "
                    "as an overrun"
                )
        elif event.get("settled_micro") is not None:
            raise LedgerError(f"event {event_id!r} is {state} but carries settled_micro")
        if state == OVERRUN:
            overrun_ids.add(event_id)
        for optional in ("dispatched_ts", "unknown_reason"):
            if event.get(optional) is not None and not isinstance(event.get(optional), str):
                raise LedgerError(f"event {event_id!r} field {optional} must be a string")
        if state != UNKNOWN and event.get("unknown_reason") is not None:
            raise LedgerError(f"event {event_id!r} has an unknown_reason but is {state}")
        by_id[event_id] = event

    audit_ids = set()
    incident_ids: set[str] = set()
    current: dict[str, int] = {}
    for record in ledger["audit"]:
        if not isinstance(record, Mapping):
            raise LedgerError("audit records must be objects")
        audit_id = _text(record.get("audit_id"), "audit_id")
        if audit_id in audit_ids:
            raise LedgerError(f"duplicate audit_id {audit_id!r}")
        audit_ids.add(audit_id)
        kind = record.get("type")
        if kind not in AUDIT_TYPES:
            raise LedgerError(f"unknown audit type {kind!r}")
        _text(record.get("by"), "audit.by")  # an annotation only, not authentication
        if kind == "correction":
            target = by_id.get(record.get("event_id"))
            if target is None:
                raise LedgerError("a correction must reference an existing event")
            if target["state"] != UNKNOWN:
                raise LedgerError("only an unknown event can be corrected")
            prior = _micro(record.get("prior_micro"), "correction.prior_micro")
            expected_prior = current.get(target["event_id"], contribution(target, {}))
            if prior != expected_prior:
                raise LedgerError("correction prior_micro does not match the recorded amount")
            current[target["event_id"]] = _micro(record.get("corrected_micro"), "correction.corrected_micro")
            _text(record.get("reason"), "correction.reason")
            _text(record.get("evidence"), "correction.evidence")
        elif kind == "replay_approval":
            original = by_id.get(record.get("original_event_id"))
            if original is None or original["state"] != UNKNOWN:
                raise LedgerError("a replay approval must reference an unknown event")
            key = record.get("record_key")
            if not isinstance(key, Mapping) or not {"seed", "view", "k"} <= set(key):
                raise LedgerError("a replay approval needs record_key {seed, view, k}")
            _text(record.get("reason"), "replay_approval.reason")
            new_id = record.get("new_event_id")
            if new_id is not None and (new_id not in by_id or new_id == original["event_id"]):
                raise LedgerError("replay new_event_id must be a different existing event")
        elif kind == "late_response":
            target = by_id.get(record.get("event_id"))
            if target is None or target["state"] != UNKNOWN:
                raise LedgerError("a late response must reference an unknown event")
        elif kind == "rebind":
            _text(record.get("reason"), "rebind.reason")
        elif kind == "budget_incident":
            target = by_id.get(record.get("event_id"))
            if target is None or target["state"] != OVERRUN:
                raise LedgerError("a budget incident must reference an overrun event")
            reserved = _micro(record.get("reserved_micro"), "budget_incident.reserved_micro",
                              positive=True)
            settled = _micro(record.get("settled_micro"), "budget_incident.settled_micro")
            _micro(record.get("overrun_micro"), "budget_incident.overrun_micro", positive=True)
            if (reserved, settled) != (target["reserved_micro"], target["settled_micro"]):
                raise LedgerError(
                    "a budget incident must record the event's real reserved and settled amounts"
                )
            incident_ids.add(record["event_id"])

    missing_incidents = sorted(overrun_ids - incident_ids)
    if missing_incidents:
        # An overrun with no incident record would let a reader miss the breach.
        raise LedgerError(f"overrun event(s) without a budget incident: {missing_incidents}")

    if policy is not None:
        for allocation, phase in pairs_in_use(ledger):
            policy.pair_cap(allocation, phase)


def pairs_in_use(ledger: Mapping) -> set[tuple[str, str]]:
    pairs = {(h["allocation_id"], h["phase"]) for h in ledger["runs"].values()}
    pairs |= {(i["allocation_id"], i["phase"]) for i in ledger["imports"]}
    return pairs


# ---------------------------------------------------------------- commitments and admission

def commitments(ledger: Mapping) -> dict:
    """Committed µ$ per (allocation, phase), per allocation, and in total."""
    corrections = effective_corrections(ledger)
    pair: dict[tuple[str, str], int] = {}
    for record in ledger["imports"]:
        key = (record["allocation_id"], record["phase"])
        pair[key] = pair.get(key, 0) + record["amount_micro"]
    for event in ledger["events"]:
        header = ledger["runs"][event["run_id"]]
        key = (header["allocation_id"], header["phase"])
        pair[key] = pair.get(key, 0) + contribution(event, corrections)
    allocation: dict[str, int] = {}
    for (alloc, _phase), amount in pair.items():
        allocation[alloc] = allocation.get(alloc, 0) + amount
    return {"pair": pair, "allocation": allocation, "total": sum(allocation.values())}


def headroom(ledger: Mapping, policy: Policy, allocation: str, phase: str) -> dict:
    """Remaining µ$ per control and the binding minimum (may be negative)."""
    pair_cap = policy.pair_cap(allocation, phase)
    totals = commitments(ledger)
    controls = {
        "phase cap": pair_cap - totals["pair"].get((allocation, phase), 0),
        "allocation cap": policy.allocation_caps[allocation] - totals["allocation"].get(allocation, 0),
        "local backstop": policy.backstop_micro - totals["total"],
    }
    binding = min(controls, key=controls.get)
    return {"controls": controls, "binding": binding, "headroom": controls[binding]}


def check_admission(ledger: Mapping, policy: Policy, run_id: str, amount_micro: int) -> dict:
    """Raise unless ``amount_micro`` fits every applicable control.

    ``BudgetIncident`` is raised first if any settlement in this ledger exceeded
    its reservation. Once that has happened the reserved bound is known to be too
    small, so no further request may be authorized -- in this run or any other run
    recorded in the same ledger. The check lives here, inside the reservation
    transaction, so no caller can bypass it.
    """
    amount_micro = _micro(amount_micro, "amount_micro", positive=True)
    incidents = budget_incidents(ledger)
    if incidents:
        raise BudgetIncident(incidents[-1])
    header = ledger["runs"].get(run_id)
    if header is None:
        raise LedgerError(f"unknown run {run_id!r}; open the run first")
    if header["config_sha256"] != ledger["config_sha256"]:
        raise LedgerError("run config hash does not match the ledger binding")
    result = headroom(ledger, policy, header["allocation_id"], header["phase"])
    if amount_micro > result["headroom"]:
        raise AdmissionRefused(result["binding"], result["headroom"], amount_micro)
    return result


# ---------------------------------------------------------------- persistence

def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new_file(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively and make its contents durable."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _serialise(ledger: Mapping) -> bytes:
    return (json.dumps(ledger, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class LedgerStore:
    """One ledger file, changed only through locked, validated, durable transactions."""

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.backup_dir = self.path.parent / "backups"
        key = str(self.path.resolve())
        with LedgerStore._locks_guard:
            self._thread_lock = LedgerStore._locks.setdefault(key, threading.RLock())

    # ---- locking

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self.path.parent.is_dir():
            raise LedgerError(f"ledger directory {self.path.parent} does not exist")
        with self._thread_lock:
            with open(self.lock_path, "a+", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # ---- reading

    def _read(self, path: Optional[Path] = None) -> dict:
        path = path or self.path
        if not path.exists():
            raise LedgerError(f"ledger {path} does not exist; initialise it explicitly")
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise LedgerError(f"ledger {path} is unreadable or not valid JSON") from exc
        return data

    def load(self, expected_config_hash: Optional[str] = None,
             policy: Optional[Policy] = None) -> dict:
        with self._locked():
            data = self._read()
            validate(data, expected_config_hash, policy)
            return data

    # ---- writing

    def _replace(self, ledger: Mapping) -> None:
        """Atomic, durable replacement. On any failure the old file is untouched."""
        tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        try:
            _write_new_file(tmp, _serialise(ledger))
            os.replace(tmp, self.path)
            _fsync_dir(self.path.parent)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise LedgerError(f"ledger write failed: {exc}") from exc

    def init(self, config_bytes: bytes) -> dict:
        """Explicit creation. Refuses if a ledger already exists."""
        with self._locked():
            if self.path.exists():
                raise LedgerError(f"ledger {self.path} already exists")
            ledger = seal(empty_ledger(config_sha256(config_bytes)))
            validate(ledger)
            self._replace(ledger)
            return ledger

    def transact(self, operation: Callable[[dict], Any],
                 expected_config_hash: Optional[str] = None,
                 policy: Optional[Policy] = None) -> Any:
        """Lock, load + validate, apply to a copy, seal + validate, write durably."""
        with self._locked():
            current = self._read()
            validate(current, expected_config_hash, policy)
            work = copy.deepcopy(current)
            work.pop("checksum", None)
            result = operation(work)
            sealed = seal(work)
            validate(sealed, expected_config_hash, policy)
            self._replace(sealed)
            return result

    # ---- backups

    def backup(self, reason: str, by: str) -> Path:
        """Write a verified, uniquely named copy of the current valid ledger."""
        _text(reason, "reason")
        _text(by, "by")
        with self._locked():
            return self._backup_locked()

    def _backup_locked(self) -> Path:
        current = self._read()
        validate(current)
        payload = _serialise(current)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            self.backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            for seq in range(10_000):
                target = self.backup_dir / f"ledger.{stamp}.{seq:04d}.{digest[:8]}.json"
                try:
                    _write_new_file(target, payload)
                    break
                except FileExistsError:
                    continue
            else:
                raise LedgerError("no free backup name")
            _write_new_file(target.with_name(target.name + ".sha256"), (digest + "\n").encode())
            _fsync_dir(self.backup_dir)
        except OSError as exc:
            raise LedgerError(f"backup failed: {exc}") from exc
        verify_backup(target)
        return target

    def restore(self, backup_path: Path, reason: str, by: str) -> dict:
        """Replace the ledger with a verified backup, recording a restore audit entry.

        A valid current ledger is backed up first; an invalid one is kept as
        ``corrupt.<ts>.json`` evidence. Spending may resume only after the
        restored ledger validates.
        """
        _text(reason, "reason")
        _text(by, "by")
        restored = verify_backup(Path(backup_path))
        with self._locked():
            if self.path.exists():
                try:
                    validate(self._read())
                    self._backup_locked()
                except LedgerError:
                    try:
                        self.backup_dir.mkdir(exist_ok=True)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        _write_new_file(self.backup_dir / f"corrupt.{stamp}.json", self.path.read_bytes())
                        _fsync_dir(self.backup_dir)
                    except OSError as exc:
                        raise LedgerError(f"could not preserve the corrupt ledger: {exc}") from exc
            work = copy.deepcopy(restored)
            work.pop("checksum", None)
            work["audit"].append({
                "audit_id": str(uuid.uuid4()), "type": "restore", "by": by, "reason": reason,
                "backup": Path(backup_path).name, "ts": utc_now(),
            })
            sealed = seal(work)
            validate(sealed)
            self._replace(sealed)
            return sealed


def verify_backup(path: Path) -> dict:
    """Check a backup against its sidecar hash and validate its contents."""
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    try:
        payload = path.read_bytes()
        expected = sidecar.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LedgerError(f"backup {path} or its .sha256 is missing") from exc
    if hashlib.sha256(payload).hexdigest() != expected:
        raise LedgerError(f"backup {path} does not match its .sha256")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LedgerError(f"backup {path} is not valid JSON") from exc
    validate(data)
    return data


# ---------------------------------------------------------------- operations

def open_run(store: LedgerStore, policy: Policy, header: Mapping, config_hash: str, by: str) -> Path:
    """Back up, then add the run header. A failed backup aborts before anything changes."""
    backup = store.backup("run open", by)

    def operation(ledger: dict) -> None:
        add_run(ledger, header)
        policy.pair_cap(header["allocation_id"], header["phase"])

    store.transact(operation, expected_config_hash=config_hash, policy=policy)
    return backup


def reserve(store: LedgerStore, policy: Policy, config_hash: str, run_id: str, call_id: str,
            attempt: int, model: str, amount_micro: int, tags: Optional[Mapping] = None) -> dict:
    """Admit and durably record one worst-case reservation under a single lock.

    Returns the event only after it is on disk. Any exception means the request
    must not be sent.
    """
    def operation(ledger: dict) -> dict:
        if _find(ledger, call_id, attempt) is not None:
            raise DuplicateReservation(f"({call_id!r}, {attempt}) is already reserved; not reserving again")
        check_admission(ledger, policy, run_id, amount_micro)
        event = make_event(str(uuid.uuid4()), run_id, call_id, attempt, model,
                           amount_micro, utc_now(), tags, pid=os.getpid())
        ledger["events"].append(event)
        return event

    return store.transact(operation, expected_config_hash=config_hash, policy=policy)


class DuplicateReservation(LedgerError):
    """The (call_id, attempt) already has a reservation; a second one is never created."""


def _find(ledger: Mapping, call_id: str, attempt: int) -> Optional[dict]:
    return next((e for e in ledger["events"]
                 if e["call_id"] == call_id and e["attempt"] == attempt), None)


def _event(ledger: Mapping, event_id: str) -> tuple[int, dict]:
    for index, event in enumerate(ledger["events"]):
        if event["event_id"] == event_id:
            return index, event
    raise LedgerError(f"no event {event_id!r}")


def find_reservation(store: LedgerStore, call_id: str, attempt: int) -> Optional[dict]:
    """Read, under the lock and after validation, the event for one call attempt."""
    return _find(store.load(), call_id, attempt)


def mark_dispatched(store: LedgerStore, config_hash: str, event_id: str) -> None:
    """Record, durably, that the request is about to be sent. Done after reserve, before send.

    A reserved event without this marker was provably never sent by this code.
    """
    def operation(ledger: dict) -> None:
        index, event = _event(ledger, event_id)
        if event["state"] != RESERVED or event.get("dispatched_ts") is not None:
            raise LedgerError(f"event {event_id!r} cannot be marked dispatched")
        ledger["events"][index] = dict(event, dispatched_ts=utc_now())

    store.transact(operation, expected_config_hash=config_hash)


def settle_event(store: LedgerStore, config_hash: str, event_id: str,
                 settled_micro: int) -> dict:
    """Reserved -> ``settled``, or -> ``overrun`` when the charge exceeds the reservation.

    An amount above ``reserved_micro`` is never stored as an ordinary settlement:
    it becomes ``overrun`` and a matching ``budget_incident`` audit record is
    written in the same transaction. The real charge is kept in full either way --
    nothing is rewritten to fit the reservation -- and because ``transact`` writes
    only after the update validates, a failed update leaves the reservation exactly
    as it was.
    """
    def operation(ledger: dict) -> dict:
        index, event = _event(ledger, event_id)
        amount = _micro(settled_micro, "settled_micro")
        state = OVERRUN if amount > event["reserved_micro"] else SETTLED
        updated = transition(event, state, utc_now(), settled_micro=amount)
        ledger["events"][index] = updated
        if state == OVERRUN:
            ledger["audit"].append(make_budget_incident(updated))
        return updated

    return store.transact(operation, expected_config_hash=config_hash)


def mark_unknown(store: LedgerStore, config_hash: str, event_id: str, reason: str) -> dict:
    """reserved -> unknown. The worst-case reservation stays in the commitment."""
    reason = _text(reason, "reason")

    def operation(ledger: dict) -> dict:
        index, event = _event(ledger, event_id)
        updated = transition(event, UNKNOWN, utc_now(), reason=reason)
        ledger["events"][index] = updated
        return updated

    return store.transact(operation, expected_config_hash=config_hash)


NOT_SENT_PERSISTENCE_UNCERTAIN = "not_sent_persistence_uncertain"
REQUEST_OUTCOME_UNCERTAIN = "request_outcome_uncertain"


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sweep_stale(store: LedgerStore, config_hash: str, stale_after_s: float,
                now: Optional[datetime] = None,
                pid_alive: Callable[[Any], bool] = _pid_alive) -> list[dict]:
    """Move stale reserved events to unknown, keeping their worst-case commitment.

    Stale: older than ``stale_after_s``, or owned by a process that is gone.
    Reason: ``not_sent_persistence_uncertain`` only when no dispatch marker was
    written (this code never sends before that marker is durable); otherwise
    ``request_outcome_uncertain``. Nothing is ever released.
    """
    now = now or datetime.now(timezone.utc)

    def operation(ledger: dict) -> list[dict]:
        swept = []
        for index, event in enumerate(ledger["events"]):
            if event["state"] != RESERVED:
                continue
            age = (now - datetime.fromisoformat(event["ts_reserved"].replace("Z", "+00:00"))).total_seconds()
            if age <= stale_after_s and pid_alive(event.get("pid")):
                continue
            reason = (REQUEST_OUTCOME_UNCERTAIN if event.get("dispatched_ts")
                      else NOT_SENT_PERSISTENCE_UNCERTAIN)
            updated = transition(event, UNKNOWN, utc_now(), reason=reason)
            ledger["events"][index] = updated
            swept.append(updated)
        return swept

    return store.transact(operation, expected_config_hash=config_hash)


def annotate_late_response(store: LedgerStore, config_hash: str, event_id: str, by: str,
                           usage: Optional[Mapping] = None, served_model: Optional[str] = None,
                           response_id: Optional[str] = None, evidence: Optional[str] = None) -> dict:
    """Append evidence about an unknown event; its state and commitment do not change."""
    def operation(ledger: dict) -> dict:
        _index, event = _event(ledger, event_id)
        if event["state"] != UNKNOWN:
            raise LedgerError("a late response can only annotate an unknown event")
        record = {
            "audit_id": str(uuid.uuid4()), "type": "late_response", "event_id": event_id,
            "by": _text(by, "by"), "ts": utc_now(), "usage": dict(usage or {}),
            "served_model": served_model, "response_id": response_id, "evidence": evidence,
        }
        ledger["audit"].append(record)
        return record

    return store.transact(operation, expected_config_hash=config_hash)


# ---------------------------------------------------------------- C_set from ledger events (P1/P2)

# Views the monitor runner can emit. Anything else is an unknown view, which is
# reported rather than silently dropped.
KNOWN_VIEWS = ("M1", "M2", "M3", "M4", "M3-ens", "M3-T07")
C_SET_VIEWS = ("M3", "M4", "M3-ens")
NON_C_SET_VIEWS = tuple(v for v in KNOWN_VIEWS if v not in C_SET_VIEWS)


def parse_call_id(call_id: str) -> Optional[dict]:
    """``<run_id>:<seed>:<view>:<k>:p<n>`` -> parts, or None if not a runner call id."""
    if not isinstance(call_id, str):
        return None
    parts = call_id.rsplit(":", 4)
    if len(parts) != 5 or not parts[4].startswith("p"):
        return None
    run_id, seed, view, k, parse = parts
    try:
        seed_i, k_i, parse_i = int(seed), int(k), int(parse[1:])
    except ValueError:
        return None
    if not run_id or not view or seed_i < 0 or k_i < 0 or parse_i < 0:
        return None
    return {"run_id": run_id, "seed": seed_i, "view": view, "k": k_i, "parse": parse_i}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_score(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _score_state(score_records, run_id: str, allow_unlabelled_scores: bool) -> dict:
    """Validate, de-duplicate and classify score records for the denominator."""
    seen: dict = {}
    valid_single: dict[int, set] = defaultdict(set)
    valid_ens: dict[int, set] = defaultdict(set)
    cset_seeds: set[int] = set()
    duplicate, invalid, unknown_view, excluded_view, cross_run, missing_k = [], [], [], [], [], []
    for index, record in enumerate(score_records):
        if not isinstance(record, Mapping):
            raise LedgerError(f"score record {index} is not an object")
        rid = record.get("run_id")
        if rid is not None and rid != run_id:
            cross_run.append(index)
            continue
        if rid is None and not allow_unlabelled_scores:
            raise LedgerError(f"score record {index} has no run_id")
        seed, view = record.get("seed"), record.get("view")
        if not _is_int(seed) or seed < 0:
            raise LedgerError(f"score record {index} has an invalid seed")
        if not isinstance(view, str) or not view:
            raise LedgerError(f"score record {index} has an invalid view")
        if view not in KNOWN_VIEWS:
            unknown_view.append(index)
            continue
        if view in NON_C_SET_VIEWS:
            excluded_view.append(index)
            continue
        cset_seeds.add(seed)
        k = record.get("k")
        if view in ("M3", "M4"):
            k = 0 if k is None else k
            if not _is_int(k) or k != 0:
                invalid.append(index)
                continue
        else:
            if k is None:
                missing_k.append(index)
            elif not _is_int(k) or k < 0 or k > 4:
                invalid.append(index)
                continue
        identity = (seed, view, k)
        status, score = record.get("status"), record.get("score")
        if identity in seen:
            if seen[identity] == (status, score):
                duplicate.append(index)
                continue
            raise LedgerError(f"conflicting duplicate score record for {identity}")
        seen[identity] = (status, score)
        if status == "ok" and _is_score(score):
            if view in ("M3", "M4"):
                valid_single[seed].add(view)
            else:
                valid_ens[seed].add(k)
        elif status == "ok":
            invalid.append(index)
    complete, incomplete = [], []
    for seed in sorted(cset_seeds):
        # A sample without an explicit k cannot be proven distinct from another
        # sample, so it never counts toward the three required ensemble samples.
        distinct_ens = {k for k in valid_ens[seed] if k is not None}
        if ("M3" in valid_single[seed] and "M4" in valid_single[seed]
                and len(distinct_ens) >= 3):
            complete.append(seed)
        else:
            incomplete.append(seed)
    return {"complete": complete, "incomplete": incomplete, "cset_seeds": cset_seeds,
            "duplicate": duplicate, "invalid": invalid, "unknown_view": unknown_view,
            "excluded_view": excluded_view, "cross_run": cross_run, "missing_k": missing_k}


def _event_state(events, run_id: str, corrections: Mapping, cset_seeds: set) -> dict:
    """Verify event identity, run association, view and usage attribution."""
    seen: set[str] = set()
    attributed = unattributed = unknown_view = excluded_view = 0
    unattributed_ids, unknown_view_ids, excluded_view_ids, mismatch_ids, orphan_ids = [], [], [], [], []
    for event in events:
        if not isinstance(event, Mapping):
            raise LedgerError("every ledger event must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise LedgerError("every ledger event needs a non-empty event_id")
        if event_id in seen:
            raise LedgerError(f"duplicate event_id {event_id!r}")
        seen.add(event_id)
        event_run = event.get("run_id")
        if not isinstance(event_run, str) or not event_run:
            raise LedgerError(f"event {event_id!r} has no run_id")
        if event_run != run_id:
            continue
        state = event.get("state")
        if state not in STATES:
            raise LedgerError(f"event {event_id!r} has invalid state {state!r}")
        amount = contribution(event, corrections)
        if not _is_int(amount) or amount < 0:
            raise LedgerError(f"event {event_id!r} has no usable commitment")
        parts = parse_call_id(event.get("call_id"))
        if parts is None:
            unattributed += amount
            unattributed_ids.append(event_id)
            continue
        if parts["run_id"] != run_id:
            # The call id claims another run; its view cannot be trusted.
            unattributed += amount
            unattributed_ids.append(event_id)
            mismatch_ids.append(event_id)
            continue
        if parts["view"] in C_SET_VIEWS:
            attributed += amount
            if parts["seed"] not in cset_seeds:
                orphan_ids.append(event_id)
        elif parts["view"] in NON_C_SET_VIEWS:
            excluded_view += amount
            excluded_view_ids.append(event_id)
        else:
            unknown_view += amount
            unknown_view_ids.append(event_id)
    return {"attributed": attributed, "unattributed": unattributed,
            "unknown_view": unknown_view, "excluded_view": excluded_view,
            "unattributed_ids": unattributed_ids, "unknown_view_ids": unknown_view_ids,
            "excluded_view_ids": excluded_view_ids, "mismatch_ids": mismatch_ids,
            "orphan_ids": orphan_ids}



def c_set_micro(ledger: Mapping, run_id: str, score_records: list[Mapping], *,
                score_run_id: Optional[str] = None,
                allow_unlabelled_scores: bool = False) -> dict:
    """§16.9.5 C_set from the authoritative ledger, never from an estimate.

    ``c_set_micro`` is the point estimate (M3/M4/M3-ens plus unattributable
    commitments, over complete traces). ``c_set_upper_micro`` and
    ``budget_c_set_micro`` add unknown-view spend; the generation guard must use
    ``budget_c_set_micro``. Duplicate or conflicting records raise; every
    exclusion is reported by event id or score-record index.
    """
    if not isinstance(ledger, Mapping):
        raise LedgerError("ledger must be a mapping")
    if not isinstance(run_id, str) or not run_id.strip():
        raise LedgerError("run_id must be a non-empty string")
    if score_run_id is not None and score_run_id != run_id:
        raise LedgerError("score_run_id does not match run_id")
    events, audit = ledger.get("events"), ledger.get("audit", [])
    if not isinstance(events, list):
        raise LedgerError("ledger has no events list")
    if not isinstance(audit, list):
        raise LedgerError("ledger audit must be a list")
    try:
        score_records = list(score_records)
    except TypeError as exc:
        raise LedgerError("score_records must be iterable") from exc
    try:
        corrections = effective_corrections(ledger)
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError(f"malformed correction record: {exc}") from exc
    scores = _score_state(score_records, run_id, allow_unlabelled_scores)
    events_state = _event_state(events, run_id, corrections, scores["cset_seeds"])

    total_committed = (events_state["attributed"] + events_state["unattributed"]
                       + events_state["unknown_view"])
    point_numerator = events_state["attributed"] + events_state["unattributed"]
    n = len(scores["complete"])
    point = None if n == 0 else -(-point_numerator // n)      # ceil
    upper = None if n == 0 else -(-total_committed // n)      # ceil
    return {
        "run_id": run_id,
        "total_micro": events_state["attributed"],
        "unattributed_micro": events_state["unattributed"],
        "unknown_view_micro": events_state["unknown_view"],
        "excluded_view_micro": events_state["excluded_view"],
        "total_committed_micro": total_committed,
        "complete_traces": n,
        "c_set_micro": point,
        "c_set_upper_micro": upper,
        "budget_c_set_micro": upper,
        "empty_denominator": n == 0,
        "incomplete_trace_seeds": scores["incomplete"],
        "orphan_event_ids": events_state["orphan_ids"],
        "unattributed_event_ids": events_state["unattributed_ids"],
        "unknown_view_event_ids": events_state["unknown_view_ids"],
        "excluded_view_event_ids": events_state["excluded_view_ids"],
        "call_id_run_mismatch_event_ids": events_state["mismatch_ids"],
        "cross_run_score_records": scores["cross_run"],
        "duplicate_score_records": scores["duplicate"],
        "invalid_score_records": scores["invalid"],
        "unknown_score_view_records": scores["unknown_view"],
        "excluded_score_view_records": scores["excluded_view"],
        "missing_k_records": scores["missing_k"],
    }


def c_set_usd(ledger: Mapping, run_id: str, score_records: list[Mapping], **kwargs) -> Optional[float]:
    """Authoritative C_set in USD for the generation guard.

    Uses ``budget_c_set_micro`` (the conservative committed total), never a
    manually supplied replacement estimate.
    """
    out = c_set_micro(ledger, run_id, score_records, **kwargs)
    micro = out["budget_c_set_micro"]
    return None if micro is None else micro / MICRO_PER_USD
