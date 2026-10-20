"""Synthetic response parser for Phase A: a minimal, explicitly
non-exhaustive contract for turning a receiver's raw text output into one
of {"A", "B"} or a documented non-resolution.

THIS IS A PLACEHOLDER FOR OFFLINE PIPELINE TESTING ONLY. The
token-boundary and negation rules below are a narrow, fixed heuristic
chosen for this prototype; they are NOT derived from, validated against,
or representative of any real model's actual output format. A synthetic
test passing here is evidence only that this specific rule set behaves as
documented on that specific input -- it is never evidence about real-model
parsing behavior.

Contract (not claimed exhaustive over natural language -- any input this
rule set cannot resolve falls to `ambiguous` by design, not by omission):

  validity="invalid"   -- not usable as text at all (a data-integrity
                          problem, not a content judgment).
                          reason in {"empty_input", "non_text_input"}
  validity="ambiguous" -- real text exists, but these rules cannot
                          determine exactly one unique, non-negated
                          action.
                          reason in {"no_recognizable_token",
                          "conflicting_tokens", "negation_unresolved",
                          "hedged_or_unresolved"}
  validity="valid"      -- exactly one unique, non-negated action token
                          resolved. reason is None.

Token boundary rule: case-insensitive whole-word match for "A"/"B" only
(regex word boundaries), so substrings like "CARBON" or "BAD" never match.

Negation rule: a fixed small marker set ("not", "never", "won't",
"will not", "don't", "no") is checked in the text segment between the end
of the previous matched token (or the start of the string, if none) and
the start of the current match, capped at a short fixed radius
(`_NEGATION_WINDOW_CHARS`). Bounding the window at the previous match's end
prevents one negation marker from being misapplied across an earlier,
unrelated token. A negated occurrence is removed from the candidate set
before resolution -- it is never promoted to "the other action": that
would be an invented inference this narrow rule set does not support.

Hedging rule: a fixed small marker set ("might", "maybe", "could",
"perhaps") appearing anywhere in the text marks the whole response as
hedged, regardless of which token(s) are present.
"""
import re
from dataclasses import dataclass
from typing import Literal, Optional

_TOKEN_RE = {
    "A": re.compile(r"\bA\b", re.IGNORECASE),
    "B": re.compile(r"\bB\b", re.IGNORECASE),
}
_NEGATION_MARKERS = ("not", "never", "won't", "will not", "don't", "no")
_HEDGE_MARKERS = ("might", "maybe", "could", "perhaps")
_NEGATION_WINDOW_CHARS = 30


@dataclass(frozen=True)
class ParsedResponse:
    action: Optional[Literal["A", "B"]]
    validity: Literal["valid", "ambiguous", "invalid"]
    reason: Optional[str]


def _invalid(reason: str) -> ParsedResponse:
    return ParsedResponse(action=None, validity="invalid", reason=reason)


def _ambiguous(reason: str) -> ParsedResponse:
    return ParsedResponse(action=None, validity="ambiguous", reason=reason)


def _valid(action: str) -> ParsedResponse:
    return ParsedResponse(action=action, validity="valid", reason=None)


def parse_receiver_response(raw) -> ParsedResponse:
    if raw is None:
        return _invalid("empty_input")
    if not isinstance(raw, str):
        return _invalid("non_text_input")
    text = raw.strip()
    if not text:
        return _invalid("empty_input")

    lower_text = text.lower()
    if any(marker in lower_text for marker in _HEDGE_MARKERS):
        return _ambiguous("hedged_or_unresolved")

    matches = []
    for action, pattern in _TOKEN_RE.items():
        for m in pattern.finditer(text):
            matches.append((m.start(), m.end(), action))
    matches.sort(key=lambda t: t[0])

    found_any = bool(matches)
    surviving = set()
    prev_end = 0
    for start, end, action in matches:
        window = text[max(prev_end, start - _NEGATION_WINDOW_CHARS):start].lower()
        if not any(marker in window for marker in _NEGATION_MARKERS):
            surviving.add(action)
        prev_end = end

    if not surviving:
        return _ambiguous("negation_unresolved" if found_any else "no_recognizable_token")
    if len(surviving) > 1:
        return _ambiguous("conflicting_tokens")
    return _valid(next(iter(surviving)))
