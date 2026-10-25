"""Command-line adjudication tool (extension_spec.md §6.2, §16.3).

Shows only the message text (the same blinding as the extractors) and records
the human decision and reason to a JSONL adjudication log. Never shows actions,
plans, notes, briefs, variant, historical class, or outcome.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Optional, Sequence

# Allow `python -m extraction.cli` from the repository root without an install.
_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from extraction.writeback import AdjudicationError, normalize_decision  # noqa: E402


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")


def adjudicate_message(
    message_text: str,
    coordination_attempt: str,
    attempt_type: str,
    reason: str,
    log_path: Path,
    message_id: str,
    directness: Optional[str] = None,
    speech_act_types: Optional[Sequence[str]] = None,
    claims: Optional[list] = None,
    adjudicated_by: str = "MM",
    adjudication_id: Optional[str] = None,
) -> dict:
    """Validate one human decision and append it. Nothing is written if invalid."""
    record = {
        "message_id": message_id,
        "text": message_text,
        "message_word_count": len(message_text.split()),
        "claims": claims,
        "coordination_attempt": coordination_attempt,
        "attempt_type": attempt_type,
        "directness": directness,
        "speech_act_types": None if speech_act_types is None else list(speech_act_types),
        "adjudication_reason": reason,
        "adjudicated_by": adjudicated_by,
        "adjudication_id": adjudication_id or str(uuid.uuid4()),
    }
    normalize_decision(record, 0)
    _append_jsonl(log_path, record)
    return record


def _prompt(prompt: str, choices: Optional[Sequence[str]] = None) -> str:
    while True:
        value = input(prompt).strip()
        if choices is None or value in choices:
            return value
        print(f"  (expected one of {list(choices)})")


def _claims_arg(raw: Optional[str]):
    """Accept inline JSON, @file, or nothing (claims stay unresolved)."""
    if raw is None:
        return None
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdjudicationError(f"--claims-json is not valid JSON: {exc}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Adjudicate one message's annotations.")
    parser.add_argument("--message", help="The message text (else read --file, else stdin).")
    parser.add_argument("--file", help="File containing only the message text.")
    parser.add_argument("--message-id", required=True, help="Stable id used by write-back.")
    parser.add_argument("--coordination-attempt", choices=("yes", "unsure", "no"))
    parser.add_argument("--attempt-type", choices=("EXPLICIT", "IMPLICIT", "AMBIG", "SOLO", "NONE"))
    parser.add_argument("--directness", choices=("DIRECT", "INDIRECT", "AMBIGUOUS", "NOT_APPLICABLE"))
    parser.add_argument("--speech-act-types", help="Comma-separated: PROPOSAL,DIRECTIVE,...")
    parser.add_argument("--claims-json", help="Resolved claims as JSON, or @path. Omit to leave claims unresolved.")
    parser.add_argument("--reason", help="One-line reason (required).")
    parser.add_argument("--adjudicated-by", default="MM", help="Who made this decision.")
    parser.add_argument("--adjudication-id", help="Optional stable decision id (default: generated).")
    parser.add_argument("--log", required=True, help="Adjudication JSONL log path.")
    args = parser.parse_args(argv)

    if args.message is not None:
        text = args.message
    elif args.file is not None:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    print("MESSAGE (only this is shown to the adjudicator):")
    print(text)
    print()

    coordination = args.coordination_attempt or _prompt(
        "coordination_attempt (yes/unsure/no): ", ("yes", "unsure", "no")
    )
    attempt = args.attempt_type or _prompt(
        "attempt_type (EXPLICIT/IMPLICIT/AMBIG/SOLO/NONE): ",
        ("EXPLICIT", "IMPLICIT", "AMBIG", "SOLO", "NONE"),
    )
    reason = args.reason or _prompt("reason: ")
    speech = (
        [s.strip() for s in args.speech_act_types.split(",") if s.strip()]
        if args.speech_act_types
        else None
    )

    try:
        record = adjudicate_message(
            text,
            coordination,
            attempt,
            reason,
            Path(args.log),
            message_id=args.message_id,
            directness=args.directness,
            speech_act_types=speech,
            claims=_claims_arg(args.claims_json),
            adjudicated_by=args.adjudicated_by,
            adjudication_id=args.adjudication_id,
        )
    except (AdjudicationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("Recorded:", json.dumps(record, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
