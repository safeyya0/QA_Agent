import pytest
from agent.planner import Planner


@pytest.fixture
def planner():
    return Planner()


# ── parse_markdown_spec ────────────────────────────────────────────────────────

class TestParseMarkdownSpec:

    def test_extracts_section_headings(self, planner):
        md = "# Login Feature\n## Overview\nSome content here."
        parsed = planner.parse_markdown_spec(md)
        titles = [s["title"] for s in parsed["sections"]]
        assert "Login Feature" in titles
        assert "Overview" in titles

    def test_extracts_requirements_r_prefix(self, planner):
        md = "- R1: Users must log in with email\n- R2: Password min 8 chars"
        parsed = planner.parse_markdown_spec(md)
        ids = [r["id"] for r in parsed["requirements"]]
        assert "R1" in ids
        assert "R2" in ids

    def test_extracts_requirements_req_prefix(self, planner):
        md = "- REQ-001: System must validate input\n- FR2: Display error messages"
        parsed = planner.parse_markdown_spec(md)
        ids = [r["id"] for r in parsed["requirements"]]
        assert "REQ-001" in ids
        assert "FR2" in ids

    def test_extracts_tc_scenario_block(self, planner):
        md = "## TC001: Valid Login\nGiven: valid credentials\nWhen: user submits form\nThen: redirected to dashboard"
        parsed = planner.parse_markdown_spec(md)
        assert len(parsed["scenarios"]) == 1
        s = parsed["scenarios"][0]
        assert s["id"] == "TC001"
        assert s["description"] == "Valid Login"
        assert s["given"] == ["valid credentials"]
        assert s["when"] == ["user submits form"]
        assert s["then"] == ["redirected to dashboard"]
        assert s["expected"] == "redirected to dashboard"

    def test_extracts_multiple_scenarios(self, planner):
        md = (
            "## TC001: Happy path\nThen: success\n"
            "## TC002: Error path\nThen: error shown\n"
        )
        parsed = planner.parse_markdown_spec(md)
        assert len(parsed["scenarios"]) == 2
        assert parsed["scenarios"][0]["id"] == "TC001"
        assert parsed["scenarios"][1]["id"] == "TC002"

    def test_extracts_explicit_steps_inside_tc(self, planner):
        md = "## TC001: Login\n- Input: email = user@test.com\n- Step: password = Secret1"
        parsed = planner.parse_markdown_spec(md)
        steps = parsed["scenarios"][0]["steps"]
        assert {"field": "email", "value": "user@test.com"} in steps

    def test_extracts_expected_from_bullet(self, planner):
        md = "## TC001: Check error\n- Expected: Error message displayed"
        parsed = planner.parse_markdown_spec(md)
        assert parsed["scenarios"][0]["expected"] == "Error message displayed"

    def test_extracts_user_story(self, planner):
        md = "As a user I want to log in to access my account."
        parsed = planner.parse_markdown_spec(md)
        assert len(parsed["user_stories"]) == 1
        assert "As a user" in parsed["user_stories"][0]

    def test_empty_spec_returns_empty_structure(self, planner):
        parsed = planner.parse_markdown_spec("")
        assert parsed["sections"] == []
        assert parsed["requirements"] == []
        assert parsed["scenarios"] == []
        assert parsed["user_stories"] == []

    def test_plain_text_no_structure_returns_empty(self, planner):
        parsed = planner.parse_markdown_spec("This is a plain paragraph with no structure.")
        assert parsed["scenarios"] == []
        assert parsed["requirements"] == []

    def test_section_collects_content(self, planner):
        md = "# Auth\n- R1: Must validate email\nSome description line."
        parsed = planner.parse_markdown_spec(md)
        section = next(s for s in parsed["sections"] if s["title"] == "Auth")
        assert any("R1" in line for line in section["content"]) or parsed["requirements"]

    def test_requirement_text_is_captured(self, planner):
        md = "- R1: Users must be able to reset their password via email"
        parsed = planner.parse_markdown_spec(md)
        assert parsed["requirements"][0]["text"] == "Users must be able to reset their password via email"


# ── build_test_cases_from_parsed_md ───────────────────────────────────────────

class TestBuildTestCasesFromParsedMd:

    def test_scenarios_become_test_cases(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "Valid login", "steps": [], "expected": "Dashboard shown",
                 "given": [], "when": [], "then": []},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert len(cases) == 1
        assert cases[0]["description"] == "Valid login"
        assert cases[0]["expected"] == "Dashboard shown"

    def test_requirements_become_verify_test_cases(self, planner):
        parsed = {
            "scenarios": [],
            "requirements": [{"id": "R1", "text": "System must log invalid attempts"}],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert len(cases) == 1
        assert "Verify:" in cases[0]["description"]
        assert cases[0]["source_req"] == "R1"

    def test_ids_are_sequential(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "A", "steps": [], "expected": "x", "given": [], "when": [], "then": []},
                {"id": "TC002", "description": "B", "steps": [], "expected": "y", "given": [], "when": [], "then": []},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert cases[0]["id"] == "TC001"
        assert cases[1]["id"] == "TC002"

    def test_steps_derived_from_given_when_when_no_explicit_steps(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "Test", "steps": [],
                 "given": ["user is on login page"], "when": ["user submits form"], "then": ["success"],
                 "expected": "success"},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        fields = [s["field"] for s in cases[0]["steps"]]
        assert "context" in fields
        assert "action" in fields

    def test_explicit_steps_preferred_over_given_when(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "Test", "steps": [{"field": "email", "value": "x@y.com"}],
                 "given": ["user on page"], "when": ["submit"], "then": [], "expected": "ok"},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert cases[0]["steps"] == [{"field": "email", "value": "x@y.com"}]

    def test_fallback_expected_when_missing(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "Test", "steps": [], "given": [], "when": [], "then": [], "expected": ""},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert cases[0]["expected"] == "Verify the expected outcome"

    def test_empty_parsed_returns_empty_list(self, planner):
        parsed = {"scenarios": [], "requirements": []}
        assert planner.build_test_cases_from_parsed_md(parsed) == []

    def test_type_is_standard(self, planner):
        parsed = {
            "scenarios": [
                {"id": "TC001", "description": "A", "steps": [], "expected": "x", "given": [], "when": [], "then": []},
            ],
            "requirements": [],
        }
        cases = planner.build_test_cases_from_parsed_md(parsed)
        assert cases[0]["type"] == "standard"


# ── _is_valid_login_test ───────────────────────────────────────────────────────

class TestIsValidLoginTest:

    def test_success_without_failure_keywords_is_valid(self, planner):
        tc = {"description": "Correct credentials", "expected": "User authenticated successfully"}
        assert planner._is_valid_login_test(tc) is True

    def test_failure_keyword_makes_it_invalid(self, planner):
        tc = {"description": "Wrong password", "expected": "Login fails with error"}
        assert planner._is_valid_login_test(tc) is False

    def test_both_keywords_means_not_valid_login(self, planner):
        tc = {"description": "Valid email", "expected": "authentication fails"}
        assert planner._is_valid_login_test(tc) is False

    def test_no_success_keyword_is_not_valid_login(self, planner):
        tc = {"description": "Empty form", "expected": "Nothing happens"}
        assert planner._is_valid_login_test(tc) is False

    def test_redirect_keyword_counts_as_success(self, planner):
        tc = {"description": "Login redirect", "expected": "User redirected to dashboard"}
        assert planner._is_valid_login_test(tc) is True

    def test_welcome_keyword_counts_as_success(self, planner):
        tc = {"description": "Login", "expected": "Welcome back message shown"}
        assert planner._is_valid_login_test(tc) is True


# ── plan_from_spec merge / dedup ───────────────────────────────────────────────

class TestPlanFromSpecMerge:

    def test_md_cases_and_llm_cases_merged_without_duplicates(self, planner, monkeypatch):
        md_cases = [
            {"id": "TC001", "description": "Valid login", "steps": [], "expected": "ok", "type": "standard"},
        ]
        llm_cases = [
            {"id": "TC001", "description": "valid login", "steps": [], "expected": "ok"},  # duplicate (case-insensitive)
            {"id": "TC002", "description": "SQL injection attempt", "steps": [], "expected": "blocked"},
        ]

        monkeypatch.setattr("agent.planner.generate_tests_from_spec", lambda *a, **kw: llm_cases)
        monkeypatch.setattr(planner, "parse_markdown_spec", lambda txt: {"scenarios": [], "requirements": []})
        monkeypatch.setattr(planner, "build_test_cases_from_parsed_md", lambda p: md_cases)

        result = planner.plan_from_spec("some spec", [], "")
        descriptions = [tc["description"] for tc in result]
        assert descriptions.count("Valid login") + descriptions.count("valid login") == 1
        assert any("SQL injection" in d for d in descriptions)

    def test_ids_resequenced_after_merge(self, planner, monkeypatch):
        md_cases = [
            {"id": "TC001", "description": "A", "steps": [], "expected": "x", "type": "standard"},
            {"id": "TC002", "description": "B", "steps": [], "expected": "y", "type": "standard"},
        ]
        monkeypatch.setattr("agent.planner.generate_tests_from_spec", lambda *a, **kw: [])
        monkeypatch.setattr(planner, "parse_markdown_spec", lambda txt: {"scenarios": [], "requirements": []})
        monkeypatch.setattr(planner, "build_test_cases_from_parsed_md", lambda p: md_cases)

        result = planner.plan_from_spec("spec", [], "")
        for i, tc in enumerate(result, 1):
            assert tc["id"] == f"TC{i:03d}"
