import pytest
from tools.llm import _clean_raw, _is_truncated, _repair_truncated_array


# ── _clean_raw ─────────────────────────────────────────────────────────────────

class TestCleanRaw:

    def test_strips_json_fence(self):
        assert _clean_raw("```json\n[1,2]\n```") == "[1,2]"

    def test_strips_uppercase_json_fence(self):
        assert _clean_raw("```JSON\n[1,2]\n```") == "[1,2]"

    def test_strips_plain_code_fence(self):
        assert _clean_raw("```\n[1,2]\n```") == "[1,2]"

    def test_strips_leading_and_trailing_whitespace(self):
        assert _clean_raw("  [1, 2]  ") == "[1, 2]"

    def test_leaves_clean_array_untouched(self):
        raw = '[{"id": "TC001"}]'
        assert _clean_raw(raw) == raw

    def test_handles_empty_string(self):
        assert _clean_raw("") == ""


# ── _is_truncated ──────────────────────────────────────────────────────────────

class TestIsTruncated:

    def test_detects_truncated_array(self):
        assert _is_truncated('[{"id": "TC001", "description": "Login') is True

    def test_complete_array_is_not_truncated(self):
        assert _is_truncated('[{"id": "TC001"}]') is False

    def test_empty_array_is_not_truncated(self):
        assert _is_truncated("[]") is False

    def test_non_array_is_not_truncated(self):
        assert _is_truncated("some plain text") is False

    def test_array_with_trailing_whitespace_not_truncated(self):
        assert _is_truncated('[{"id": "TC001"}]   ') is False

    def test_just_opening_bracket_is_truncated(self):
        assert _is_truncated("[") is True


# ── _repair_truncated_array ────────────────────────────────────────────────────

class TestRepairTruncatedArray:

    def test_repairs_missing_closing_bracket(self):
        truncated = '[{"id": "TC001", "description": "Valid login", "steps": [], "expected": "ok"}'
        result = _repair_truncated_array(truncated)
        assert result is not None
        assert isinstance(result, list)
        assert result[0]["id"] == "TC001"

    def test_repairs_two_objects_cut_after_first(self):
        truncated = '[{"id": "TC001", "steps": []}, {"id": "TC002", "steps": []'
        result = _repair_truncated_array(truncated)
        assert result is not None
        assert len(result) >= 1
        assert result[0]["id"] == "TC001"

    def test_returns_none_when_no_opening_bracket(self):
        assert _repair_truncated_array("no bracket here") is None

    def test_returns_none_for_empty_string(self):
        assert _repair_truncated_array("") is None

    def test_valid_complete_array_still_parsed(self):
        valid = '[{"id": "TC001", "steps": [], "expected": "ok"}]'
        result = _repair_truncated_array(valid)
        assert result is not None
        assert result[0]["id"] == "TC001"

    def test_handles_nested_objects_in_steps(self):
        text = '[{"id": "TC001", "steps": [{"field": "email", "value": "x@y.com"}], "expected": "ok"}'
        result = _repair_truncated_array(text)
        assert result is not None
        assert result[0]["steps"][0]["field"] == "email"

    def test_handles_escaped_quotes_in_strings(self):
        text = '[{"id": "TC001", "description": "User\\"s login", "steps": [], "expected": "ok"}'
        result = _repair_truncated_array(text)
        assert result is not None


# ── _validate_test_cases / _resolve_field_id / _snap_select_value ──────────────

from tools.llm import _validate_test_cases, _resolve_field_id, _snap_select_value

FIELDS = [
    {"id": "user-name", "name": "user-name", "label": "Username", "placeholder": "Username", "type": "text"},
    {"id": "password", "name": "password", "label": "Password", "type": "password"},
]


class TestResolveFieldId:

    def test_exact_match_returned_as_is(self):
        assert _resolve_field_id("user-name", FIELDS) == "user-name"

    def test_case_insensitive_exact_match(self):
        assert _resolve_field_id("Password", FIELDS) == "password"

    def test_fuzzy_matches_label(self):
        assert _resolve_field_id("username", FIELDS) == "user-name"

    def test_fuzzy_disabled_returns_none_for_label(self):
        assert _resolve_field_id("username", FIELDS, fuzzy=False) is None

    def test_unknown_field_returns_none(self):
        assert _resolve_field_id("zipcode", FIELDS, fuzzy=False) is None

    def test_empty_inputs(self):
        assert _resolve_field_id("", FIELDS) is None
        assert _resolve_field_id("x", []) is None


class TestSnapSelectValue:

    SELECTS = [{"id": "sort", "name": "sort", "options": ["Name (A to Z)", "Price (low to high)"]}]

    def test_exact_option_kept(self):
        assert _snap_select_value("sort", "Name (A to Z)", self.SELECTS) == "Name (A to Z)"

    def test_substring_snapped_to_full_label(self):
        assert _snap_select_value("sort", "price (low", self.SELECTS) == "Price (low to high)"

    def test_unknown_value_left_alone(self):
        assert _snap_select_value("sort", "Rating", self.SELECTS) == "Rating"


class TestValidateTestCases:

    def test_repairs_wrong_field_id(self):
        cases = [{"id": "TC001", "description": "d", "expected": "e", "steps": [
            {"action": "fill", "field": "username", "value": "u"},
        ]}]
        out = _validate_test_cases(cases, FIELDS)
        assert out[0]["steps"][0]["field"] == "user-name"

    def test_inserts_submit_before_verify_text(self):
        cases = [{"id": "TC001", "description": "d", "expected": "e", "steps": [
            {"action": "fill", "field": "user-name", "value": "u"},
            {"action": "verify_text", "value": "Products"},
        ]}]
        out = _validate_test_cases(cases, FIELDS)
        actions = [s["action"] for s in out[0]["steps"]]
        assert actions == ["fill", "submit", "verify_text"]

    def test_no_submit_inserted_when_click_present(self):
        cases = [{"id": "TC001", "description": "d", "expected": "e", "steps": [
            {"action": "fill", "field": "user-name", "value": "u"},
            {"action": "click", "text": "Save"},
            {"action": "verify_text", "value": "Successfully"},
        ]}]
        out = _validate_test_cases(cases, FIELDS)
        actions = [s["action"] for s in out[0]["steps"]]
        assert "submit" not in actions

    def test_drops_bare_number_verify_text(self):
        cases = [{"id": "TC001", "description": "d", "expected": "e", "steps": [
            {"action": "click", "text": "Add to cart"},
            {"action": "verify_text", "value": "1"},
        ]}]
        out = _validate_test_cases(cases, FIELDS)
        actions = [s["action"] for s in out[0]["steps"]]
        assert "verify_text" not in actions

    def test_truncates_long_verify_text_at_word_boundary(self):
        long_text = "The employee record was created successfully in the system"
        cases = [{"id": "TC001", "description": "d", "expected": "e", "steps": [
            {"action": "click", "text": "Save"},
            {"action": "verify_text", "value": long_text},
        ]}]
        out = _validate_test_cases(cases, FIELDS)
        vt = out[0]["steps"][1]["value"]
        assert len(vt) <= 30
        assert not vt.endswith(" ")

    def test_drops_case_without_steps(self):
        cases = [
            {"id": "TC001", "description": "d", "expected": "e", "steps": []},
            {"id": "TC002", "description": "d", "expected": "e", "steps": [{"action": "submit"}]},
        ]
        out = _validate_test_cases(cases, FIELDS)
        assert len(out) == 1
        assert out[0]["id"] == "TC002"

    def test_non_dict_entries_ignored(self):
        out = _validate_test_cases(["garbage", None], FIELDS)
        assert out == []
