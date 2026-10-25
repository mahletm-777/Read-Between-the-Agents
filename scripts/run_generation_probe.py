"""OBJ-1.0 generation probe: the offline check, and the guarded live command.

    python scripts/run_generation_probe.py check --run-dir experiments/generation_probe_obj10_v1
    python scripts/run_generation_probe.py live  --run-dir experiments/generation_probe_obj10_v1 \\
        --out-dir runs/generation-probe-obj10-v1-live-20261023 --init-ledger --confirm-live

``check`` is offline. It validates the generation-probe manifest with its own validator
(never through ``apart.behavior_probe``, which owns only the Stage 4N protocol), confirms
every pinned file hash, prints the round-robin plan, and prints the worst-case reservation
computed with the **v3.0 briefs installed** - the text the run would actually send. It
constructs no client, opens no ledger and reads no credential.

``live`` is the only path that can send a request. It requires ``--confirm-live`` and an
owner authorization recorded outside this script, refuses any output directory that
already exists, and refuses any run whose ledger, provenance or raw-reply artefacts are
already present. Nothing here starts it automatically.

Why this exists
---------------
``apart.generation_probe.run_generation_probe`` is the execution path, but until now its
only caller was the offline dry-run harness. This module supplies the missing live wiring:
``LedgerStore`` -> ``open_run`` -> ``LedgerGuard`` -> ``ModelClient`` -> provenance and
raw-reply logs -> ``run_generation_probe`` -> ``write_probe_outputs``.

Every spend control is the existing one. There is no parallel budget, ledger, provenance
or client implementation here, and no path around ``LedgerGuard``: the client is built
with the guard attached, and ``run_generation_probe`` refuses a client whose
``max_attempts`` is not 1.

Status: infrastructure only. Live execution is authorized by the owner but must be
re-verified against the preflight in ``experiments/generation_probe_obj10_v1/decisions.md``
before it is started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from apart.behavior_probe import (ProbeError, check_binding,  # noqa: E402
                                  plan_worst_case, write_probe_outputs)
from apart.brief_registry import BriefVersionError, applied, verify_frozen_originals  # noqa: E402
from apart.generation_probe import (ProbePathError,  # noqa: E402
                                    assert_writable_target, brief_plan_summary,
                                    manifest_brief_version, run_generation_probe)
from apart.probe_protocol import (ProtocolError,  # noqa: E402
                                  validate_generation_probe_manifest,
                                  validate_prompt_files, validate_seed_block)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DOES_NOT_FIT = 3
EXIT_STOPPED_BY_GUARD = 4
EXIT_RUN_INCOMPLETE = 5
EXIT_OUTPUT_FAILED = 6


def _load(run_dir: Path):
    """Validate everything checkable without a client or a credential.

    Fails closed on: an unusable manifest, a changed pinned file, an overlapping seed
    block, a manifest/policy mismatch, a drifted frozen brief, and an unavailable brief
    version. Nothing here opens a ledger or reads a key.
    """
    from monitors.ledger import Policy, config_sha256

    policy_bytes = (run_dir / "policy.json").read_bytes()
    policy = Policy.from_config(json.loads(policy_bytes.decode("utf-8")))
    manifest = validate_generation_probe_manifest(
        json.loads((run_dir / "manifest.json").read_bytes().decode("utf-8")),
        repo_root=ROOT)
    validate_prompt_files(manifest["prompt_files_sha256"], repo_root=ROOT)
    validate_seed_block(manifest["seed_start"], manifest["max_traces"], repo_root=ROOT)
    check_binding(manifest, policy)
    verify_frozen_originals()
    brief_version = manifest_brief_version(manifest)
    # Resolve every cell's briefs up front, so an unavailable version stops the run
    # before the first request rather than part way through.
    brief_plan_summary(manifest, brief_version)
    return manifest, policy, policy_bytes, config_sha256(policy_bytes), brief_version


def _worst_case(manifest, policy, brief_version: str) -> dict:
    """Worst case computed with the brief text the run will actually send."""
    with applied(brief_version):
        return plan_worst_case(manifest, policy)


def _report(summary: dict, ledger: Optional[dict] = None,
            run_id: Optional[str] = None) -> None:
    """Print the outcome, keeping reservations and settled spend distinct."""
    print(json.dumps(summary["descriptive_counts_by_cell"], sort_keys=True))
    print(f"planned={summary['planned_traces']} attempted={summary['attempted_traces']} "
          f"not_attempted={summary['not_attempted']}")
    gate = summary["gate"]
    print(f"invalid={gate['checks']['invalid_traces']['observed']} "
          f"allowed={gate['checks']['invalid_traces']['allowed']}")
    print(f"gate passed={gate['passed']} stopped={summary['stopped']!r}")
    print(f"brief_version={summary.get('brief_version')} "
          f"delivery_all_verified={summary.get('brief_delivery_all_verified')}")
    if ledger is None or run_id is None:
        return
    events = [e for e in ledger.get("events", []) if e.get("run_id") == run_id]
    reserved = sum(int(e.get("reserved_micro") or 0) for e in events)
    settled = sum(int(e.get("settled_micro") or 0)
                  for e in events if e.get("settled_micro") is not None)
    states: dict[str, int] = {}
    for event in events:
        states[event["state"]] = states.get(event["state"], 0) + 1
    print(f"calls={len(events)} reserved={reserved} micro$ settled={settled} micro$")
    print(f"call states={json.dumps(states, sort_keys=True)}")
    print(f"settlement overruns={states.get('overrun', 0)}")


def check(run_dir: Path) -> int:
    """Offline validation, plan and worst case. No client, no credential, no ledger."""
    run_dir = Path(run_dir).resolve()
    try:
        manifest, policy, _bytes, config_hash, brief_version = _load(run_dir)
    except (OSError, ValueError, ProbeError, ProtocolError, BriefVersionError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    worst = _worst_case(manifest, policy, brief_version)
    print(f"run {manifest['run_id']} protocol={manifest['protocol_version']} "
          f"manifest={manifest['probe_manifest_version']} "
          f"prompt={manifest['prompt_version']} briefs={brief_version} "
          f"policy_sha256={config_hash}")
    print(f"model {manifest['provider']}/{manifest['model']} "
          f"base_url={manifest['base_url']} fallback={manifest['fallback_model']!r}")
    print(f"cap {worst['cap_micro']} micro$  max_traces={manifest['max_traces']} "
          f"max_calls={manifest['max_calls']}  gate="
          f"invalid<={manifest['feasibility_gate']['max_invalid_traces']} "
          f"attempted>={manifest['feasibility_gate']['min_attempted_traces']}")
    for entry in brief_plan_summary(manifest, brief_version):
        print(f"  {entry['variant']}/{entry['condition']}: "
              f"{entry['sender_brief_display_id']} -> {entry['receiver_brief_display_id']} "
              f"sha256 {entry['sender_brief_sha256'][:12]}/{entry['receiver_brief_sha256'][:12]}")
    print(f"worst case for {worst['traces']} traces / {worst['calls']} calls: "
          f"{worst['total_micro']} micro$ against cap {worst['cap_micro']} micro$ -> "
          f"{'FITS' if worst['fits'] else 'DOES NOT FIT'}")
def live(run_dir: Path, out_dir: Path, init_ledger: bool, *,
         client_factory: Optional[Callable[[Any], Any]] = None) -> int:
    """Execute the probe for real. Sends billable requests once started.

    ``client_factory`` is a test seam: it receives the bound ``LedgerGuard`` and returns
    the agent client. Production never passes it, so the real ``ModelClient`` is used.
    A test client must still route through the guard - the checks below refuse one that
    would not.
    """
    from apart.provenance import ProvenanceLog
    from apart.raw_replies import RawReplyLog
    from monitors.client import LedgerGuard, ModelClient, load_apart_env, validate_base_url
    from monitors.ledger import (LedgerError, LedgerStore, make_run_header, open_run)

    run_dir, out_dir = Path(run_dir).resolve(), Path(out_dir).resolve()

    # 1. Destination safety, before anything is created or spent.
    try:
        assert_writable_target(out_dir, repo_root=ROOT)
    except ProbePathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if out_dir.exists():
        print(f"error: output directory already exists: {out_dir}", file=sys.stderr)
        return EXIT_CONFIG

    # 2. Configuration.
    try:
        manifest, policy, policy_bytes, config_hash, brief_version = _load(run_dir)
    except (OSError, ValueError, ProbeError, ProtocolError, BriefVersionError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    provenance_path = run_dir / "provenance" / "calls.jsonl"
    raw_path = run_dir / "raw" / "replies.jsonl"
    ledger_path = run_dir / "spend" / "ledger.json"
    for path, label in ((provenance_path, "provenance log"), (raw_path, "raw reply log")):
        if path.exists():
            print(f"error: {label} already exists: {path}", file=sys.stderr)
            return EXIT_CONFIG

    # 3. Credential PRESENCE only, so a missing key cannot leave a half-opened run.
    #    The value is never read, printed, logged or compared.
    if client_factory is None:
        load_apart_env()
        if not os.environ.get("APART_API_KEY"):
            print("error: APART_API_KEY is not set; refusing to open a run", file=sys.stderr)
            return EXIT_CONFIG

    try:
        store = LedgerStore(ledger_path)
        if not ledger_path.exists():
            if not init_ledger:
                print(f"error: ledger {ledger_path} does not exist; pass --init-ledger once",
                      file=sys.stderr)
                return EXIT_CONFIG
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.init(policy_bytes)
        ledger = store.load(expected_config_hash=config_hash, policy=policy)
        if manifest["run_id"] in ledger["runs"]:
            print(f"error: run {manifest['run_id']!r} is already open in the ledger",
                  file=sys.stderr)
            return EXIT_CONFIG
        provider, model = policy.require_model()
        base_url = validate_base_url(manifest["base_url"])
        header = make_run_header(
            manifest["run_id"], manifest["allocation"], manifest["phase"], config_hash,
            dict(manifest["prompt_files_sha256"]), manifest["created_utc"],
            provenance={"model": f"{provider}/{model}", "base_url": base_url,
                        "manifest": "manifest.json"},
        )
        open_run(store, policy, header, config_hash, "MM")
        guard = LedgerGuard(store, policy, config_hash, manifest["run_id"])
        if client_factory is None:
            client = ModelClient(guard=guard, base_url=base_url, load_env=True,
                                 max_attempts=1)
        else:
            client = client_factory(guard)
        # No implicit retry, and no silent fallback: both are refused, not assumed.
        if int(getattr(client, "max_attempts", 1)) != 1:
            raise ProbeError("agent client max_attempts must be 1")
        if list(getattr(client, "provider_order", []) or []):
            raise ProbeError("provider fallback/routing order must be empty")
        if getattr(client, "guard", None) is not guard:
            raise ProbeError("agent client is not bound to this run's LedgerGuard")
        provenance = ProvenanceLog(provenance_path, run_id=manifest["run_id"])
        raw_log = RawReplyLog(raw_path, run_id=manifest["run_id"])
        result = run_generation_probe(
            manifest=manifest, policy=policy, store=store, config_hash=config_hash,
            agent_client=client, provenance=provenance, raw_log=raw_log, repo_root=ROOT)
    except (LedgerError, ProbeError, BriefVersionError, RuntimeError) as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_STOPPED_BY_GUARD

    try:
        summary = write_probe_outputs(out_dir, result)
    except (OSError, FileExistsError) as exc:
        print(f"STOP: run finished but outputs could not be written: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_FAILED
    _report(summary, store.load(expected_config_hash=config_hash, policy=policy),
            manifest["run_id"])
    return EXIT_RUN_INCOMPLETE if summary["stopped"] else EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "live"))
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="directory holding manifest.json and policy.json")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="fresh output directory; required for 'live'")
    parser.add_argument("--init-ledger", action="store_true",
                        help="create the ledger once; required on the first run")
    parser.add_argument("--confirm-live", action="store_true",
                        help="required for 'live'; sends real, billable requests")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.run_dir)
    if not args.confirm_live or args.out_dir is None:
        print("error: 'live' needs --out-dir and --confirm-live (and owner authorization)",
              file=sys.stderr)
        return EXIT_CONFIG
    return live(args.run_dir, args.out_dir, args.init_ledger)


if __name__ == "__main__":
    sys.exit(main())



