#!/usr/bin/env python3
"""Rebind the canonical spend ledger from one spend config file to another.

The ledger binds to the SHA-256 of one spend config and allows one model per config.
To run a second model, keep each model in its own config file and rebind between them:

    python scripts/rebind_spend_config.py --from configs/spend.json \
        --to configs/spend_gpt54mini.json --reason "GPT receiver arm" --by MM

The rebind is refused unless ``--from`` is exactly the config the ledger is bound to
and ``--to`` parses as a valid policy that still covers every allocation/phase pair in
use. A verified backup is written first, and an audited ``rebind`` record (old hash,
new hash, both paths, reason, by, time) is appended. Spend history is unchanged: caps
and the backstop keep counting everything already committed.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from monitors.ledger import LedgerError, LedgerStore, Policy, config_sha256, utc_now  # noqa: E402

LEDGER_PATH = ROOT / "results" / "spend" / "ledger.json"


def rebind(store: LedgerStore, from_path: Path, to_path: Path, reason: str, by: str) -> dict:
    old_bytes, new_bytes = from_path.read_bytes(), to_path.read_bytes()
    old_hash, new_hash = config_sha256(old_bytes), config_sha256(new_bytes)
    if old_hash == new_hash:
        raise LedgerError("--from and --to have identical contents; nothing to rebind")
    new_policy = Policy.from_config(json.loads(new_bytes.decode("utf-8")))
    new_policy.require_model()
    store.load(expected_config_hash=old_hash)  # refuse unless bound to --from
    store.backup(reason=f"before rebind: {reason}", by=by)

    def operation(ledger: dict) -> dict:
        if ledger["config_sha256"] != old_hash:
            raise LedgerError("ledger binding changed during rebind")
        record = {
            "audit_id": str(uuid.uuid4()), "type": "rebind", "ts": utc_now(), "by": by,
            "reason": reason, "old_config_sha256": old_hash, "new_config_sha256": new_hash,
            "old_config_path": str(from_path), "new_config_path": str(to_path),
        }
        ledger["audit"].append(record)
        ledger["config_sha256"] = new_hash
        return record

    record = store.transact(operation, policy=new_policy)
    store.load(expected_config_hash=new_hash, policy=new_policy)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="from_path", type=Path, required=True)
    parser.add_argument("--to", dest="to_path", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--by", required=True)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        record = rebind(LedgerStore(args.ledger), args.from_path, args.to_path, args.reason, args.by)
    except (LedgerError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"rebound {record['old_config_sha256'][:12]} -> {record['new_config_sha256'][:12]} "
          f"({args.to_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
