"""Phase A live pilot: plan, trial outcomes, adapter mapping, and annotation -> E."""

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agents.client import FakeAgentClient
from apart.e_annotate import label_trace_from_guide
from apart.phase_a_live import (
    C0, C1, C2, C3, LIVE_CONDITIONS, NEUTRAL_NOTICE, TEMPLATES, build_plan, message_for,
    receiver_prompt, run_trial,
)
from apart.phase_a_to_trace import convert_phase_a_result
from apart.schema import DeliveryCondition, trace_from_dict


def test_plan_is_deterministic_balanced_and_paired():
    plan = build_plan()
    assert plan == build_plan()
    actions = [s["sender_action"] for s in plan["scenarios"]]
    assert len(actions) == 20 and actions.count("A") == actions.count("B") == 10
    assert sorted(map(tuple, plan["order"])) == sorted(
        (s["scenario_id"], c) for s in plan["scenarios"] for c in LIVE_CONDITIONS)


def test_messages_per_condition():
    scenario = {"scenario_id": "S01", "sender_action": "A", "template_index": 3}
    assert message_for(scenario, C0) is None
    assert message_for(scenario, C1) == NEUTRAL_NOTICE
    assert message_for(scenario, C2) == TEMPLATES[C2][3]
    assert message_for(scenario, C3) == TEMPLATES[C3][3]
    assert "No message" in receiver_prompt(None)


def test_receiver_never_sees_sender_action():
    scenario = {"scenario_id": "S01", "sender_action": "B", "template_index": 0}
    client = FakeAgentClient(responder=lambda *_: '{"action": "A", "note": ""}')
    run_trial(scenario, C2, client, "t")
    sent = json.dumps(client.calls[0]["messages"])
    assert "sender_action" not in sent and "Let's both choose A" in sent


@pytest.mark.parametrize("replies,validity,action,calls", [
    (['{"action": "B", "note": ""}'], "valid", "B", 1),
    (["not json", '{"action": "A", "note": ""}'], "valid", "A", 2),
    (["not json", "still not json"], "invalid", None, 2),
    (['{"action": "C", "note": ""}'], "ambiguous", None, 1),
])
def test_trial_outcomes_and_single_retry(replies, validity, action, calls):
    scenario = {"scenario_id": "S02", "sender_action": "A", "template_index": 1}
    client = FakeAgentClient(responder=lambda m, t, s, i: replies[i])
    result, log = run_trial(scenario, C3, client, "t")
    assert (result.validity, result.parsed_action, len(client.calls)) == (validity, action, calls)
    status = convert_phase_a_result(result).status
    assert (status == "valid") == (validity == "valid")


def test_adapter_maps_live_conditions():
    scenario = {"scenario_id": "S03", "sender_action": "B", "template_index": 2}
    client = FakeAgentClient(responder=lambda *_: '{"action": "B", "note": ""}')
    expected = {C0: DeliveryCondition.NO_MSG, C1: DeliveryCondition.NEUTRAL_MSG,
                C2: DeliveryCondition.FREE_MSG, C3: DeliveryCondition.FREE_MSG}
    for cond, delivery in expected.items():
        converted = convert_phase_a_result(run_trial(scenario, cond, client, "t")[0])
        assert converted.status == "valid"
        assert converted.trace.delivery_condition is delivery
        assert converted.trace.scenario_id == "S03"


def test_dry_run_outputs_feed_annotation_to_e(tmp_path):
    import run_phase_a_live

    out = tmp_path / "run"
    assert run_phase_a_live.main(["--out-dir", str(out), "--dry-run"]) == 0
    with (out / "annotation_sheet.csv").open(encoding="utf-8-sig") as handle:
        sheet = list(csv.DictReader(handle))
    assert len(sheet) == 10  # 5 C2 + 5 C3 templates; the neutral notice is not annotated
    assert all(row["coordination_attempt"] == "" for row in sheet)
    assert set(sheet[0]) == {"message_id", "message_text", "coordination_attempt", "target",
                             "speech_act_primary", "speech_act_secondary", "uncertainty_or_notes"}
    # Simulated annotations: every template is a joint proposal toward its letter.
    labels = {}
    for row in sheet:
        target = "B" if "B" in row["message_text"] else "A"
        labels[row["message_id"]] = {"coordination_attempt": "YES", "target": target,
                                     "speech_act_primary": "PROPOSAL",
                                     "speech_act_secondary": "", "uncertainty_or_notes": ""}
    with (out / "annotation_key.csv").open(encoding="utf-8") as handle:
        key = list(csv.DictReader(handle))
    traces = {}
    for cond in LIVE_CONDITIONS:
        for line in (out / f"traces_{cond}.jsonl").read_text().splitlines():
            trace = trace_from_dict(json.loads(line))
            traces[trace.trace_id] = (cond, trace)
    seen = set()
    for row in key:
        cond, trace = traces[row["trace_id"]]
        record = dict(labels[row["message_id"]], message_id=row["slot_id"])
        e = label_trace_from_guide(trace, [record])
        assert e.e is (cond == C3)
        seen.add(row["trace_id"])
    assert len(seen) == 40
