"""Planner: classifies page context and drives LLM test-case generation."""
import re
import logging
from typing import Any
from tools.llm import generate_quick_test_cases, generate_tests_from_spec
from tools.auth import detect_auth_forms

logger = logging.getLogger(__name__)

# Page context labels
PAGE_CTX_AUTH      = "auth"       # has password field
PAGE_CTX_CRUD_LIST = "crud_list"  # table + add/create button
PAGE_CTX_DATA_LIST = "data_list"  # table, no add button
PAGE_CTX_DATA_FORM = "data_form"  # form without password
PAGE_CTX_GENERAL   = "general"    # everything else


class Planner:
    """Turns observed page elements into a structured test plan."""

    # ── Page context detection ─────────────────────────────────────────────────

    def detect_page_context(self, elements: list[dict[str, Any]]) -> str:
        """Classify the page type from its element list.

        Returns one of the PAGE_CTX_* constants.
        """
        field_types = {
            e["type"] for e in elements if e.get("category") == "form_field"
        }
        has_password = "password" in field_types
        has_table    = any(e.get("category") == "data_display" for e in elements)
        has_fields   = bool(field_types)

        action_texts = [
            e["text"].lower()
            for e in elements
            if e.get("category") == "action"
        ]
        has_add_btn = any(
            any(kw in t for kw in ("add", "new", "create", "ajouter", "nouveau"))
            for t in action_texts
        )

        if has_password:
            return PAGE_CTX_AUTH
        if has_table and has_add_btn:
            return PAGE_CTX_CRUD_LIST
        if has_table:
            return PAGE_CTX_DATA_LIST
        if has_fields:
            return PAGE_CTX_DATA_FORM
        return PAGE_CTX_GENERAL

    # ── Live-page planning ─────────────────────────────────────────────────────

    def plan(
        self,
        elements: list[dict[str, Any]],
        url:      str,
        page_info: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate test cases from observed page elements.

        Always produces 3-8 test cases:
          auth       → valid login, invalid login, empty fields
          crud_list  → Create → Read → Update → Delete, plus negative tests
          data_list  → data visible, search/pagination if present
          data_form  → valid submit, missing required, boundary, invalid type
          general    → screenshot + document what's on the page
        """
        page_text    = (page_info or {}).get("page_text", "")
        page_context = self.detect_page_context(elements)
        fields       = [e for e in elements if e.get("category") == "form_field"]
        auth_info    = detect_auth_forms(fields, url, page_text)

        logger.info(
            "Planning %s — context=%s  auth_login=%s  auth_register=%s",
            url, page_context, auth_info["has_login"], auth_info["has_register"],
        )

        # ── Build rich page_context dict for the LLM ─────────────────────────
        pi = page_info or {}

        # Unique, non-empty button/link texts
        action_texts: list[str] = list(dict.fromkeys(
            e["text"] for e in elements
            if e.get("category") == "action" and e.get("text", "").strip()
        ))

        # Form field labels — give the LLM real field names, not just IDs
        labels: list[str] = list(dict.fromkeys(
            e["label"] for e in fields if e.get("label", "").strip()
        ))

        # Select/dropdown options from form fields
        selects: list[dict] = [
            {"name": e.get("name") or e.get("id", ""), "options": e.get("options", [])}
            for e in fields
            if e.get("type") == "select" and e.get("options")
        ]

        ctx: dict[str, Any] = {
            "page_type":    page_context,
            "buttons":      action_texts[:12],
            "action_links": action_texts[:15],
            "has_table":    any(e.get("category") == "data_display" for e in elements),
        }

        # Page metadata from observer
        if pi.get("title"):
            ctx["title"] = pi["title"]
        if pi.get("headings"):
            ctx["headings"] = pi["headings"][:6]
        if labels:
            ctx["labels"] = labels[:12]
        if selects:
            ctx["selects"] = selects[:4]

        # Table metadata
        tables = [e for e in elements if e.get("category") == "data_display"]
        if tables:
            ctx["row_count"] = tables[0].get("row_count", 0)
            # Column headers help the LLM write specific assertions
            if tables[0].get("headers"):
                ctx["table_headers"] = tables[0]["headers"][:8]

        # Auth hint so the LLM generates the right scenario set
        if page_context == PAGE_CTX_AUTH:
            if auth_info["has_login"]:
                ctx["auth_type"] = "login"
            elif auth_info["has_register"]:
                ctx["auth_type"] = "register"
        else:
            ctx["page_type_label"] = {
                PAGE_CTX_CRUD_LIST: "CRUD_LIST — page with data table and add/edit/delete buttons",
                PAGE_CTX_DATA_LIST: "DATA_LIST — read-only data table, possibly with search/filter",
                PAGE_CTX_DATA_FORM: "DATA_FORM — form page for creating or editing a record",
                PAGE_CTX_GENERAL:   "GENERAL — informational or dashboard page",
            }.get(page_context, "")

        test_cases = generate_quick_test_cases(fields, url, page_text[:300], ctx)
        logger.info("Generated %d test cases for %s (%s)", len(test_cases), url, page_context)

        # Prepend auth-flow meta-test for auth pages
        if page_context == PAGE_CTX_AUTH:
            test_cases = [self._auth_scenario(auth_info)] + test_cases

        return test_cases

    # ── Spec-based planning (no live URL) ──────────────────────────────────────

    def plan_from_spec(
        self,
        spec_text: str,
        fields:    list[dict[str, Any]],
        url:       str,
        page_info: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate test cases from a specification document."""
        parsed   = self.parse_markdown_spec(spec_text)
        md_cases = self.build_test_cases_from_parsed_md(parsed)
        if md_cases:
            logger.info("Extracted %d test cases from structured markdown.", len(md_cases))

        logger.info("Generating test cases from spec (%d chars) — 3-pass LLM mode...", len(spec_text))
        llm_cases = generate_tests_from_spec(spec_text, fields, url)
        logger.info("Total from LLM: %d test cases.", len(llm_cases))

        seen       = {tc["description"].lower() for tc in md_cases}
        deduped    = [tc for tc in llm_cases if tc.get("description", "").lower() not in seen]
        test_cases = md_cases + deduped

        for i, tc in enumerate(test_cases, 1):
            tc["id"] = f"TC{i:03d}"

        logger.info("Total after merge: %d test cases.", len(test_cases))

        if fields:
            page_text = (page_info or {}).get("page_text", "")
            auth_info = detect_auth_forms(fields, url, page_text)
            if auth_info["has_login"] or auth_info["has_register"]:
                test_cases = [self._auth_scenario(auth_info)] + test_cases

        return test_cases

    # ── Markdown spec parser ───────────────────────────────────────────────────

    def parse_markdown_spec(self, spec_text: str) -> dict[str, Any]:
        """Parse a markdown spec into sections, requirements, and BDD scenarios."""
        lines = spec_text.splitlines()

        result: dict[str, Any] = {
            "sections":     [],
            "requirements": [],
            "scenarios":    [],
            "user_stories": [],
        }

        _req_pat   = re.compile(r'^[-*]\s*(R\d+|REQ[-_]?\d+|FR\d+|NFR\d+)[:\s](.+)', re.IGNORECASE)
        _tc_pat    = re.compile(r'^#{1,4}\s*(TC[-_]?\d+|Test\s+Case\s*\d*)[:\s]?(.*)', re.IGNORECASE)
        _story_pat = re.compile(r'\bAs a\b.+\bI want\b', re.IGNORECASE)
        _given_pat = re.compile(r'^\*{0,2}Given\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _when_pat  = re.compile(r'^\*{0,2}When\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _then_pat  = re.compile(r'^\*{0,2}Then\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _step_pat  = re.compile(r'(?:Input|Step|Action)[:\s]+(.+?)\s*[=:]\s*(.+)', re.IGNORECASE)
        _exp_pat   = re.compile(r'Expected[:\s]+(.+)', re.IGNORECASE)

        current_section  = None
        current_scenario = None

        for line in lines:
            s = line.strip()
            if not s:
                continue

            if s.startswith("#"):
                if current_scenario:
                    result["scenarios"].append(current_scenario)
                    current_scenario = None

                tc_m = _tc_pat.match(s)
                if tc_m:
                    current_scenario = {
                        "id":          tc_m.group(1).replace(" ", "").upper(),
                        "description": tc_m.group(2).strip(),
                        "given": [], "when": [], "then": [],
                        "steps": [], "expected": "",
                    }
                else:
                    heading = re.sub(r"^#+\s*", "", s)
                    result["sections"].append({"title": heading, "content": []})
                    current_section = heading

            elif current_scenario is not None:
                given_m = _given_pat.match(s)
                when_m  = _when_pat.match(s)
                then_m  = _then_pat.match(s)
                if given_m:
                    current_scenario["given"].append(given_m.group(1).strip())
                elif when_m:
                    current_scenario["when"].append(when_m.group(1).strip())
                elif then_m:
                    val = then_m.group(1).strip()
                    current_scenario["then"].append(val)
                    if not current_scenario["expected"]:
                        current_scenario["expected"] = val
                elif s.startswith(("-", "*", "+")):
                    item   = s.lstrip("-*+ ").strip()
                    step_m = _step_pat.match(item)
                    exp_m  = _exp_pat.match(item)
                    if step_m:
                        current_scenario["steps"].append({
                            "field": step_m.group(1).strip(),
                            "value": step_m.group(2).strip(),
                        })
                    elif exp_m and not current_scenario["expected"]:
                        current_scenario["expected"] = exp_m.group(1).strip()

            else:
                req_m = _req_pat.match(s)
                if req_m:
                    result["requirements"].append({
                        "id":   req_m.group(1).upper(),
                        "text": req_m.group(2).strip(),
                    })
                if _story_pat.search(s) and len(s) < 300:
                    result["user_stories"].append(s)
                if current_section:
                    for sec in result["sections"]:
                        if sec["title"] == current_section:
                            sec["content"].append(s)
                            break

        if current_scenario:
            result["scenarios"].append(current_scenario)

        return result

    def build_test_cases_from_parsed_md(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert parsed markdown into TC-format dicts."""
        test_cases: list[dict[str, Any]] = []
        idx = 1

        for scenario in parsed.get("scenarios", []):
            steps = list(scenario.get("steps", []))
            if not steps:
                for g in scenario.get("given", []):
                    steps.append({"field": "context", "value": g})
                for w in scenario.get("when", []):
                    steps.append({"field": "action", "value": w})

            expected = scenario.get("expected", "")
            if not expected and scenario.get("then"):
                expected = scenario["then"][0]
            if not expected:
                expected = "Verify the expected outcome"

            test_cases.append({
                "id":          f"TC{idx:03d}",
                "description": scenario.get("description") or f"Spec block {scenario.get('id', idx)}",
                "steps":       steps,
                "expected":    expected,
                "type":        "standard",
            })
            idx += 1

        for req in parsed.get("requirements", []):
            test_cases.append({
                "id":          f"TC{idx:03d}",
                "description": f"Verify: {req['text'][:70]}",
                "steps":       [],
                "expected":    f"System satisfies {req['id']}: {req['text'][:60]}",
                "type":        "standard",
                "source_req":  req["id"],
            })
            idx += 1

        return test_cases

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _auth_scenario(self, auth_info: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical auth-flow meta test-case."""
        return {
            "id":          "AUTH_FLOW_001",
            "type":        "auth_flow",
            "description": "Authentication Flow Test (Register + Login + Invalid Login + Logout)",
            "auth_info":   auth_info,
            "steps":       [],
            "expected":    "User can register, login with valid credentials, and get error on bad credentials",
        }
