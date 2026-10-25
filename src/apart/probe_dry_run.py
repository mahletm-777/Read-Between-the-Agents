"""Offline dry run for the OBJ-1.0 generation probe.

Runs the *real* orchestration, trace assembly, provenance and raw-sidecar path with a
fake client and an in-memory ledger stand-in:

* zero network/API calls, no credentials are read, no provider is contacted;
* no real ledger file is opened, created or written; reservations are mirrored in
  memory only (``monitors.ledger`` helpers, no ``LedgerStore``);
* the extraction, labeler and monitor paths are never imported or invoked;
* the output directory must be fresh, is refused if it exists, and is refused outright
  under a protected directory or an existing ``runs/<run>/`` tree.

    python -m apart.probe_dry_run --self-test --out-dir /tmp/apart-dry-run-1
    python -m apart.probe_dry_run --manifest runs/<new>/manifest.json \
        --policy runs/<new>/policy.json --out-dir /tmp/apart-dry-run-2

``--self-test`` uses a built-in **fixture** manifest and policy that are not experiment
artifacts: no variant brief, seed block, cap or gate in this module is an approved
design decision. The ledger stand-in does not reproduce the real store's transaction
semantics (file locking, sealing, crash recovery); those are covered by the ledger
tests. What it does exercise is stage ordering, trace assembly, invalid-trace
preservation, provenance/raw serialisation and the fail-closed stop checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from agents.client import FakeAgentClient  # noqa: E402
from agents.prompts import (AGENT_SYSTEM_PROMPT_SHA256, MAX_OUTPUT_TOKENS,  # noqa: E402
                            PROMPT_VERSION)
from agents.runner import AGENT_TEMPERATURE, REQUEST_HASH_FORMAT, STAGE_CONTEXT  # noqa: E402
from apart.behavior_probe import (PROVENANCE_SCHEMA_VERSION,  # noqa: E402
                                  probe_plan, write_probe_outputs)
from apart.brief_registry import BASE_BRIEF_VERSION  # noqa: E402
from apart.generation_probe import (assert_writable_target,  # noqa: E402
                                    run_generation_probe)
from apart.probe_protocol import (GENERATION_PROBE_MANIFEST_VERSION,  # noqa: E402
                                  GENERATION_PROBE_PROTOCOL_VERSION,
                                  GENERATION_PROBE_VARIANTS)
from apart.provenance import ProvenanceLog  # noqa: E402
from apart.raw_replies import RawReplyLog  # noqa: E402
from monitors.client import _input_token_bound  # noqa: E402
from monitors.ledger import (SETTLED, LedgerError, Policy, add_run,  # noqa: E402
                             check_admission, config_sha256, empty_ledger,
                             make_event, make_run_header, transition, utc_now)

#: Fixture seed block. Disjoint from every historical/corpus block the protocol module
#: knows about, and a fixture only - not an approved seed block.
FIXTURE_SEED_START = 7000
FIXTURE_CAP_USD = 10.0
FIXTURE_PER_CELL = 5
FIXTURE_MODEL = "anthropic/claude-sonnet-4.6"


class DryRunBook:
    """In-memory stand-in for ``LedgerStore``: same ``load`` surface, no disk."""

    def __init__(self, *, policy: Policy, config_hash: str, manifest: Mapping) -> None:
        self.policy = policy
        self.config_hash = config_hash
        self.run_id = manifest["run_id"]
        self.model = manifest["model"]
        self._ledger = empty_ledger(config_hash)
        add_run(self._ledger, make_run_header(
            manifest["run_id"], manifest["allocation"], manifest["phase"], config_hash,
            dict(manifest["prompt_files_sha256"]), manifest["created_utc"],
            provenance={"model": f"{manifest['provider']}/{manifest['model']}",
                        "base_url": manifest["base_url"],
                        "manifest": "dry-run-in-memory"},
        ))
        self._n = 0

    def load(self, *, expected_config_hash: Optional[str] = None,
             policy: Optional[Policy] = None) -> dict:
        if expected_config_hash is not None and expected_config_hash != self.config_hash:
            raise LedgerError("dry-run ledger is bound to a different config hash")
        return self._ledger

    @property
    def events(self) -> list[dict]:
        return list(self._ledger["events"])

    def reserve_and_settle(self, *, messages: Sequence[dict], max_tokens: int,
                           call_id: str) -> dict:
        """Mirror reserve-before-dispatch, then settle at the reservation.

        The amount comes from the real policy and the real input-token bound. Settling
        at the reservation is the pessimistic offline choice: with no provider usage
        there is no smaller true charge to settle at.
        """
        micro = self.policy.worst_case_micro(_input_token_bound(messages), max_tokens)
        check_admission(self._ledger, self.policy, self.run_id, micro)
        self._n += 1
        event = make_event(f"dryrun-{self._n:04d}", self.run_id, call_id, 0, self.model,
                           micro, utc_now())
        event["dispatched_ts"] = utc_now()
        settled = transition(event, SETTLED, utc_now(), micro)
        self._ledger["events"].append(settled)
        return settled


class DryRunClient:
    """Deterministic offline client; reserves in memory, then delegates to the fake.

    ``call_ids`` is shared with the responder so a stage-aware reply can be produced
    without the fake client needing to know the stage itself.
    """

    def __init__(self, inner: Any, book: DryRunBook,
                 call_ids: Optional[list] = None) -> None:
        self._inner = inner
        self._book = book
        self.call_ids = call_ids if call_ids is not None else []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def call_model(self, messages: Sequence[dict], **kwargs: Any) -> Any:
        call_id = kwargs.get("call_id")
        if isinstance(call_id, str):
            self.call_ids.append(call_id)
        self._book.reserve_and_settle(
            messages=messages,
            max_tokens=kwargs.get("max_tokens", MAX_OUTPUT_TOKENS),
            call_id=call_id)
        return self._inner.call_model(messages, **kwargs)


# ------------------------------------------------------------------ fixtures

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_policy_config(cap_usd: float = FIXTURE_CAP_USD) -> dict:
    """Self-test spend policy: the real policy *shape*, fixture values only.

    This is not an approved spend policy and is not derived from ``configs/spend.json``.
    """
    return {
        "_comment": ["FIXTURE ONLY - dry-run self-test; not an approved spend policy."],
        "provider": "openrouter",
        "model": FIXTURE_MODEL,
        "prices": {f"openrouter/{FIXTURE_MODEL}": {"input_per_million": 3.0,
                                                   "output_per_million": 15.0}},
        "allocations": {"sprint": {"cap_usd": cap_usd,
                                   "phase_caps_usd": {"extension": cap_usd}}},
        "backstop_usd": cap_usd,
        "stale_after_s": 600,
    }


def fixture_manifest(*, run_id: str = "dry-run-self-test",
                     per_cell: int = FIXTURE_PER_CELL,
                     seed_start: int = FIXTURE_SEED_START,
                     cap_usd: float = FIXTURE_CAP_USD,
                     max_output_bytes: int = 512,
                     max_invalid_traces: int = 0,
                     repo_root: Optional[Path] = None) -> dict:
    """Self-test manifest: seven variants, FREE_MSG only, generation-only scope locks.

    Every value here is a fixture. Revision of the real variant briefs, seed block,
    cap and gate belongs to the experiment-design workflow.
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    cells = [[variant, "FREE_MSG"] for variant in GENERATION_PROBE_VARIANTS]
    return {
        "probe_manifest_version": GENERATION_PROBE_MANIFEST_VERSION,
        "protocol_version": GENERATION_PROBE_PROTOCOL_VERSION,
        "objective_id": "OBJ-1.0",
        "run_id": run_id,
        "design_status": "dry run self-test (fixture, not an experiment artifact)",
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": AGENT_SYSTEM_PROMPT_SHA256,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "request_hash_format": REQUEST_HASH_FORMAT,
        "provider": "openrouter",
        "model": FIXTURE_MODEL,
        "base_url": "https://openrouter.ai/api/v1",
        "allocation": "sprint",
        "phase": "extension",
        "cap_usd": cap_usd,
        "cells": cells,
        "per_cell": per_cell,
        "schedule": "round_robin",
        "seed_start": seed_start,
        "max_traces": per_cell * len(cells),
        "max_calls": per_cell * len(cells) * len(STAGE_CONTEXT),
        "max_attempts": 1,
        "stage_parse_attempts": 1,
        "agent_temperature": AGENT_TEMPERATURE,
        "agent_max_tokens": MAX_OUTPUT_TOKENS,
        "max_output_bytes": max_output_bytes,
        "stage_context": {key: list(value) for key, value in STAGE_CONTEXT.items()},
        "extraction": False,
        "labeler": False,
        "monitor_arms": [],
        "confirmatory": False,
        "outcome": "joint action (sender/receiver); descriptive only",
        "feasibility_gate": {
            "kind": "execution_validity",
            "min_attempted_traces": per_cell * len(cells),
            "max_invalid_traces": max_invalid_traces,
            "note": "execution validity only; no expected action or message",
        },
        "stop_conditions": {
            "retries": "none",
            "fallback_model": "none",
            "on_ledger_error": "stop",
            "on_cap_refusal": "stop",
            "on_unknown_cost": "stop",
            "on_settlement_overrun": "stop",
            "on_served_model_mismatch": "stop",
        },
        "created_utc": utc_now(),
        "brief_versions": {
            "version": BASE_BRIEF_VERSION,
            "status": "fixture",
            "note": ("Fixture manifest declares the historical brief version explicitly. "
                     "The proposed probe manifest declares the revised version."),
        },
        "prompt_files_sha256": {
            "agents/prompts.py": _sha256_file(root / "agents" / "prompts.py"),
        },
    }


def fixture_responder(call_ids: list, *, invalid_stages: Sequence[tuple] = (),
                      declined_seeds: Sequence[int] = ()) -> Any:
    """Stage-aware deterministic responder: valid JSON for every stage.

    ``invalid_stages`` is a set of ``(seed, stage)`` pairs, with ``stage`` using the
    call-id token (``s1``/``r1``/``s2``/``s3``/``r2``), that answer with non-JSON so
    invalid-stage handling and invalid-trace preservation can be exercised. The reply
    schema follows the stage's parser: ``plan`` for S1/R1, ``message`` for S2, and
    ``action`` for S3/R2. ``call_ids`` carries the request in flight (appended by
    :class:`DryRunClient`).
    """
    invalid = {(int(seed), str(stage)) for seed, stage in invalid_stages}
    declined = {int(seed) for seed in declined_seeds}

    def respond(messages: Sequence[dict], _temperature: float, seed: Optional[int],
                _index: int) -> str:
        call_id = call_ids[-1] if call_ids else ""
        stage = call_id.split(":")[1] if call_id.count(":") == 2 else ""
        if (seed, stage) in invalid:
            return "I would rather not answer in JSON."
        if stage == "s2":
            return json.dumps({"message": "" if seed in declined else f"m{seed}"})
        if stage in ("s1", "r1"):
            return json.dumps({"plan": "A", "note": ""})
        return json.dumps({"action": "A"})

    return respond



# ------------------------------------------------------------------ dry run

def run_dry_run(*, out_dir: Any, manifest: Optional[Mapping] = None,
                policy_config: Optional[Mapping] = None,
                repo_root: Optional[Path] = None,
                invalid_stages: Sequence[tuple] = (),
                declined_seeds: Sequence[int] = ()) -> dict:
    """Run the real orchestration offline into a fresh directory.

    ``out_dir`` must not exist and must not be protected. Layout written::

        manifest.json          the manifest actually used (fixture or supplied)
        config.json            the policy config actually used
        provenance/calls.jsonl per-call provenance (fsynced, append-only)
        raw/replies.jsonl      raw reply sidecar (fsynced, append-only)
        out/probe_traces.jsonl rows, one JSON object per attempted trace
        out/probe_summary.json trace-level summary incl. the execution-validity gate
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    target = assert_writable_target(out_dir, repo_root=root)
    if target.exists():
        raise FileExistsError(f"refusing to write into an existing directory: {target}")
    resolved_manifest = dict(manifest) if manifest is not None else fixture_manifest(
        repo_root=root)
    resolved_config = dict(policy_config) if policy_config is not None else \
        fixture_policy_config()
    policy = Policy.from_config(resolved_config)
    config_hash = config_sha256(
        json.dumps(resolved_config, sort_keys=True, ensure_ascii=False).encode("utf-8"))

    target.mkdir(parents=True)
    provenance = ProvenanceLog(target / "provenance" / "calls.jsonl",
                              run_id=resolved_manifest["run_id"])
    raw_log = RawReplyLog(target / "raw" / "replies.jsonl",
                          run_id=resolved_manifest["run_id"])
    book = DryRunBook(policy=policy, config_hash=config_hash, manifest=resolved_manifest)
    call_ids: list = []
    inner = FakeAgentClient(
        fixture_responder(call_ids, invalid_stages=invalid_stages,
                          declined_seeds=declined_seeds),
        model=resolved_manifest["model"])
    client = DryRunClient(inner, book, call_ids)

    result = run_generation_probe(
        manifest=resolved_manifest, policy=policy, store=book, config_hash=config_hash,
        agent_client=client, provenance=provenance, raw_log=raw_log, repo_root=root)
    summary = write_probe_outputs(target / "out", result)
    (target / "manifest.json").write_text(
        json.dumps(resolved_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "config.json").write_text(
        json.dumps(resolved_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "summary": summary,
        "out_dir": str(target),
        "calls": len(book.events),
        "call_ids": list(call_ids),
        "rows": result["rows"],
        "provenance_records": provenance.records(),
        "raw_records": raw_log.records(),
    }


def load_run_config(run_dir: Any) -> tuple[dict, dict]:
    """Read a run directory's ``manifest.json``/``policy.json`` (read-only, no writes)."""
    run_dir = Path(run_dir)
    return (json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")),
            json.loads((run_dir / "policy.json").read_text(encoding="utf-8")))



# ----------------------------------------------------------------------- cli

def main(argv: Optional[list[str]] = None) -> int:
    """Offline dry-run entry point. Never constructs a real client."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="fresh directory for dry-run artifacts "
                             "(default: a new system temp directory)")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true",
                        help="use the built-in fixture manifest/policy")
    parser.add_argument("--inject-invalid", type=int, default=0,
                        help="answer S1 with non-JSON for the first N traces")
    parser.add_argument("--inject-declined", type=int, default=0,
                        help="decline the S2 message for the first N traces")
    args = parser.parse_args(argv)

    if args.self_test and (args.manifest or args.policy):
        print("error: --self-test cannot be combined with --manifest/--policy",
              file=sys.stderr)
        return 2
    if not args.self_test and not (args.manifest and args.policy):
        print("error: pass --self-test, or both --manifest and --policy", file=sys.stderr)
        return 2

    manifest: Optional[dict] = None
    policy_config: Optional[dict] = None
    try:
        if not args.self_test:
            manifest, policy_config = load_run_config(args.manifest.parent)
        else:
            manifest = fixture_manifest()
        seeds = [cell.seed for cell in probe_plan(manifest)]
    except (OSError, ValueError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out_dir if args.out_dir is not None else Path(
        tempfile.mkdtemp(prefix="apart-probe-dry-run-"))
    invalid_stages = [(seed, "s1") for seed in seeds[:max(0, args.inject_invalid)]]
    declined_seeds = seeds[:max(0, args.inject_declined)]
    try:
        outcome = run_dry_run(out_dir=out_dir, manifest=manifest,
                             policy_config=policy_config,
                             invalid_stages=invalid_stages,
                             declined_seeds=declined_seeds)
    except (OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    summary = outcome["summary"]
    print(f"dry run wrote {outcome['out_dir']} "
          f"(no network call, no credential, no ledger file)")
    print(f"calls={outcome['calls']} traces={summary['attempted_traces']}"
          f"/{summary['planned_traces']} invalid={summary['gate']['checks']['invalid_traces']['observed']}")
    print(f"gate kind={summary['gate']['kind']} passed={summary['gate']['passed']} "
          f"stopped={summary['stopped']!r}")
    return 0 if summary["gate"]["passed"] else 5


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())


__all__ = [
    "FIXTURE_SEED_START", "FIXTURE_CAP_USD", "FIXTURE_PER_CELL", "FIXTURE_MODEL",
    "DryRunBook", "DryRunClient", "fixture_policy_config", "fixture_manifest",
    "fixture_responder", "run_dry_run", "load_run_config", "main",
]

