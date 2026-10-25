#!/usr/bin/env python3
"""Convert frozen Stage 4N probe rows into a canonical monitor-corpus file.

    python scripts/probe_rows_to_traces.py \
        --rows runs/ext-probe-v21-sonnet46/out/probe_traces.jsonl \
        --out  reports/<stage2 dir>/corpus/probe_traces_canonical.jsonl

Reads the probe rows produced by ``apart.behavior_probe.run_probe`` and writes the
canonical ``apart.schema.Trace`` JSONL that ``scripts/run_monitors.py`` consumes.
The conversion is ``agents.runner.build_trace``, unchanged: this script only adds
provenance about where the rows came from and refuses to overwrite anything.

Offline only. The probe run directory is read, never written. No claims are
invented: the converted traces carry ``claims=[]`` because the Stage 4N manifest
sets ``extraction: false``, so no adjudicated claim set exists for this corpus.

Status: an engineering transformation for plumbing checks. The corpus it produces
is NOT an experimental corpus and NOT evidence about monitor performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from agents.runner import build_trace  # noqa: E402
from apart.schema import Trace  # noqa: E402

PROTECTED_DIRS = tuple(ROOT / name for name in ("data", "results", "configs", "docs", "AI Context"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def convert(rows: list[dict]) -> tuple[list[Trace], list[dict]]:
    """Return (canonical traces, per-row conversion failures)."""
    traces: list[Trace] = []
    failures: list[dict] = []
    for row in rows:
        trace_id = row.get("trace_id", "<missing trace_id>")
        try:
            trace = build_trace(dict(row))
            trace.validate()
            traces.append(trace)
        except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
            failures.append({"trace_id": trace_id, "error": f"{type(exc).__name__}: {exc}"})
    return traces, failures


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=Path,
                        default=ROOT / "runs" / "ext-probe-v21-sonnet46" / "out" / "probe_traces.jsonl")
    parser.add_argument("--out", type=Path, required=True,
                        help="canonical JSONL to create; must not exist")
    parser.add_argument("--source-run", default="ext-probe-v21-sonnet46")
    args = parser.parse_args(argv)

    rows = read_jsonl(args.rows)
    traces, failures = convert(rows)

    out = Path(args.out).resolve()
    if any(out == protected or protected in out.parents for protected in PROTECTED_DIRS):
        print(f"error: refusing to write under a protected directory: {out}", file=sys.stderr)
        return 2
    if out.exists():
        print(f"error: refusing to overwrite an existing file: {out}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            handle.write(json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    sidecar = {
        "source_run": args.source_run,
        "source_rows": str(args.rows),
        "source_rows_sha256": sha256_file(args.rows),
        "converter": "agents.runner.build_trace",
        "n_rows": len(rows),
        "n_converted": len(traces),
        "n_failed": len(failures),
        "failures": failures,
        "claims": "empty: the Stage 4N manifest sets extraction=false, so no claims exist",
        "status": "engineering smoke corpus, not an experimental corpus",
    }
    (out.parent / "corpus_manifest.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{len(traces)}/{len(rows)} rows -> {out}")
    if failures:
        for failure in failures:
            print(f"FAILED {failure['trace_id']}: {failure['error']}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
