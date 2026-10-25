"""Monitor runner: renders views, calls the client, applies §5/§6, writes §4."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from apart.schema import Trace

from .client import ApiError
from .observable import observable
from .parser import RETRY_MESSAGE, aggregate_ensemble, parse_response
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, display_id, render_view

# view -> (temperature, calls, max_tokens); monitor_spec.md §1
VIEW_PLAN = {
    "M1": (0.0, 1, 250),
    "M2": (0.0, 1, 250),
    "M3": (0.0, 1, 250),
    "M3-ens": (0.7, 5, 250),
    "M3-T07": (0.7, 1, 250),
    "M4": (0.0, 1, 600),
}
VIEW_ORDER = ("M1", "M2", "M3", "M3-ens", "M3-T07", "M4")
ENSEMBLE_VIEW = "M3-ens"
_VIEW_INDEX = {view: index for index, view in enumerate(VIEW_ORDER)}


def select_views(views: Optional[Sequence[str]] = None) -> tuple[str, ...]:
    """Validate and normalise a view selection; ``None`` means every view.

    The canonical ``VIEW_ORDER`` is returned for ``None``, so the default call is
    unchanged. Any explicit selection is returned in canonical order, which keeps
    the per-view RNG seed (``_VIEW_INDEX``) and therefore the rendered records
    identical to the corresponding views of a full run.
    """
    if views is None:
        return VIEW_ORDER
    selected = tuple(views)
    if not selected:
        raise ValueError("at least one view must be selected")
    unknown = sorted({view for view in selected if view not in VIEW_PLAN})
    if unknown:
        raise ValueError(f"unknown view(s) {unknown}; known views are {list(VIEW_ORDER)}")
    if len(set(selected)) != len(selected):
        raise ValueError(f"duplicate view(s) in the selection: {list(selected)}")
    return tuple(sorted(selected, key=_VIEW_INDEX.__getitem__))



def prompt_sha256(system: str, user: str) -> str:
    """§4: sha256 over system + user bytes."""
    return hashlib.sha256((system + user).encode("utf-8")).hexdigest()


def _display_for(seed: int, view: str) -> str:
    """M3-T07 is the M3 prompt itself, so it reuses the M3 display id."""
    return display_id(seed, "M3" if view == "M3-T07" else view)


def system_prompt_sha256() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def call_key_prefix(client, seed: int, view: str, k: int) -> str:
    """Deterministic identity of one logical record call, fixed before any reservation."""
    guard = getattr(client, "guard", None)
    run_id = getattr(guard, "run_id", None) or "dry-run"
    return f"{run_id}:{seed}:{view}:{k}"


def _call_with_retries(
    client, base_messages, view, temperature, provider_seed, max_tokens,
    raw_sink=None, meta_sink=None, call_prefix=None
):
    """§5.5: up to 3 attempts; retry prompt after an invalid score.

    Each parse attempt is its own logical call ``<prefix>:p<n>``. Only
    ``ApiError`` becomes an ``api_error`` record; ledger, budget and
    persistence errors propagate so the whole corpus stops.
    """
    messages = list(base_messages)
    attempts = 0
    last_response_id = None
    parsed = None
    while attempts < 3:
        attempts += 1
        try:
            response = client.call_model(
                messages, temperature=temperature, seed=provider_seed, max_tokens=max_tokens,
                call_id=f"{call_prefix or 'adhoc'}:p{attempts}",
            )
        except ApiError:
            return None, attempts, last_response_id, "api_error"
        last_response_id = response.response_id
        if raw_sink is not None:
            raw_sink.append(response.text)
        if meta_sink is not None:
            meta_sink.update(
                {
                    "served_model": response.served_model,
                    "served_provider": response.served_provider,
                }
            )
        parsed = parse_response(response.text, view)
        if parsed.valid:
            return parsed, attempts, last_response_id, "ok"
        messages = list(base_messages) + [
            {"role": "assistant", "content": response.text},
            {"role": "user", "content": RETRY_MESSAGE},
        ]
    return parsed, attempts, last_response_id, "parse_failure"


def write_raw_response(raw_dir: Path, seed: int, view: str, k: int, texts: Sequence[str]) -> Path:
    """Persist raw reply text(s) for one (seed, view, k) to ``raw_dir``.

    Created exclusively (``x`` mode): an existing file is never overwritten, so
    a re-run pointed at a previous run's ``--raw-dir`` fails loudly instead of
    silently destroying evidence. Raw text is kept independently of the parsed
    score, and retries keep every attempt's text.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{seed}_{view}_{k}.json"
    payload = {"seed": seed, "view": view, "k": k, "responses": list(texts)}
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise FileExistsError(
            f"raw response {path} already exists; refusing to overwrite a previous run "
            f"(use a fresh --raw-dir per run)"
        ) from exc
    return path


def _record(trace, display, view, k, temperature, provider_seed, digest, parsed, status,
            attempts, response_id, created_utc, model, snapshot,
            max_tokens, provider_order, served_model, served_provider):
    usable = parsed is not None and parsed.valid
    return {
        "seed": trace.seed,
        "trace_id": trace.trace_id,
        # Scenario grouping metadata, carried from the trace (None when the
        # corpus establishes no scenario unit). Grouping only: `trace_id`
        # remains the join key for every consumer.
        "scenario_id": trace.scenario_id,
        "display_id": display,
        "view": view,
        "k": k,
        "model": model,
        "snapshot": snapshot,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "provider_order": list(provider_order),
        "provider_seed": provider_seed,
        "served_model": served_model,
        "served_provider": served_provider,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": digest,
        "score": parsed.score if usable else None,
        "rationale": parsed.rationale if usable else None,
        "checklist": parsed.checklist if usable else None,
        "checklist_valid": parsed.checklist_valid if usable else None,
        "status": status,
        "n_attempts": attempts,
        "response_id": response_id,
        "created_utc": created_utc,
    }


def _sort_key(record: dict):
    return (record["seed"], _VIEW_INDEX[record["view"]], record["k"])


def run_corpus(
    traces: Sequence[Trace],
    client,
    created_utc: Optional[str] = None,
    raw_dir: Optional[Path] = None,
    views: Optional[Sequence[str]] = None,
) -> tuple[list[dict], dict[int, dict]]:
    """Run the selected views over ``traces``; return (records, ensemble summaries).

    ``observable`` is called once per trace and the same record feeds every
    view, so all views for a trace are built from the same evidence.
    Call order is shuffled per view (§1); order does not affect content.
    When ``raw_dir`` is given, each (seed, view, k) reply text is written to
    ``<raw_dir>/<seed>_<view>_<k>.json`` (retries keep all attempt texts).

    ``views`` selects a subset of ``VIEW_ORDER``; ``None`` (the default) runs
    every view exactly as before. Ensemble summaries are only produced when
    ``M3-ens`` is among the selected views.
    """
    selected = select_views(views)
    stamp = created_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    model = getattr(client, "model", None)
    snapshot = getattr(client, "snapshot", None)
    provider_order = list(getattr(client, "provider_order", []))
    observables = [(trace, observable(trace)) for trace in traces]
    records: list[dict] = []
    ensemble_scores: dict[int, list[Optional[int]]] = {trace.seed: [] for trace in traces}

    for view in selected:
        temperature, calls, max_tokens = VIEW_PLAN[view]
        ordered = list(observables)
        random.Random(_VIEW_INDEX[view]).shuffle(ordered)

        for trace, record in ordered:
            display = _display_for(trace.seed, view)
            user = render_view(record, view, display)
            base = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]
            digest = prompt_sha256(SYSTEM_PROMPT, user)
            if view == ENSEMBLE_VIEW:
                for k in range(calls):
                    raw_texts: list[str] = []
                    meta: dict = {}
                    parsed, attempts, response_id, status = _call_with_retries(
                        client, base, view, temperature, k, max_tokens,
                        raw_sink=raw_texts, meta_sink=meta,
                        call_prefix=call_key_prefix(client, trace.seed, view, k),
                    )
                    records.append(
                        _record(trace, display, view, k, temperature, k, digest, parsed,
                                status, attempts, response_id, stamp, model, snapshot,
                                max_tokens, provider_order,
                                meta.get("served_model"), meta.get("served_provider"))
                    )
                    if raw_dir is not None:
                        write_raw_response(raw_dir, trace.seed, view, k, raw_texts)
                    ensemble_scores[trace.seed].append(
                        parsed.score if parsed is not None and status == "ok" else None
                    )
            else:
                raw_texts = []
                meta = {}
                parsed, attempts, response_id, status = _call_with_retries(
                    client, base, view, temperature, 0, max_tokens,
                    raw_sink=raw_texts, meta_sink=meta,
                    call_prefix=call_key_prefix(client, trace.seed, view, 0),
                )
                records.append(
                    _record(trace, display, view, 0, temperature, 0, digest, parsed,
                            status, attempts, response_id, stamp, model, snapshot,
                            max_tokens, provider_order,
                            meta.get("served_model"), meta.get("served_provider"))
                )
                if raw_dir is not None:
                    write_raw_response(raw_dir, trace.seed, view, 0, raw_texts)

    ensembles = {}
    if ENSEMBLE_VIEW in selected:
        for trace in traces:
            score, valid_count, status = aggregate_ensemble(ensemble_scores[trace.seed])
            ensembles[trace.seed] = {
                "score": score,
                "valid_samples": valid_count,
                "status": status,
            }

    records.sort(key=_sort_key)
    return records, ensembles


def write_scores(records: Sequence[dict], path: Path) -> None:
    """§4: results/scores_v1.jsonl, one line per (seed, view, k).

    Created exclusively (``x`` mode): an existing file is never overwritten, so a
    run cannot destroy a previous run's scores. Use a fresh ``--out`` per run.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(
                    json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
    except FileExistsError as exc:
        raise FileExistsError(
            f"score output {path} already exists; refusing to overwrite a previous run "
            f"(use a fresh --out path per run)"
        ) from exc


def write_id_map(records: Sequence[dict], path: Path) -> None:
    """§2: seed <-> per-view display id, kept out of prompts.

    Created exclusively (``x`` mode): an existing file is never overwritten. Use a
    fresh ``--id-map`` per run.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = {(record["seed"], record["view"]): record["display_id"] for record in records}
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["seed", "view", "display_id"])
            for (seed, view), display in sorted(
                seen.items(), key=lambda item: (item[0][0], _VIEW_INDEX[item[0][1]])
            ):
                writer.writerow([seed, view, display])
    except FileExistsError as exc:
        raise FileExistsError(
            f"id map {path} already exists; refusing to overwrite a previous run "
            f"(use a fresh --id-map path per run)"
        ) from exc


def non_ok_rate(records: Sequence[dict], view: str) -> float:
    rows = [record for record in records if record["view"] == view]
    if not rows:
        return 0.0
    return sum(record["status"] != "ok" for record in rows) / len(rows)


def should_stop(records: Sequence[dict], threshold: float = 0.10) -> bool:
    """§5.6 stop rule on the gate/sanity set."""
    return any(non_ok_rate(records, view) > threshold for view in VIEW_ORDER)
