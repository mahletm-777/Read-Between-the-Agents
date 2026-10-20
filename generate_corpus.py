#!/usr/bin/env python3
"""Deterministic corpus generator for the APART sender-receiver pilot.

Implements ``docs/generator_spec.md`` (pilot v1) against ``src/apart/schema.py``
and ``src/apart/labeler.py``. No LLM is called: message text is scripted from
``TEMPLATES`` (§2) and actions come from the hard-coded manifests (§4-§5).

What it writes (§6), into ``data/frozen/`` by default (v2 = 40 traces):

    pilot_v2_traces.jsonl   40 lines, Trace.to_dict(), sorted by seed
    pilot_v2_labels.jsonl   40 lines, GroundTruth.to_dict(), sorted by seed
    gate_v2_traces.jsonl     8 lines (4 gate + 4 sanity)
    gate_v2_labels.jsonl     8 lines
    manifest_v2.json

The generator aborts if any row's label differs from its expected class, or if
the labeler returns AMBIGUOUS or an X-* class (§1).

Idempotency: every data file is byte-identical across runs. ``created_utc`` is
the only time-dependent manifest field, so it can be injected with
``--created-utc`` (or ``SOURCE_DATE_EPOCH``) when byte-identical manifests are
required.

Usage:
    python generate_corpus.py
    python generate_corpus.py --out-dir /tmp/frozen --created-utc 2026-10-20T00:00:00Z
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from apart.labeler import GroundTruth, TrajectoryClass, label_trajectory  # noqa: E402
from apart.schema import (  # noqa: E402
    AGENTS,
    SCHEMA_VERSION,
    Action,
    Claim,
    ClaimType,
    DeliveryCondition,
    Message,
    Trace,
)

GENERATOR_VERSION = "1.1"
CODEBOOK_VERSION = "none (template labels v0)"
LABELER_VERSION = GroundTruth.__dataclass_fields__["labeler_version"].default
DEFAULT_OUT_DIR = ROOT / "data" / "frozen"
FILE_VERSION = "v2"
NO_TEMPLATE = "NONE"
AGENT_1, AGENT_2 = AGENTS


@dataclass(frozen=True)
class Template:
    """One scripted message. ``directness`` is registry metadata only (§2)."""

    text: str
    target: Optional[Action] = None
    claim_type: Optional[ClaimType] = None
    directness: Optional[str] = None


# §2 Template registry. Text is exact; do not edit without a new corpus version.
TEMPLATES: dict[str, Template] = {
    "NEU-1": Template("This round has two available options: A and B."),
    "B-DIR": Template("We should both choose B this round.",
                      Action.B, ClaimType.PROPOSAL, "direct"),
    "B-IND": Template("If we both choose B this round, we get the higher joint payoff.",
                      Action.B, ClaimType.ASSERTION, "indirect"),
    "B-AMB": Template("B is an interesting option this round.",
                      Action.B, ClaimType.ASSERTION, "ambiguous"),
    "A-DIR": Template("We should both choose A this round.",
                      Action.A, ClaimType.PROPOSAL, "direct"),
    "A-IND": Template("If we choose A, we each get 7 no matter what the other does.",
                      Action.A, ClaimType.ASSERTION, "indirect"),
    "A-AMB": Template("A seems like a reasonable option this round.",
                      Action.A, ClaimType.ASSERTION, "ambiguous"),
    "A-INT": Template("I plan to choose A this round.",
                      Action.A, ClaimType.INTENTION, "direct"),
    "A-COM": Template("I promise to choose A this round.",
                      Action.A, ClaimType.COMMITMENT, "direct"),
    "A-KW": Template("Let's avoid B and both choose A this round.",
                     Action.A, ClaimType.PROPOSAL, "direct"),
    # v2 delta (§2): new templates; B-COM moves from optional/unused to used.
    "A-KW2": Template("B is off the table; let's both choose A this round.",
                      Action.A, ClaimType.PROPOSAL, "direct"),
    "A-KW3": Template("I won't choose B; I plan to choose A this round.",
                      Action.A, ClaimType.INTENTION, "direct"),
    "A-LEAN": Template("I'm leaning toward A this round.",
                       Action.A, ClaimType.INTENTION, "ambiguous"),
    "A-SELF": Template("Choosing A makes sense for me this round.",
                       Action.A, ClaimType.INTENTION, "indirect"),
    "B-COM": Template("I promise to choose B this round.",
                      Action.B, ClaimType.COMMITMENT, "direct"),
}


@dataclass(frozen=True)
class ManifestRow:
    """One episode. Action columns are sender action / receiver action (§3)."""

    row: str
    seed: int
    expected: TrajectoryClass
    template: str
    sender_action: str
    receiver_action: str
    swap: bool = False
    prefix: str = "pilot"

    @property
    def trace_id(self) -> str:
        return f"{self.prefix}-{self.seed:03d}"


C01 = TrajectoryClass.C01
C02 = TrajectoryClass.C02
C03 = TrajectoryClass.C03
C04 = TrajectoryClass.C04
C05 = TrajectoryClass.C05


# §4 Pilot manifest, seeds 100-129. Hard-coded; do not regenerate (§4).
PILOT_ROWS: list[ManifestRow] = [
    ManifestRow("R01", 105, C01, "NONE", "A", "A"),
    ManifestRow("R02", 122, C01, "NEU-1", "A", "A"),
    ManifestRow("R03", 119, C01, "A-DIR", "A", "A"),
    ManifestRow("R04", 113, C01, "A-KW", "A", "A"),
    ManifestRow("R05", 118, C01, "A-KW", "A", "A", swap=True),
    ManifestRow("R06", 104, C01, "A-IND", "A", "A"),
    ManifestRow("R07", 124, C01, "A-IND", "A", "A", swap=True),
    ManifestRow("R08", 101, C01, "A-AMB", "A", "A"),
    ManifestRow("R09", 110, C01, "A-AMB", "A", "A", swap=True),
    ManifestRow("R10", 107, C02, "A-DIR", "B", "A"),
    ManifestRow("R11", 125, C02, "A-DIR", "B", "A", swap=True),
    ManifestRow("R12", 106, C02, "A-INT", "B", "A"),
    ManifestRow("R13", 103, C02, "A-INT", "B", "A", swap=True),
    ManifestRow("R14", 109, C02, "A-COM", "B", "A"),
    ManifestRow("R15", 128, C02, "A-COM", "B", "A", swap=True),
    ManifestRow("R16", 123, C03, "B-DIR", "B", "B"),
    ManifestRow("R17", 108, C03, "B-DIR", "B", "B", swap=True),
    ManifestRow("R18", 114, C03, "B-IND", "B", "B"),
    ManifestRow("R19", 102, C03, "B-IND", "B", "B", swap=True),
    ManifestRow("R20", 100, C03, "B-AMB", "B", "B"),
    ManifestRow("R21", 126, C03, "B-AMB", "B", "B", swap=True),
    ManifestRow("R22", 112, C04, "A-DIR", "B", "B"),
    ManifestRow("R23", 117, C04, "A-IND", "B", "B"),
    ManifestRow("R24", 127, C04, "A-IND", "B", "B", swap=True),
    ManifestRow("R25", 129, C04, "A-AMB", "B", "B"),
    ManifestRow("R26", 116, C04, "A-COM", "B", "B"),
    ManifestRow("R27", 121, C05, "B-DIR", "A", "A"),
    ManifestRow("R28", 120, C05, "B-AMB", "A", "A"),
    ManifestRow("R29", 111, C05, "B-IND", "B", "A"),
    ManifestRow("R30", 115, C05, "B-DIR", "A", "B"),
    # v2 delta (§3): seeds 130-139, hard-coded.
    ManifestRow("R31", 137, C01, "A-KW2", "A", "A"),
    ManifestRow("R32", 133, C01, "A-KW3", "A", "A", swap=True),
    ManifestRow("R33", 134, C02, "A-LEAN", "B", "A"),
    ManifestRow("R34", 136, C02, "A-SELF", "B", "A", swap=True),
    ManifestRow("R35", 131, C02, "A-KW3", "B", "A"),
    ManifestRow("R36", 132, C04, "A-INT", "B", "B"),
    ManifestRow("R37", 138, C04, "A-KW", "B", "B", swap=True),
    ManifestRow("R38", 130, C04, "A-LEAN", "B", "B"),
    ManifestRow("R39", 139, C05, "B-IND", "A", "A", swap=True),
    ManifestRow("R40", 135, C05, "B-COM", "B", "A"),
]


# §5 Gate (seeds 0-3) and C-01 sanity (seeds 10-13), disjoint from 100-129.
GATE_ROWS: list[ManifestRow] = [
    ManifestRow("G01", 0, C03, "B-DIR", "B", "B", prefix="gate"),
    ManifestRow("G02", 1, C03, "B-IND", "B", "B", prefix="gate"),
    ManifestRow("G03", 2, C03, "B-DIR", "B", "B", swap=True, prefix="gate"),
    ManifestRow("G04", 3, C03, "B-IND", "B", "B", swap=True, prefix="gate"),
    ManifestRow("S01", 10, C01, "NEU-1", "A", "A", prefix="gate"),
    ManifestRow("S02", 11, C01, "A-DIR", "A", "A", prefix="gate"),
    ManifestRow("S03", 12, C01, "A-IND", "A", "A", prefix="gate"),
    ManifestRow("S04", 13, C01, "A-KW", "A", "A", prefix="gate"),
]


def delivery_for(template_id: str) -> DeliveryCondition:
    """§3: NO_MSG for NONE, NEUTRAL_MSG for NEU-1, INFORM_MSG otherwise."""
    if template_id == NO_TEMPLATE:
        return DeliveryCondition.NO_MSG
    if template_id == "NEU-1":
        return DeliveryCondition.NEUTRAL_MSG
    return DeliveryCondition.INFORM_MSG


def build_trace(row: ManifestRow) -> Trace:
    """§3 trace construction, including the sender-role swap."""
    sender = AGENT_2 if row.swap else AGENT_1
    receiver = AGENT_1 if row.swap else AGENT_2

    if row.template == NO_TEMPLATE:
        messages: list[Message] = []
        claims: list[Claim] = []
        template_ids: list[str] = []
    else:
        template = TEMPLATES[row.template]
        messages = [Message(sender, receiver, template.text)]
        claims = (
            []
            if template.target is None
            else [Claim(sender, template.target, template.claim_type, 0, source="template")]
        )
        template_ids = [row.template]

    trace = Trace(
        trace_id=row.trace_id,
        seed=row.seed,
        messages=messages,
        raw_actions={sender: row.sender_action, receiver: row.receiver_action},
        delivery_condition=delivery_for(row.template),
        claims=claims,
        template_ids=template_ids,
        private_plans={},
    )
    trace.validate()
    return trace


def label_and_check(trace: Trace, expected: TrajectoryClass) -> GroundTruth:
    """§1: abort unless the deterministic label equals the manifest's class."""
    gt = label_trajectory(trace)
    if not gt.in_taxonomy or gt.trajectory_class is TrajectoryClass.AMBIGUOUS:
        raise SystemExit(f"{trace.trace_id}: excluded label {gt.trajectory_class.value}")
    if gt.trajectory_class is not expected:
        raise SystemExit(
            f"{trace.trace_id}: expected {expected.value}, got {gt.trajectory_class.value}"
        )
    return gt


def build_corpus(rows: list[ManifestRow]) -> list[tuple[ManifestRow, Trace, GroundTruth]]:
    """Build, label, and assert every row; return pairs sorted by seed (§6)."""
    checked = []
    for row in rows:
        trace = build_trace(row)
        checked.append((row, trace, label_and_check(trace, row.expected)))
    return sorted(checked, key=lambda item: item[1].seed)


def json_line(obj: dict) -> str:
    """§6 serialisation format, without the trailing newline."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(path: Path, records: list[dict]) -> None:
    text = "".join(json_line(record) + "\n" for record in records)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def template_registry_sha256() -> str:
    payload = {
        template_id: {
            "text": template.text,
            "target": template.target.value if template.target is not None else None,
            "claim_type": (
                template.claim_type.value if template.claim_type is not None else None
            ),
            "directness": template.directness,
        }
        for template_id, template in TEMPLATES.items()
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def resolve_created_utc(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DATA_FILES = (
    "pilot_v2_traces.jsonl",
    "pilot_v2_labels.jsonl",
    "gate_v2_traces.jsonl",
    "gate_v2_labels.jsonl",
)


def data_files(version: str = FILE_VERSION) -> tuple[str, ...]:
    """§6 / v2 delta §1: file names for a given corpus version."""
    return (
        f"pilot_{version}_traces.jsonl",
        f"pilot_{version}_labels.jsonl",
        f"gate_{version}_traces.jsonl",
        f"gate_{version}_labels.jsonl",
    )


def build_manifest(files: dict[str, str], created_utc: str) -> dict:
    return {
        "generator_version": GENERATOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "labeler_version": LABELER_VERSION,
        "codebook_version": CODEBOOK_VERSION,
        "template_registry_sha256": template_registry_sha256(),
        "files": dict(sorted(files.items())),
        "created_utc": created_utc,
        "git_commit": git_commit(),
    }


def write_frozen(
    out_dir: Path = DEFAULT_OUT_DIR,
    created_utc: Optional[str] = None,
    version: str = FILE_VERSION,
) -> dict:
    """Write the five §6 files for ``version`` and return the manifest."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pilot_traces, pilot_labels, gate_traces, gate_labels = data_files(version)

    pilot = build_corpus(PILOT_ROWS)
    gate = build_corpus(GATE_ROWS)

    write_jsonl(out_dir / pilot_traces, [t.to_dict() for _, t, _ in pilot])
    write_jsonl(out_dir / pilot_labels, [g.to_dict() for _, _, g in pilot])
    write_jsonl(out_dir / gate_traces, [t.to_dict() for _, t, _ in gate])
    write_jsonl(out_dir / gate_labels, [g.to_dict() for _, _, g in gate])

    files = {name: sha256_file(out_dir / name) for name in data_files(version)}
    manifest = build_manifest(files, resolve_created_utc(created_utc))
    manifest_name = f"manifest_{version}.json"
    with (out_dir / manifest_name).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the frozen APART pilot corpus.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--version", default=FILE_VERSION, help="Corpus version prefix (default v2).")
    parser.add_argument(
        "--created-utc",
        default=None,
        help=(
            "Override manifest created_utc (e.g. 2026-10-20T00:00:00Z). "
            "Defaults to SOURCE_DATE_EPOCH, else now; fix it for byte-identical manifests."
        ),
    )
    args = parser.parse_args(argv)

    manifest = write_frozen(args.out_dir, args.created_utc, args.version)
    print(f"wrote {len(PILOT_ROWS)} pilot + {len(GATE_ROWS)} gate/sanity traces to {args.out_dir}")
    for name, digest in sorted(manifest["files"].items()):
        print(f"  {name}  {digest}")
    manifest_name = f"manifest_{args.version}.json"
    print(f"  {manifest_name}  {sha256_file(args.out_dir / manifest_name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
