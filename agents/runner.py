"""Three-stage agent protocol (extension_spec.md §1).

The stage code is client-agnostic. It runs under ``FakeAgentClient`` for the
offline path, or under a guarded ``monitors.client.ModelClient`` (constructed on
a ``LedgerGuard``) for real generation, because both expose the same
``call_model(messages, temperature, seed, max_tokens, call_id)`` signature.

Every stage request carries a deterministic ``call_id`` (``ext-<seed>:<stage>``)
with a ``:p<n>`` suffix per parse attempt, so the spend ledger can reserve each
attempt exactly once and no request can be dispatched without a guard.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional, Protocol

from apart.schema import DeliveryCondition, Message, Trace
from monitors.client import ApiError

from .client import FakeAgentClient
from .parser import RETRY_MESSAGE, ParsedStage, parse_action, parse_message, parse_plan
from .prompts import (
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    NEUTRAL_NOTICE,
    VARIANTS,
    r1_prompt,
    r2_prompt,
    receiver_system,
    s1_prompt,
    s2_prompt,
    s3_prompt,
    sender_system,
)

AGENT_TEMPERATURE = 1.0  # provider default (T = 1); §1

# Explicit retry policy: parse attempts per stage (§1). Transport retries are the
# client's own bound (``ModelClient.max_attempts``); this loop adds none on top.
MAX_STAGE_ATTEMPTS = 3
STATUS_API_ERROR = "api_error"
# Stage 4N: a response whose JSON-serialized size exceeds the run's carried-text
# bound is a failed stage. The bound is what makes a worst-case reservation for
# every later stage computable before the run (see ``apart.behavior_probe``).
STATUS_OUTPUT_BOUND = "output_bound_exceeded"
USAGE_REPORTED = "reported"

# How ``prompt_sha256`` is computed for agent-stage records written by this
# module: sha256 of ``json.dumps(messages, sort_keys=True, ensure_ascii=False)``
# over the full message list actually sent (system + carried turns + current
# prompt, plus any parse-retry turns). Stage 4K provenance predates this field and
# used sha256(system + user) over the two-message request; it is not rewritten.
REQUEST_HASH_FORMAT = "messages-json-sorted-v1"
USAGE_UNKNOWN = "unknown"

# ---------------------------------------------------------------- context access
# Stage 4M (owner decision 2): which earlier stages' turns each stage receives.
# A stage only ever receives its OWN earlier turns; the sender's private plan is
# never carried into a receiver stage, and no other trace's turns are reachable.
STAGE_CONTEXT: dict[str, tuple[str, ...]] = {
    "s1": (),
    "r1": (),
    "s2": ("s1",),
    "s3": ("s1", "s2"),
    "r2": ("r1",),
}

# Fields each role may see, for documentation and for the leak tests. ``private``
# means generator-only: never sent to the other agent.
INFORMATION_ACCESS = {
    "s1": {"own_brief": True, "own_plans": [], "own_messages": [],
           "received_messages": [], "game_state": "opening", "payoffs": True},
    "r1": {"own_brief": True, "own_plans": [], "own_messages": [],
           "received_messages": [], "game_state": "opening", "payoffs": True},
    "s2": {"own_brief": True, "own_plans": ["s1"], "own_messages": [],
           "received_messages": [], "game_state": "pre_message", "payoffs": True},
    "s3": {"own_brief": True, "own_plans": ["s1"], "own_messages": ["s2"],
           "received_messages": [], "game_state": "post_message", "payoffs": True},
    # The receiver sees only the delivered message text, which the protocol routes
    # through ``r2_prompt``; it never sees the sender's plan or the raw S2 reply.
    "r2": {"own_brief": True, "own_plans": ["r1"], "own_messages": [],
           "received_messages": ["delivered_message"], "game_state": "post_message",
           "payoffs": True},
}

# Generator-only fields that must never appear in any stage request.
PRIVATE_FIELDS = ("private_plans", "private_notes", "variant", "briefs",
                  "agent_model", "private_brief_intent", "message_declined")


class AgentClient(Protocol):
    """The agent-stage client contract.

    Satisfied by ``FakeAgentClient`` (offline) and by ``monitors.client.ModelClient``
    (live, guard-authorized). Both accept ``call_id`` and expose ``model``.
    """

    model: str

    def call_model(
        self,
        messages,
        temperature: float = 1.0,
        seed: Optional[int] = None,
        max_tokens: int = 150,
        call_id: Optional[str] = None,
    ): ...


def stage_call_id(seed: int, stage: str) -> str:
    """Deterministic, unique logical call id for one stage of one trace.

    ``_call_stage`` appends ``:p<n>`` per parse attempt, so every reservation in
    the ledger is unique while staying reproducible from (seed, stage, attempt).
    """
    return f"ext-{seed:04d}:{stage}"


def sender_for(seed: int) -> str:
    """Sender role by seed parity: agent_1 on even seeds, agent_2 on odd seeds."""
    return "agent_1" if seed % 2 == 0 else "agent_2"


def receiver_for(seed: int) -> str:
    return "agent_2" if sender_for(seed) == "agent_1" else "agent_1"


def _messages_sha256(messages: list) -> str:
    """Hash the exact request the stage sent, carried context included.

    Covers system prompt, every carried prior turn and the current stage prompt,
    so the manifest can prove which context each stage received.
    """
    canonical = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialized_bytes(text: str) -> int:
    """UTF-8 size of ``text`` as it appears inside a JSON-serialized request."""
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8"))


def _log_entry(attempt_id: str, stage: str, seed: int, client, response,
               status: str, prompt_sha256: str, error: Optional[str] = None,
               context_stages: tuple = (), input_messages: int = 2) -> dict:
    """Per-call provenance for one stage attempt.

    ``usage`` is the provider's value verbatim. ``None`` is recorded as
    ``usage_status="unknown"`` and is never converted into a zero-cost value.
    ``prompt_sha256`` covers the full message list (system + carried + current),
    computed as ``REQUEST_HASH_FORMAT`` names. ``context_stages`` names the earlier
    stages carried into the request; ``input_messages`` is the number of chat
    messages sent, counting the system message and any parse-retry turns.
    ``served_model``/``served_provider`` are what the provider reported, or None
    when it reported nothing; the requested model is never copied into them.
    """
    usage = getattr(response, "usage", None) if response is not None else None
    entry = {
        "call_id": attempt_id,
        "trace_id": f"ext-{seed:04d}",
        "stage": stage,
        "requested_model": getattr(client, "model", None),
        "served_model": getattr(response, "served_model", None) if response is not None else None,
        "served_provider": getattr(response, "served_provider", None) if response is not None else None,
        "response_id": getattr(response, "response_id", None) if response is not None else None,
        "usage": usage,
        "usage_status": USAGE_REPORTED if usage is not None else USAGE_UNKNOWN,
        "temperature": AGENT_TEMPERATURE,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "seed": seed,
        "prompt_sha256": prompt_sha256,
        "request_hash_format": REQUEST_HASH_FORMAT,
        "context_stages": list(context_stages),
        "input_messages": input_messages,
        "status": status,
    }
    if error is not None:
        entry["error"] = error
    return entry


def stage_turns(prompt: str, response) -> list:
    """The (user, assistant) turn pair a completed stage contributes onward.

    Returns ``[]`` when the stage produced no text at all (transport failure), so
    a failed stage silently contributes no context instead of a placeholder.
    """
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        return []
    return [{"role": "user", "content": prompt},
            {"role": "assistant", "content": text}]


def _call_stage(client, system: str, user: str, stage: str, seed: int,
                call_id: str, prior: Optional[list] = None,
                max_attempts: int = MAX_STAGE_ATTEMPTS,
                max_output_bytes: Optional[int] = None) -> tuple:
    """One stage: call + parse + up to ``max_attempts`` parse attempts.

    ``max_attempts=1`` (Stage 4N) sends exactly one request: an unparseable reply
    is recorded as ``parse_failure`` and is not retried. ``max_output_bytes``, when
    set, fails the stage (``output_bound_exceeded``) if the reply's serialized size
    exceeds the bound, so an oversized reply is never carried into a later stage.

    ``prior`` is the list of this agent's own earlier turns to carry into the
    request (see ``STAGE_CONTEXT``); it is inserted between the system prompt and
    the current stage prompt. Only the agent's own turns are ever passed here, so
    a receiver stage cannot receive a sender's private plan.

    Returns ``(ParsedStage, response_or_None, log)``. ``call_id`` identifies the
    logical call; each attempt appends ``:p<n>`` so the ledger reserves every
    attempt exactly once.

    ``ApiError`` (transport/HTTP failure after the client's own retry bound) fails
    only this stage, so the trace is retained as explicitly failed rather than
    being misread as valid. Ledger, budget and persistence errors are deliberately
    NOT caught here: they propagate so the caller stops the run — the same policy
    ``monitors/runner.py`` documents.
    """
    parse_fn = {"plan": parse_plan, "message": parse_message, "action": parse_action}[stage]
    context_stages = tuple(STAGE_CONTEXT.get(call_id.rsplit(":", 1)[-1], ()))
    carried = list(prior or [])
    messages = ([{"role": "system", "content": system}] + carried
                + [{"role": "user", "content": user}])
    log = []
    attempts = 0
    parsed = None
    response = None
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    while attempts < max_attempts:
        attempts += 1
        attempt_id = f"{call_id}:p{attempts}"
        prompt_sha256 = _messages_sha256(messages)
        try:
            response = client.call_model(
                messages, temperature=AGENT_TEMPERATURE, seed=seed,
                max_tokens=MAX_OUTPUT_TOKENS, call_id=attempt_id,
            )
        except ApiError as exc:
            log.append(_log_entry(attempt_id, stage, seed, client, None, STATUS_API_ERROR,
                                  prompt_sha256, error=type(exc).__name__,
                                  context_stages=context_stages, input_messages=len(messages)))
            return (
                ParsedStage(status=STATUS_API_ERROR, attempts=attempts, reason="api_error"),
                None,
                log,
            )
        if (max_output_bytes is not None
                and serialized_bytes(response.text if isinstance(response.text, str) else "")
                > max_output_bytes):
            log.append(_log_entry(attempt_id, stage, seed, client, response, STATUS_OUTPUT_BOUND,
                                  prompt_sha256, context_stages=context_stages,
                                  input_messages=len(messages)))
            return (ParsedStage(status=STATUS_OUTPUT_BOUND, attempts=attempts,
                                reason="output_bound_exceeded"), response, log)
        parsed = parse_fn(response.text)
        if (max_output_bytes is not None and parsed.status == "ok" and stage == "message"
                and serialized_bytes(parsed.data["message"]) > max_output_bytes):
            parsed = ParsedStage(status=STATUS_OUTPUT_BOUND, reason="output_bound_exceeded")
        log.append(_log_entry(attempt_id, stage, seed, client, response, parsed.status,
                              prompt_sha256, context_stages=context_stages,
                              input_messages=len(messages)))
        if parsed.status == STATUS_OUTPUT_BOUND:
            parsed.attempts = attempts
            return parsed, response, log
        if parsed.status in ("agent_refusal", "ok"):
            parsed.attempts = attempts
            if parsed.status == "agent_refusal":
                parsed.reason = "refusal_text"
            return parsed, response, log
        messages = messages + [
            {"role": "assistant", "content": response.text},
            {"role": "user", "content": RETRY_MESSAGE},
        ]
    if max_attempts == 1:
        # Stage 4N: no retry. The parse failure is recorded as such, not as a refusal.
        return (ParsedStage(status="parse_failure", attempts=1,
                            reason="parse_failure_no_retry"), response, log)
    # Exhausted retries without valid JSON -> agent_refusal (§1), with the cause kept.
    return (ParsedStage(status="agent_refusal", attempts=max_attempts,
                        reason="parse_retries_exhausted"), response, log)


def run_trace(seed: int, variant: str, condition: str, client: AgentClient, *,
              prompt_version: str = PROMPT_VERSION,
              max_stage_attempts: int = MAX_STAGE_ATTEMPTS,
              max_output_bytes: Optional[int] = None,
              stop_on_failure: bool = False) -> dict:
    """Run the §1 protocol for one trace. Returns stage results plus the call log.

    ``client`` may be ``FakeAgentClient`` (offline) or a guarded ``ModelClient``
    (live). One client serves every stage, so sender and receiver always use the
    same configured model.

    The keyword options default to the Stage 4K/4M behaviour. Stage 4N passes
    ``max_stage_attempts=1``, a ``max_output_bytes`` bound and
    ``stop_on_failure=True``: the first stage that is not ``ok`` ends the trace,
    and every later stage is recorded as ``not_run`` instead of being sent with a
    failed stage's text as context.
    """
    cfg = VARIANTS[variant.upper()]
    sender_brief = cfg["sender"]
    receiver_brief = cfg["receiver"]
    sender = sender_for(seed)
    receiver = receiver_for(seed)

    sender_sys = sender_system(sender_brief, prompt_version)
    receiver_sys = receiver_system(receiver_brief, prompt_version)
    calls: list[dict] = []
    turns: dict[str, list] = {}  # stage -> its own (user, assistant) turns, per trace
    options = {"max_attempts": max_stage_attempts, "max_output_bytes": max_output_bytes}
    halted: list[str] = []       # name of the first failed stage, when stop_on_failure

    def _carry(*stages) -> list:
        carried: list = []
        for name in stages:
            carried.extend(turns.get(name) or [])
        return carried

    def _remember(name: str, prompt: str, response) -> None:
        turns[name] = stage_turns(prompt, response)

    def _check(name: str, parsed) -> None:
        if stop_on_failure and not halted and parsed.status != "ok":
            halted.append(name)

    def _skipped():
        return ParsedStage(status="not_run", reason=f"stopped_after_{halted[0]}_failure")

    s1_text = s1_prompt()
    s1, s1_resp, log = _call_stage(client, sender_sys, s1_text, "plan", seed,
                                   stage_call_id(seed, "s1"),
                                   prior=_carry(*STAGE_CONTEXT["s1"]), **options)
    calls += log
    _remember("s1", s1_text, s1_resp)
    _check("s1", s1)

    if halted:
        r1 = _skipped()
    else:
        r1_text = r1_prompt()
        r1, r1_resp, log = _call_stage(client, receiver_sys, r1_text, "plan", seed,
                                       stage_call_id(seed, "r1"),
                                       prior=_carry(*STAGE_CONTEXT["r1"]), **options)
        calls += log
        _remember("r1", r1_text, r1_resp)
        _check("r1", r1)

    s2 = None
    message_text: Optional[str] = None
    message_declined = False
    message_stage_failed = False
    if condition == "FREE_MSG":
        if halted:
            s2 = _skipped()
        else:
            s2_text = s2_prompt()
            s2, s2_resp, log = _call_stage(client, sender_sys, s2_text, "message", seed,
                                           stage_call_id(seed, "s2"),
                                           prior=_carry(*STAGE_CONTEXT["s2"]), **options)
            calls += log
            _remember("s2", s2_text, s2_resp)
            _check("s2", s2)
        if s2.status == "ok":
            message_text = s2.data["message"]
            message_declined = s2.data["message_declined"]
        else:
            # Fail closed: the S3 prompt would say "Your message has been delivered",
            # which is false. Stop the trace; it is excluded (no actions recorded).
            message_stage_failed = True
    elif condition == "NEUTRAL_MSG":
        message_text = NEUTRAL_NOTICE

    if halted:
        s3 = _skipped()
        r2 = _skipped()
    elif message_stage_failed:
        s3 = ParsedStage(status="not_run", reason="message_stage_failed")
        r2 = ParsedStage(status="not_run", reason="message_stage_failed")
    else:
        s3_text = s3_prompt(condition, declined=message_declined)
        s3, s3_resp, log = _call_stage(client, sender_sys, s3_text, "action",
                                       seed, stage_call_id(seed, "s3"),
                                       prior=_carry(*STAGE_CONTEXT["s3"]), **options)
        calls += log
        _remember("s3", s3_text, s3_resp)
        _check("s3", s3)

        if halted:
            r2 = _skipped()
        else:
            r2_text = message_text
            if condition == "NO_MSG" or (condition == "FREE_MSG" and message_declined):
                r2_text = None
            r2_text_prompt = r2_prompt(condition, r2_text)
            r2, r2_resp, log = _call_stage(client, receiver_sys, r2_text_prompt, "action",
                                           seed, stage_call_id(seed, "r2"),
                                           prior=_carry(*STAGE_CONTEXT["r2"]), **options)
            calls += log
            _remember("r2", r2_text_prompt, r2_resp)
            _check("r2", r2)

    def _val(parsed, key):
        return parsed.data.get(key) if (parsed is not None and parsed.status == "ok" and parsed.data) else None

    return {
        "seed": seed,
        "variant": variant.upper(),
        "condition": condition,
        "sender": sender,
        "receiver": receiver,
        "sender_brief": sender_brief,
        "receiver_brief": receiver_brief,
        "agent_model": client.model,
        "s1": {"plan": _val(s1, "plan"), "note": _val(s1, "note"), "status": s1.status, "reason": s1.reason},
        "r1": {"plan": _val(r1, "plan"), "note": _val(r1, "note"), "status": r1.status, "reason": r1.reason},
        "s2": ({"message": _val(s2, "message"), "message_declined": message_declined, "status": s2.status,
                "reason": s2.reason}
               if condition == "FREE_MSG" else None),
        # Final-choice notes are the agent's own text from the S3/R2 reply, or None
        # when that stage produced no parseable reply. They are never invented.
        "s3": {"action": _val(s3, "action"), "raw_action": _val(s3, "raw_action"),
               "note": _val(s3, "note"), "status": s3.status, "reason": s3.reason},
        "r2": {"action": _val(r2, "action"), "raw_action": _val(r2, "raw_action"),
               "note": _val(r2, "note"), "status": r2.status, "reason": r2.reason},
        "prompt_version": prompt_version,
        "stopped_after": halted[0] if halted else None,
        "calls": calls,
    }


def build_trace(result: dict, claims: Optional[list] = None) -> Trace:
    """Assemble a Trace from run_trace results (claims come from extraction)."""
    sender = result["sender"]
    receiver = result["receiver"]
    condition = result["condition"]

    if condition == "FREE_MSG":
        s2 = result["s2"]
        if s2 is None or s2["status"] != "ok" or s2["message_declined"] or not isinstance(s2["message"], str):
            messages = []
        else:
            messages = [Message(sender=sender, recipient=receiver, text=result["s2"]["message"])]
        delivery = DeliveryCondition.FREE_MSG
    elif condition == "NEUTRAL_MSG":
        messages = [Message(sender=sender, recipient=receiver, text=NEUTRAL_NOTICE)]
        delivery = DeliveryCondition.NEUTRAL_MSG
    else:
        messages = []
        delivery = DeliveryCondition.NO_MSG

    raw_actions = {sender: result["s3"]["raw_action"], receiver: result["r2"]["raw_action"]}

    return Trace(
        trace_id=f"ext-{result['seed']:04d}",
        seed=result["seed"],
        messages=messages,
        raw_actions=raw_actions,
        delivery_condition=delivery,
        claims=list(claims or []),
        template_ids=[],
        private_plans={sender: result["s1"]["plan"], receiver: result["r1"]["plan"]},
        private_notes={
            sender: {"S1": result["s1"]["note"] or "", "S3": result["s3"].get("note") or ""},
            receiver: {"R1": result["r1"]["note"] or "", "R2": result["r2"].get("note") or ""},
        },
        variant=result["variant"],
        briefs={sender: result["sender_brief"], receiver: result["receiver_brief"]},
        agent_model=result.get("agent_model"),
        message_declined=bool(result["s2"] and result["s2"]["message_declined"]),
        agent_status=_agent_status(result),
        private_brief_intent=_private_brief_intent(result),
    )


def _private_brief_intent(result: dict) -> Optional[str]:
    """§16.2: the V2 covert brief is recorded as generator metadata only."""
    return "both agreed on B" if result["variant"] == "V2" else None


def _agent_status(result: dict) -> Optional[str]:
    statuses = [result["s1"]["status"], result["r1"]["status"], result["s3"]["status"], result["r2"]["status"]]
    if result["s2"] is not None:
        statuses.append(result["s2"]["status"])
    if any(s == "agent_refusal" for s in statuses):
        return "agent_refusal"
    return "ok" if all(s == "ok" for s in statuses) else "stage_failed"


def render_transcript(result: dict) -> str:
    """Human-readable transcript of every stage, for the demo."""
    lines = [
        f"=== seed {result['seed']} | {result['variant']} | {result['condition']} ===",
        f"sender={result['sender']} ({result['sender_brief']})  receiver={result['receiver']} ({result['receiver_brief']})",
        "",
        f"[S1 sender] {s1_prompt()!r}",
        f"    -> plan={result['s1']['plan']!r} note={result['s1']['note']!r} status={result['s1']['status']}",
        f"[R1 receiver] {r1_prompt()!r}",
        f"    -> plan={result['r1']['plan']!r} note={result['r1']['note']!r} status={result['r1']['status']}",
    ]
    if result["s2"] is not None:
        lines.append(f"[S2 sender] {s2_prompt()!r}")
        lines.append(
            f"    -> message={result['s2']['message']!r} declined={result['s2']['message_declined']} status={result['s2']['status']}"
        )
    lines.append(f"[S3 sender] {s3_prompt(result['condition'], declined=bool(result['s2'] and result['s2']['message_declined']))!r}")
    lines.append(f"    -> action={result['s3']['action']!r} status={result['s3']['status']}")
    lines.append(f"[R2 receiver] {r2_prompt(result['condition'], _r2_text(result))!r}")
    lines.append(f"    -> action={result['r2']['action']!r} status={result['r2']['status']}")
    return "\n".join(lines)


def _r2_text(result: dict) -> Optional[str]:
    if result["condition"] == "NO_MSG":
        return None
    if result["s2"] is not None and result["s2"]["message_declined"]:
        return None
    if result["s2"] is not None:
        return result["s2"]["message"]
    return NEUTRAL_NOTICE


