"""Saves and loads trial records as JSONL (one JSON object per line)."""
import dataclasses
import json
import os
from apart.models import TrialResult


def save_trials(trials: list[TrialResult], path: str, append: bool = False) -> None:
    """Writes each trial as one JSON line, creating the directory if needed."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as f:
        for trial in trials:
            f.write(json.dumps(dataclasses.asdict(trial)) + "\n")


def load_trials(path: str) -> list[dict]:
    """Reads trial records back as plain dicts, one per JSONL line."""
    trials = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                trials.append(json.loads(line))
    return trials
