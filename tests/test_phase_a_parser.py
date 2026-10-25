"""Tests for the Phase A synthetic response parser. These tests confirm
only that the documented, narrow rule set in `phase_a_parser.py` behaves
as specified on these specific inputs -- they are NOT evidence about
parsing real model output, which has not been characterized here."""
from apart.phase_a_parser import parse_receiver_response


def test_valid_single_token():
    result = parse_receiver_response("B")
    assert result.action == "B"
    assert result.validity == "valid"
    assert result.reason is None


def test_valid_token_in_sentence():
    result = parse_receiver_response("I choose A.")
    assert result.action == "A"
    assert result.validity == "valid"


def test_none_input_is_invalid_empty():
    result = parse_receiver_response(None)
    assert result.validity == "invalid"
    assert result.reason == "empty_input"


def test_whitespace_only_string_is_invalid_empty():
    result = parse_receiver_response("   ")
    assert result.validity == "invalid"
    assert result.reason == "empty_input"


def test_non_text_input_is_invalid():
    result = parse_receiver_response(123)
    assert result.validity == "invalid"
    assert result.reason == "non_text_input"


def test_no_recognizable_token_is_ambiguous():
    result = parse_receiver_response("The weather is nice today.")
    assert result.validity == "ambiguous"
    assert result.reason == "no_recognizable_token"


def test_token_boundary_rejects_substring_match():
    # "ABSOLUTELY" contains "A" and "B" as substrings but not as whole words.
    result = parse_receiver_response("ABSOLUTELY.")
    assert result.validity == "ambiguous"
    assert result.reason == "no_recognizable_token"


def test_conflicting_tokens_is_ambiguous():
    result = parse_receiver_response("A or B, either works.")
    assert result.validity == "ambiguous"
    assert result.reason == "conflicting_tokens"


def test_negated_token_with_no_alternative_is_ambiguous():
    result = parse_receiver_response("I will not choose A.")
    assert result.validity == "ambiguous"
    assert result.reason == "negation_unresolved"


def test_negated_token_with_surviving_alternative_is_valid():
    result = parse_receiver_response("Not A, I choose B.")
    assert result.action == "B"
    assert result.validity == "valid"


def test_hedged_phrasing_is_ambiguous_regardless_of_token():
    result = parse_receiver_response("I might choose B.")
    assert result.validity == "ambiguous"
    assert result.reason == "hedged_or_unresolved"


# --- Regression tests for the anchored token/negation/hedge rules.
#
# Confirmed defects (fixed 2026-10-22): the English indefinite article "a" was
# read as action A, and the negation/hedge marker sets were matched as
# substrings, so "know", "cannot" and "another" read as negations and "mighty"
# read as a hedge. The output contract is unchanged: exactly
# {valid, ambiguous, invalid} with the same reason vocabulary.


def test_english_article_is_not_an_action_token():
    for text in (
        "Choosing B is a good idea.",
        "B is a good idea.",
        "I will choose B, a solid option.",
    ):
        result = parse_receiver_response(text)
        assert result.action == "B", text
        assert result.validity == "valid", text


def test_lowercase_label_is_still_recognised_when_not_an_article():
    assert parse_receiver_response("i choose b").action == "B"
    assert parse_receiver_response("I choose a.").action == "A"


def test_uppercase_label_is_never_treated_as_an_article():
    result = parse_receiver_response("A works for me.")
    assert result.action == "A"
    assert result.validity == "valid"


def test_uppercase_sentence_initial_article_is_a_documented_limitation():
    # Known limitation of this placeholder contract: an upper-case article at the
    # start of a sentence cannot be separated from the label "A" by anchored
    # rules alone. The result fails closed (ambiguous), never to a wrong action.
    result = parse_receiver_response("A good idea is to choose B.")
    assert result.validity == "ambiguous"
    assert result.reason == "conflicting_tokens"
    assert result.action is None


def test_negation_markers_match_whole_words_only():
    for text in (
        "I know that choosing B is best.",
        "Let us consider another option, B.",
        "I cannot choose B.",
    ):
        result = parse_receiver_response(text)
        assert result.action == "B", text
        assert result.validity == "valid", text


def test_real_negation_markers_still_negate():
    for text in ("I will not choose A.", "I never choose A.", "I don't choose A.", "No, A."):
        result = parse_receiver_response(text)
        assert result.action is None, text
        assert result.validity == "ambiguous", text
        assert result.reason == "negation_unresolved", text


def test_hedge_markers_match_whole_words_only():
    result = parse_receiver_response("The algorithm is mighty, I choose B.")
    assert result.action == "B"
    assert result.validity == "valid"


def test_real_hedge_anywhere_still_marks_the_response_ambiguous():
    result = parse_receiver_response("I choose B, though I might change my mind.")
    assert result.validity == "ambiguous"
    assert result.reason == "hedged_or_unresolved"


def test_parser_is_deterministic_and_uses_the_documented_vocabulary():
    inputs = [
        "B", "I choose A.", "A or B, either works.", "I might choose B.",
        "I will not choose A.", "The weather is nice today.", "Choosing B is a good idea.",
        "", "   ", None, 123,
    ]
    first = [parse_receiver_response(text) for text in inputs]
    second = [parse_receiver_response(text) for text in inputs]
    assert first == second

    valid_reasons = {None}
    invalid_reasons = {"empty_input", "non_text_input"}
    ambiguous_reasons = {
        "no_recognizable_token",
        "conflicting_tokens",
        "negation_unresolved",
        "hedged_or_unresolved",
    }
    for parsed in first:
        assert parsed.validity in {"valid", "ambiguous", "invalid"}
        if parsed.validity == "valid":
            assert parsed.action in ("A", "B")
            assert parsed.reason in valid_reasons
        elif parsed.validity == "invalid":
            assert parsed.action is None
            assert parsed.reason in invalid_reasons
        else:
            assert parsed.action is None
            assert parsed.reason in ambiguous_reasons
