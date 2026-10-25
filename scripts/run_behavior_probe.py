"""Stage 4N behavioural probe: offline check, and the (unauthorized) live command.

    python scripts/run_behavior_probe.py check --run-dir runs/ext-probe-v21-sonnet46

``check`` is offline: it validates the frozen manifest and policy, confirms the
prompt/code file hashes, prints the round-robin plan and the exact worst-case
reservation for every trace, and exits non-zero if the plan does not fit the cap.
It constructs no client and reads no credentials.

``live`` is the only path that can send a request. It requires ``--confirm-live``
and an explicit owner authorization recorded outside this script. As of
2026-10-23 live execution of Stage 4N is NOT authorized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from apart.behavior_probe import (ProbeError, check_binding, load_probe_manifest,  # noqa: E402
                                  plan_worst_case, probe_plan)

PROTECTED_DIRS = tuple(ROOT / name for name in ("data", "results", "configs", "docs", "AI Context"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(run_dir: Path):
    from monitors.ledger import Policy, config_sha256

    policy_bytes = (run_dir / "policy.json").read_bytes()
    policy = Policy.from_config(json.loads(policy_bytes.decode("utf-8")))
    manifest = load_probe_manifest(run_dir / "manifest.json")
    check_binding(manifest, policy)
    stale = {name: digest for name, digest in manifest["prompt_files_sha256"].items()
             if _sha256(ROOT / name) != digest}
    if stale:
        raise ProbeError(f"code/prompt files changed since the manifest was frozen: {sorted(stale)}")
    return manifest, policy, policy_bytes, config_sha256(policy_bytes)


def check(run_dir: Path) -> int:
    try:
        manifest, policy, _bytes, config_hash = _load(run_dir)
    except (OSError, ValueError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return 2
    worst = plan_worst_case(manifest, policy)
    print(f"run {manifest['run_id']} protocol={manifest['protocol_version']} "
          f"prompt={manifest['prompt_version']} policy_sha256={config_hash}")
    print("plan (round robin): " + ", ".join(f"ext-{c.seed:04d}:{c.variant}"
                                             for c in probe_plan(manifest)))
    for cell, trace in worst["per_cell"].items():
        stages = " ".join(f"{s}={v['micro']}" for s, v in trace["stages"].items())
        print(f"  {cell}: worst {trace['total_micro']} µ$ per trace ({trace['branch']}): {stages}")
    print(f"worst case for {worst['traces']} traces / {worst['calls']} calls: "
          f"{worst['total_micro']} µ$ against cap {worst['cap_micro']} µ$ -> "
          f"{'FITS' if worst['fits'] else 'DOES NOT FIT'}")
    return 0 if worst["fits"] else 3


def live(run_dir: Path, out_dir: Path, init_ledger: bool) -> int:  # pragma: no cover - live only
    from apart.behavior_probe import run_probe, write_probe_outputs
    from apart.provenance import ProvenanceLog
    from monitors.client import LedgerGuard, ModelClient, validate_base_url
    from monitors.ledger import LedgerError, LedgerStore, make_run_header, open_run

    run_dir, out_dir = run_dir.resolve(), out_dir.resolve()
    for target in (run_dir, out_dir):
        if any(target == p.resolve() or p.resolve() in target.parents for p in PROTECTED_DIRS):
            print(f"error: refusing to write under a protected directory: {target}", file=sys.stderr)
            return 2
    if out_dir.exists():
        print(f"error: output directory already exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        manifest, policy, policy_bytes, config_hash = _load(run_dir)
    except (OSError, ValueError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return 2
    provenance_path = run_dir / "provenance" / "calls.jsonl"
    if provenance_path.exists():
        print(f"error: provenance log already exists: {provenance_path}", file=sys.stderr)
        return 2
    ledger_path = run_dir / "spend" / "ledger.json"
    try:
        store = LedgerStore(ledger_path)
        if not ledger_path.exists():
            if not init_ledger:
                print(f"error: ledger {ledger_path} does not exist; pass --init-ledger once",
                      file=sys.stderr)
                return 2
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.init(policy_bytes)
        ledger = store.load(expected_config_hash=config_hash, policy=policy)
        if manifest["run_id"] in ledger["runs"]:
            print(f"error: run {manifest['run_id']!r} is already open in the ledger", file=sys.stderr)
            return 2
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
        client = ModelClient(guard=guard, base_url=base_url, load_env=True, max_attempts=1)
        provenance = ProvenanceLog(provenance_path, run_id=manifest["run_id"])
        result = run_probe(manifest=manifest, policy=policy, store=store,
                           config_hash=config_hash, agent_client=client, provenance=provenance)
    except (LedgerError, ProbeError, RuntimeError) as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    summary = write_probe_outputs(out_dir, result)
    print(json.dumps(summary["counts"], sort_keys=True))
    print(f"gate passed={summary['gate']['passed']} stopped={summary['stopped']!r}")
    return 5 if summary["stopped"] else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "live"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--init-ledger", action="store_true")
    parser.add_argument("--confirm-live", action="store_true",
                        help="required for 'live'; sends real, billable requests")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.run_dir)
    if not args.confirm_live or args.out_dir is None:
        print("error: 'live' needs --out-dir and --confirm-live (and owner authorization)",
              file=sys.stderr)
        return 2
    return live(args.run_dir, args.out_dir, args.init_ledger)


if __name__ == "__main__":
    sys.exit(main())
