import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Output directory in the project root.
OUTPUT_DIR = PROJECT_ROOT 

MESSAGES = [
    ("C01", "I propose that we both choose B this round."),
    ("C02", "I intend to choose A this round."),
    ("C03", "Maybe we should both go with A this time?"),
    ("C04", "The available choices this round are A and B."),
    ("C05", "I promise I'm choosing B this round, whatever you decide."),
    (
        "C06",
        "If we both choose B this round, we will receive a higher joint "
        "payoff than if we both choose A.",
    ),
    ("C07", "I'm not picking B, and you shouldn't either."),
    ("C08", "B seems like a reasonable option this round."),
    ("C09", "I'll probably end up choosing B."),
    ("C10", "Let's rule out B and choose A together this round."),
    ("C11", "I'll go B if you go B."),
    ("C12", "Let's do the same thing as before."),
    ("C13", "Choose whichever you like; I'm going with A."),
    ("C14", "Let's both take the other one this time."),
]

# Proposed form fields: confirm alignment with the current codebook.
FIELDNAMES = [
    "message_id",
    "message_text",
    "coordination_attempt",
    "target",
    "speech_act_primary",
    "speech_act_secondary",
    "uncertainty_or_notes",
]


def create_form(output_path: Path) -> None:
    """Create a blank annotation CSV without overwriting an existing file."""
    with output_path.open("x", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()

        for message_id, message_text in MESSAGES:
            writer.writerow({
                "message_id": message_id,
                "message_text": message_text,
                "coordination_attempt": "",
                "target": "",
                "speech_act_primary": "",
                "speech_act_secondary": "",
                "uncertainty_or_notes": "",
            })


def main() -> None:
    forms = [
        OUTPUT_DIR / "annotator_1_calibration.csv",
        OUTPUT_DIR / "annotator_2_calibration.csv",
    ]

    for path in forms:
        try:
            create_form(path)
            print(f"Created: {path}")
        except FileExistsError:
            print(f"Skipped existing file to protect your annotations: {path}")

    print("\nUse one file per annotator.")
    print("Complete annotations independently before comparing responses.")


if __name__ == "__main__":
    main()