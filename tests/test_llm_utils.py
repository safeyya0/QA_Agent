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
