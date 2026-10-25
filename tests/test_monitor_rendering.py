"""Rendering regression tests for monitor views (contract v1.0 §9).

Covers the M3 multi-message defect, M3/M4 evidence parity, explicit message statuses,
system-notice attribution, and the absence of label leakage into prompts.

No network, no provider calls, no file writes.
"""

from __future__ import annotations

import pytest

from apart.e_label import MessageStatus
from apart.schema import DeliveryCondition, Message, Trace
from monitors.observable import MonitorMessageSlot, observable
from monitors.prompts import PROMPT_VERSION, evidence_m3, evidence_m4, render_view

VIEWS_ALL = ("M1", "M3", "M3-ens", "M4")
NON_DELIVERED = (
    MessageStatus.EMPTY,
    MessageStatus.DECLINED,
    MessageStatus.NOT_SENT_BY_DESIGN,
    MessageStatus.SYSTEM_NOTICE,
    MessageStatus.DELIVERY_FAILED,
    MessageStatus.GENERATION_FAILED,
    MessageStatus.EXTRACTION_FAILED,
)
TWO_MESSAGES = ("First: I intend to choose B.", "Second: if you choose B, I will too.")


def trace_with(messages, condition=DeliveryCondition.FREE_MSG, declined=False, trace_id="t"):
    return Trace(trace_id=trace_id, seed=1, messages=list(messages),
                 raw_actions={"agent_1": "B", "agent_2": "B"},
                 delivery_condition=condition, message_declined=declined)


def record_with_status(status):
    return observable(trace_with([Message("agent_1", "agent_2", "placeholder")]),
                      slots=[MonitorMessageSlot("m1", status, text="notice text")])


# ------------------------------------------------------------------ M3 multi-message

def test_m3_renders_every_message_slot():
    trace = trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0]),
                        Message("agent_2", "agent_1", TWO_MESSAGES[1])])
    record = observable(trace)
    prompt = render_view(record, "M3", "case-x")
    for text in TWO_MESSAGES:
        assert text in prompt, "M3 dropped a message slot"
    assert "[1]" in prompt and "[2]" in prompt
    for view in ("M1", "M4"):
        for text in TWO_MESSAGES:
            assert text in render_view(record, view, "case-x")


def test_message_order_is_preserved():
    trace = trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0]),
                        Message("agent_2", "agent_1", TWO_MESSAGES[1])])
    prompt = render_view(observable(trace), "M3", "case-x")
    assert prompt.index(TWO_MESSAGES[0]) < prompt.index(TWO_MESSAGES[1])


# ------------------------------------------------------------------ M3 / M4 parity

def test_m4_contains_exactly_the_m3_evidence_block():
    trace = trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0]),
                        Message("agent_2", "agent_1", TWO_MESSAGES[1])])
    record = observable(trace)
    assert evidence_m4(record).startswith(evidence_m3(record) + "\n\n")
    m3 = render_view(record, "M3", "case-x")
    m4 = render_view(record, "M4", "case-x")
    assert evidence_m3(record) in m4
    assert m3 != m4                      # M4 adds the checklist and its own format block
    assert "CHECKLIST" in m4 and "CHECKLIST" not in m3


def test_m3_and_m4_show_the_same_choices_and_payoffs():
    record = observable(trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0])]))
    m3, m4 = evidence_m3(record), evidence_m4(record)
    for fragment in ("Agent 1 chose B", "Agent 2 chose B",
                     "Payoffs: Agent 1 = 10, Agent 2 = 10"):
        assert fragment in m3 and fragment in m4, fragment


# ------------------------------------------------------------------ statuses

def test_message_status_is_visible_and_explicit():
    no_msg = render_view(observable(trace_with([], DeliveryCondition.NO_MSG)), "M3", "case-x")
    notice = render_view(observable(trace_with([Message("agent_1", "agent_2", "two options")],
                                              DeliveryCondition.NEUTRAL_MSG)), "M3", "case-x")
    declined = render_view(observable(trace_with([], declined=True)), "M3", "case-x")
    assert "by design" in no_msg
    assert "System notice, not agent-authored" in notice
    assert "declined" in declined
    assert len({no_msg, notice, declined}) == 3, "distinct statuses must not collapse"


def test_all_non_delivered_statuses_render_distinctly():
    rendered = {status: render_view(record_with_status(status), "M3", "case-x")
                for status in NON_DELIVERED}
    assert len(set(rendered.values())) == len(NON_DELIVERED)
    for status, prompt in rendered.items():
        assert "[No message sent]" not in prompt, status
        assert prompt.count("[") >= 1


def test_obscuring_statuses_are_rendered_as_failures():
    for status in (MessageStatus.DELIVERY_FAILED, MessageStatus.GENERATION_FAILED,
                   MessageStatus.EXTRACTION_FAILED):
        assert "failed" in render_view(record_with_status(status), "M3", "case-x")


def test_system_notice_is_never_attributed_to_an_agent():
    record = observable(trace_with([Message("agent_1", "agent_2", "two options")],
                                   DeliveryCondition.NEUTRAL_MSG))
    prompt = render_view(record, "M1", "case-x")
    notice_line = next(line for line in prompt.splitlines() if "System notice" in line)
    assert "Agent 1" not in notice_line and "Agent 2" not in notice_line
    with pytest.raises(ValueError):
        MonitorMessageSlot("m1", MessageStatus.SYSTEM_NOTICE, text="x", sender="agent_1")


def test_delivered_slots_keep_speaker_attribution():
    record = observable(trace_with([Message("agent_2", "agent_1", "let's both pick B")]))
    assert "Agent 2 -> Agent 1" in render_view(record, "M3", "case-x")


# ------------------------------------------------------------------ boundaries and leakage

def test_m1_has_no_actions_and_m2_has_no_message_text():
    text = "SECRET MESSAGE TEXT 42"
    record = observable(trace_with([Message("agent_1", "agent_2", text)]))
    m1 = render_view(record, "M1", "case-x")
    m2 = render_view(record, "M2", "case-x")
    assert text in m1 and "chose" not in m1 and "Payoffs:" not in m1
    assert text not in m2 and "MESSAGES\n[Not shown in this record]" in m2
    assert "Agent 1 chose: B" in m2


def test_prompts_carry_no_reference_label_or_generator_fields():
    record = observable(trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0])]))
    for view in VIEWS_ALL:
        prompt = render_view(record, view, "case-x")
        for token in ("JOINT_PROPOSAL", "CONDITIONAL_COMMITMENT", "mc=", "e_state",
                      "objective_conflicting", "claims", "variant", "brief"):
            assert token not in prompt, (view, token)
        assert "case-x" in prompt


def test_monitor_slots_cannot_carry_a_label():
    slot = MonitorMessageSlot("m1", MessageStatus.DELIVERED, text="x", sender="agent_1",
                              recipient="agent_2")
    assert not hasattr(slot, "annotation")
    with pytest.raises(TypeError):
        MonitorMessageSlot("m1", MessageStatus.DELIVERED, text="x", sender="agent_1",
                           recipient="agent_2", annotation=object())


def test_rendering_is_deterministic():
    trace = trace_with([Message("agent_1", "agent_2", TWO_MESSAGES[0])])
    for view in VIEWS_ALL:
        assert render_view(observable(trace), view, "case-x") == \
            render_view(observable(trace), view, "case-x")


def test_renderer_version_is_two_zero():
    assert PROMPT_VERSION == "2.0"

