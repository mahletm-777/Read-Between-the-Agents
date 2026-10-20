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
