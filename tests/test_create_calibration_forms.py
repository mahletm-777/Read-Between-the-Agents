import csv

import pytest

from apart.create_calibration_forms import FIELDNAMES, MESSAGES, create_form

ACTIVE_FIELDS = [
    "message_id",
    "message_text",
    "coordination_attempt",
    "target",
    "speech_act_primary",
    "speech_act_secondary",
    "uncertainty_or_notes",
]


def test_form_header_matches_active_schema(tmp_path):
    path = tmp_path / "form.csv"
    create_form(path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert FIELDNAMES == ACTIVE_FIELDS
    assert list(rows[0].keys()) == ACTIVE_FIELDS
    for removed in ("evidence_type", "directness", "commitment"):
        assert removed not in rows[0]
    assert [r["message_id"] for r in rows] == [m[0] for m in MESSAGES]


def test_form_does_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "form.csv"
    path.write_text("existing annotations")
    with pytest.raises(FileExistsError):
        create_form(path)
    assert path.read_text() == "existing annotations"
