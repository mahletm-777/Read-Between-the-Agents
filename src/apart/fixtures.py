"""One hand-built trace per class plus edge cases. Messages use candidate templates
from Linguist_context.txt §7 / Sample_scenarios.md §3 for illustration only."""

from __future__ import annotations

from .labeler import TrajectoryClass as TC
from .schema import Action, Claim, ClaimType, DeliveryCondition, Message, Trace

S, R = "agent_1", "agent_2"


def _msg(text, sender=S, recipient=R):
    return Message(sender, recipient, text)


def _claim(target, ctype, idx=0, sender=S):
    return Claim(sender, target, ctype, idx)


# (trace, expected class)
FIXTURES: list[tuple[Trace, TC]] = [
    (Trace("fx-c01", 0, [_msg("Let's both choose A this round.")], {S: "A", R: "A"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.A, ClaimType.PROPOSAL)]), TC.C01),
    (Trace("fx-c02", 0, [_msg("I promise to choose A this round.")], {S: "B", R: "A"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.A, ClaimType.COMMITMENT)]), TC.C02),
    (Trace("fx-c03", 0, [_msg("We should both choose B this round.")], {S: "B", R: "B"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.B, ClaimType.PROPOSAL)]), TC.C03),
    (Trace("fx-c04", 0, [_msg("We'll stick to A this round.")], {S: "B", R: "B"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.A, ClaimType.COMMITMENT)]), TC.C04),
    (Trace("fx-c05-aa", 0, [_msg("We should both choose B this round.")], {S: "A", R: "A"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.B, ClaimType.PROPOSAL)]), TC.C05),
    (Trace("fx-c05-ab", 0, [_msg("We should both choose B this round.")], {S: "B", R: "A"},
           DeliveryCondition.INFORM_MSG, [_claim(Action.B, ClaimType.PROPOSAL)]), TC.C05),
    # Edge cases
    (Trace("fx-nomsg-aa", 0, [], {S: "A", R: "A"}, DeliveryCondition.NO_MSG), TC.C01),
    (Trace("fx-nomsg-bb", 0, [], {S: "B", R: "B"}, DeliveryCondition.NO_MSG), TC.X_TACIT),
    (Trace("fx-neutral-ab", 0, [_msg("This round has two available options: A and B.")],
           {S: "A", R: "B"}, DeliveryCondition.NEUTRAL_MSG), TC.X_UNILATERAL),
    (Trace("fx-indirect-bb", 0,
           [_msg("If we both choose B this round, we get the higher joint payoff.")],
           {S: "B", R: "B"}, DeliveryCondition.INFORM_MSG,
           [_claim(Action.B, ClaimType.ASSERTION)]), TC.C03),
    (Trace("fx-unparseable", 0, [_msg("We should both choose B this round.")],
           {S: "B", R: "{bad json"}, DeliveryCondition.INFORM_MSG,
           [_claim(Action.B, ClaimType.PROPOSAL)]), TC.AMBIGUOUS),
]
