#!/usr/bin/env python3
"""Run monitor views over a frozen corpus.

Dry run (no API, fake client, no ledger):
    python scripts/run_monitors.py --dry-run

Real run (requires APART_API_KEY, an initialised canonical ledger, and the
spend config the ledger is bound to):
    python scripts/run_monitors.py --allocation sprint --phase gate \
        --spend-config configs/spend.json --traces data/frozen/gate_v2_traces.jsonl

A real run must also set ``APART_BASE_URL`` explicitly: the implicit OpenAI
endpoint fallback is disabled for this entry point, so a gateway-configured
model cannot be sent to an unintended provider. The monitor client is built with
``max_attempts=1`` (one HTTP attempt per parse attempt; the three-attempt parse
retry cap lives in ``monitors.runner``), and any other value, provider routing,
or unbound client is refused before the first request.

Spend control is the canonical ledger only (``results/spend/ledger.json``).
There is no option to pick another ledger. The run is opened with a backup,
stale reservations are recovered, and any ledger, budget or persistence error
stops the run before anything else is sent.

``--views`` selects a subset of the views (for example ``--views M1,M2,M3,M4``).
The default is every view in ``VIEW_ORDER``, so existing invocations are
unchanged.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart.schema import trace_from_dict  # noqa: E402
from monitors.client import (  # noqa: E402
    FakeClient,
    LedgerGuard,
    ModelClient,
    SpendConfigError,
    load_apart_env,
    validate_base_url,
)
from monitors.ledger import (  # noqa: E402
    ALLOCATIONS,
    LedgerError,
    LedgerStore,
    Policy,
    check_pair,
    config_sha256,
    make_run_header,
    open_run,
    sweep_stale,
)
from monitors.prompts import PROMPT_VERSION  # noqa: E402
from monitors.runner import (  # noqa: E402
    VIEW_ORDER,
    VIEW_PLAN,
    non_ok_rate,
    run_corpus,
    select_views,
    should_stop,
    system_prompt_sha256,
    write_id_map,
    write_scores,
)
from monitors.run_status import (  # noqa: E402
    RunStatus,
    RunStatusError,
    build_metadata,
    default_status_path,
)

PHASES = ("smoke", "gate", "pilot", "extension")
LEDGER_PATH = ROOT / "results" / "spend" / "ledger.json"  # canonical; never user-selectable
DEFAULT_BASE_URL = "https://api.openai.com/v1"


def load_traces(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [trace_from_dict(json.loads(line)) for line in handle if line.strip()]


def _fail(message: str, code: int = 3) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def _record_status(action, **fields) -> None:
    """Record a terminal run status; a status-write failure is never fatal.

    The run's own exit code must not be masked by a problem writing the status
    record, so a failure here is reported and ignored.
    """
    try:
        action(**fields)
    except RunStatusError as exc:
        print(f"warning: could not record run status: {exc}", file=sys.stderr)


def _check_monitor_client(client, guard) -> None:
    """Fail closed unless the monitor client cannot retry, route, or detach.

    The monitor retry policy is one HTTP attempt per parse attempt; the parse
    attempts stay capped at three inside ``monitors.runner``. ``getattr`` uses a
    strict default, so a client that does not expose ``max_attempts`` is refused
    rather than assumed to be single-attempt.
    """
    attempts = getattr(client, "max_attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts != 1:
        raise SpendConfigError(
            f"monitor client max_attempts must be exactly 1; got {attempts!r}. "
            "HTTP retries are disabled for this entry point"
        )
    routing = list(getattr(client, "provider_order", None) or [])
    if routing:
        raise SpendConfigError(
            f"provider routing/fallback must be disabled for monitor runs; got {routing!r}"
        )
    if getattr(client, "guard", None) is not guard:
        raise SpendConfigError("monitor client is not bound to this run's LedgerGuard")


def open_guarded_client(args) -> tuple:
    """Validate everything, then open the run. Returns (client, store, config_hash)."""
    if not args.allocation or not args.phase:
        raise SpendConfigError("--allocation and --phase are required for a real run")
    check_pair(args.allocation, args.phase)
    if args.spend_config is None:
        raise SpendConfigError("--spend-config or APART_SPEND_CONFIG is required for a real run")
    try:
        config_bytes = Path(args.spend_config).read_bytes()
        config = json.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SpendConfigError(f"cannot read spend config {args.spend_config}: {exc}") from exc
    config_hash = config_sha256(config_bytes)
    policy = Policy.from_config(config)
    provider, model = policy.require_model()
    policy.pair_cap(args.allocation, args.phase)
    if not os.environ.get("APART_API_KEY"):
        raise SpendConfigError("APART_API_KEY is not set")
    # The endpoint must be stated explicitly. Falling back to the OpenAI default
    # would silently send a gateway-configured model to the wrong provider.
    configured_base_url = os.environ.get("APART_BASE_URL")
    if not configured_base_url:
        raise SpendConfigError(
            "APART_BASE_URL is not set; the implicit endpoint fallback "
            f"({DEFAULT_BASE_URL}) is disabled for this entry point. Set APART_BASE_URL "
            f"to the endpoint that serves {provider!r}."
        )
    base_url = validate_base_url(configured_base_url)

    store = LedgerStore(LEDGER_PATH)
    store.load(expected_config_hash=config_hash, policy=policy)  # missing/invalid/mismatch -> stop

    run_id = args.run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    header = make_run_header(
        run_id, args.allocation, args.phase, config_hash,
        {
            "traces_sha256": hashlib.sha256(Path(args.traces).read_bytes()).hexdigest(),
            "system_prompt_sha256": system_prompt_sha256(),
            "prompt_version": PROMPT_VERSION,
        },
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        provenance={"model": f"{provider}/{model}", "base_url": base_url,
                    "spend_config_path": str(args.spend_config)},
    )
    open_run(store, policy, header, config_hash, args.by)            # backup first, then the header
    swept = sweep_stale(store, config_hash, policy.stale_after_s)    # recover before any new spend
    if swept:
        print(f"recovered {len(swept)} stale reservation(s) as unknown (worst case kept)")
    guard = LedgerGuard(store, policy, config_hash, run_id)
    client = ModelClient(guard=guard, base_url=base_url, load_env=False, max_attempts=1)
    _check_monitor_client(client, guard)     # refuse before the first request
    return client, store, config_hash


def main(argv=None) -> int:
    load_apart_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run M1-M4 monitor views.")
    parser.add_argument(
        "--traces", type=Path, default=ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "scores_v1.jsonl")
    parser.add_argument("--id-map", type=Path, default=ROOT / "results" / "id_map_v1.csv")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "results" / "raw")
    parser.add_argument("--allocation", choices=ALLOCATIONS, help="Required for real runs.")
    parser.add_argument("--phase", choices=PHASES, help="Required for real runs.")
    parser.add_argument(
        "--spend-config",
        type=Path,
        default=Path(os.environ["APART_SPEND_CONFIG"]) if os.environ.get("APART_SPEND_CONFIG") else None,
        help="Spend config; must be the exact file the ledger is bound to.",
    )
    parser.add_argument("--run-id", default=None, help="Stable id for per-run accounting.")
    parser.add_argument(
        "--views",
        default=None,
        help=("Comma-separated subset of the monitor views, e.g. M1,M2,M3,M4. "
              f"Default: every view in {','.join(VIEW_ORDER)}."),
    )
    parser.add_argument("--by", default="MM", help="Audit annotation for the run-open backup (not authentication).")
    parser.add_argument("--dry-run", action="store_true", help="Use a fake client; no API calls, no ledger.")
    parser.add_argument("--created-utc", default=None)
    parser.add_argument(
        "--status",
        type=Path,
        default=None,
        help=("Run status record path. Default: '<out>.status.json'. Created "
              "exclusively; an existing status file is never overwritten."),
    )
    args = parser.parse_args(argv)

    try:
        views = select_views(None if args.views is None
                             else [name.strip() for name in args.views.split(",") if name.strip()])
    except ValueError as exc:
        return _fail(str(exc))

    traces = load_traces(args.traces)
    store = None
    config_hash = provider = model = base_url = None
    if args.dry_run:
        client = FakeClient()
    else:
        try:
            client, store, config_hash = open_guarded_client(args)
        except (SpendConfigError, LedgerError) as exc:
            return _fail(str(exc))
        guard = getattr(client, "guard", None)
        provider = getattr(guard, "provider", None)
        model = getattr(client, "model", None)
        base_url = getattr(client, "base_url", None)

    # Operational status record: created before execution, written beside the
    # outputs, never overwritten. It states no cap and records no secret.
    status_path = args.status or default_status_path(args.out)
    records_expected = len(traces) * sum(VIEW_PLAN[view][1] for view in views)
    try:
        status = RunStatus.begin(
            status_path,
            run_id=args.run_id or "dry-run",
            metadata=build_metadata(
                traces_path=args.traces,
                prompt_version=PROMPT_VERSION,
                system_prompt_sha256=system_prompt_sha256(),
                views=views,
                dry_run=args.dry_run,
                provider=provider,
                model=model,
                base_url=base_url,
                config_sha256=config_hash,
                allocation=args.allocation,
                phase=args.phase,
            ),
            counts={"traces": len(traces), "records_expected": records_expected},
            outputs={"scores": str(args.out), "id_map": str(args.id_map),
                     "raw_dir": str(args.raw_dir)},
        )
        status.running()
    except RunStatusError as exc:
        return _fail(str(exc))

    try:
        records, ensembles = run_corpus(
            traces, client, args.created_utc, raw_dir=args.raw_dir, views=views
        )
    except LedgerError as exc:  # budget refusal, persistence failure, invalid ledger
        print(f"STOP: {exc}", file=sys.stderr)
        _record_status(status.failed, error=f"{type(exc).__name__}: {exc}", exit_code=4)
        return 4
    except FileExistsError as exc:  # a raw reply already exists for this corpus
        _record_status(status.failed, error=f"raw output collision: {exc}", exit_code=1)
        raise
    try:
        write_scores(records, args.out)
        write_id_map(records, args.id_map)
    except FileExistsError as exc:
        # An existing output file is never overwritten; record the run as failed.
        _record_status(status.failed, error=f"output collision: {exc}",
                       records_written=len(records), exit_code=1)
        raise

    print(f"{len(traces)} traces, {len(records)} records -> {args.out}")
    print(f"views: {','.join(views)}")
    print("dry-run: fake client, no API calls" if args.dry_run else f"model: {client.model}")
    for view in views:
        print(f"  {view}: non-ok {non_ok_rate(records, view):.1%}")
    if store is not None:
        from monitors.ledger import commitments

        totals = commitments(store.load(expected_config_hash=config_hash))
        print(f"ledger commitment: allocation {args.allocation} "
              f"{totals['allocation'].get(args.allocation, 0)} µ$, total {totals['total']} µ$")
    if should_stop(records):
        print("STOP: a view exceeds 10% non-ok on the gate/sanity set (spec §5.6).")
        _record_status(status.stopped, reason="non-ok rate above 10% on at least one view",
                       records_written=len(records), exit_code=2)
        return 2
    try:
        status.completed(records_written=len(records),
                         required_outputs=[args.out, args.id_map])
    except RunStatusError as exc:
        _record_status(status.failed, error=f"completion check failed: {exc}",
                       records_written=len(records), exit_code=1)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
