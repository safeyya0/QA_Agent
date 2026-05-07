"""Planner: classifies page context and drives LLM test-case generation."""
import re
import logging
from typing import Any
from tools.llm import generate_quick_test_cases, generate_tests_from_spec, convert_spec_scenarios_to_steps
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

        # Icon-only buttons (cart, hamburger, etc.) — label derived by observer
        icon_labels: list[str] = list(dict.fromkeys(
            e["text"] for e in elements
            if e.get("category") == "action" and e.get("is_icon") and e.get("text", "").strip()
        ))

        # Text-based buttons (exclude icons — already captured above)
        text_action_texts: list[str] = list(dict.fromkeys(
            e["text"] for e in elements
            if e.get("category") == "action" and not e.get("is_icon") and e.get("text", "").strip()
        ))

        ctx: dict[str, Any] = {
            "page_type":    page_context,
            "buttons":      text_action_texts[:12],
            "action_links": action_texts[:15],
            "has_table":    any(e.get("category") == "data_display" for e in elements),
        }

        if icon_labels:
            ctx["icons"] = icon_labels[:12]

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
        sections:  list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the test scenarios found in the spec file against the live app.

        Primary path: parse the spec's own scenarios/requirements and convert each
        one to executable Playwright steps.  The agent runs EXACTLY the tests that
        are written in the file — nothing is invented.

        Fallback (spec has no structured content): generate scenarios from the raw
        spec text via the 4-pass LLM mode.

        Args:
            sections: nav sections discovered by Phase 2 (text + href pairs) so
                      the LLM can write correct navigate steps with real hrefs.
        """
        parsed = self.parse_markdown_spec(spec_text)

        has_structured = bool(
            parsed.get("scenarios")
            or parsed.get("requirements")
            or parsed.get("user_stories")
            or any(sec.get("content") for sec in parsed.get("sections", []))
        )

        if has_structured:
            # ── PRIMARY: convert spec's own scenarios to executable steps ─────
            logger.info(
                "Spec has structured content (%d scenarios, %d requirements, %d stories) "
                "— converting to executable test cases.",
                len(parsed.get("scenarios", [])),
                len(parsed.get("requirements", [])),
                len(parsed.get("user_stories", [])),
            )
            test_cases = convert_spec_scenarios_to_steps(parsed, fields, url, sections=sections)
            logger.info("Converted %d spec scenarios to executable test cases.", len(test_cases))
        else:
            # ── FALLBACK: spec is plain text — generate scenarios from content ─
            logger.info(
                "Spec has no structured scenarios — falling back to 4-pass generation (%d chars).",
                len(spec_text),
            )
            test_cases = generate_tests_from_spec(spec_text, fields, url, sections=sections)
            logger.info("Generated %d test cases from spec text.", len(test_cases))

        # Preserve the original IDs from the spec file (TC-001, TC-002…) when available.
        # Only renumber if the test case has no ID or a generic placeholder.
        for i, tc in enumerate(test_cases, 1):
            if not tc.get("id") or tc["id"].startswith("TC0"):
                tc["id"] = f"TC{i:03d}"

        logger.info("Total spec test cases: %d.", len(test_cases))
        return test_cases

    # ── Markdown spec parser ───────────────────────────────────────────────────

    def parse_markdown_spec(self, spec_text: str) -> dict[str, Any]:
        """Parse a markdown spec into sections, requirements, and BDD scenarios.

        Handles multiple formats:
        - BDD: Given / When / Then blocks
        - Table steps: | N° | Action | Résultat attendu | rows inside TC sections
        - Requirements: - R1: / - REQ-1: bullet items
        - Test data hints: **Données de test suggérées:** `key: val, ...`
        - Sub-headings (#### Préconditions, #### Étapes du test) are kept inside
          their parent TC scenario instead of terminating it.
        """
        lines = spec_text.splitlines()

        result: dict[str, Any] = {
            "sections":     [],
            "requirements": [],
            "scenarios":    [],
            "user_stories": [],
        }

        _req_pat      = re.compile(r'^[-*]\s*(R\d+|REQ[-_]?\d+|FR\d+|NFR\d+)[:\s](.+)', re.IGNORECASE)
        _tc_pat       = re.compile(r'^#{1,4}\s*(TC[-_]?\d+|Test\s+Case\s*\d*)[:\s\-]*(.*)', re.IGNORECASE)
        _story_pat    = re.compile(r'\bAs a\b.+\bI want\b', re.IGNORECASE)
        _given_pat    = re.compile(r'^\*{0,2}Given\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _when_pat     = re.compile(r'^\*{0,2}When\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _then_pat     = re.compile(r'^\*{0,2}Then\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _step_pat     = re.compile(r'(?:Input|Step|Action)[:\s]+(.+?)\s*[=:]\s*(.+)', re.IGNORECASE)
        _exp_pat      = re.compile(r'Expected[:\s]+(.+)', re.IGNORECASE)
        _global_exp   = re.compile(r'\*\*R[ée]sultat\s+global[^*]*\*\*[:\s]*(.+)', re.IGNORECASE)
        _testdata_pat = re.compile(r'Donn[ée]es de test[^`]*`([^`]+)`', re.IGNORECASE)
        _bold_exp     = re.compile(r'\*\*([^*]+)\*\*[:\s]+(.+)')

        current_section  = None
        current_scenario = None

        for line in lines:
            s = line.strip()
            if not s:
                continue

            # ── Heading line ─────────────────────────────────────────────────
            if s.startswith("#"):
                tc_m = _tc_pat.match(s)
                if tc_m:
                    # New TC heading — save previous scenario first
                    if current_scenario:
                        result["scenarios"].append(current_scenario)
                    desc = tc_m.group(2).strip().lstrip("-– ").strip()
                    current_scenario = {
                        "id":          tc_m.group(1).replace(" ", "").upper(),
                        "description": desc,
                        "given": [], "when": [], "then": [],
                        "steps": [], "expected": "", "test_data": "",
                    }
                    current_section = None
                elif current_scenario is not None:
                    # Sub-heading inside a TC (e.g. #### Étapes du test) —
                    # do NOT close the scenario; just ignore the heading line
                    pass
                else:
                    # Regular section heading outside any TC
                    if current_scenario:
                        result["scenarios"].append(current_scenario)
                        current_scenario = None
                    heading = re.sub(r"^#+\s*", "", s)
                    result["sections"].append({"title": heading, "content": []})
                    current_section = heading

            # ── Content inside a TC scenario ─────────────────────────────────
            elif current_scenario is not None:

                # Markdown table row: | N° | Action | Résultat attendu |
                if s.startswith("|"):
                    cells = [c.strip() for c in s.split("|")]
                    cells = [c for c in cells if c]  # drop empty edge tokens
                    # Skip separator rows (|---|---|) and header rows
                    is_sep    = all(re.match(r'^[-:]+$', c) for c in cells)
                    is_header = cells and any(
                        cells[0].lower() in ("n°", "n", "#", "no", "id", "étape")
                        or "action" in cells[0].lower()
                        for _ in [None]
                    )
                    if not is_sep and not is_header and cells:
                        # Data row — first cell should be a step number
                        if re.match(r'^\d+$', cells[0]):
                            action_text   = cells[1] if len(cells) > 1 else ""
                            expected_text = cells[2] if len(cells) > 2 else ""
                            if action_text:
                                current_scenario["steps"].append({
                                    "field": "action",
                                    "value": action_text,
                                })
                                if expected_text and not current_scenario["expected"]:
                                    current_scenario["expected"] = expected_text

                # BDD keywords
                elif _given_pat.match(s):
                    current_scenario["given"].append(_given_pat.match(s).group(1).strip())
                elif _when_pat.match(s):
                    current_scenario["when"].append(_when_pat.match(s).group(1).strip())
                elif _then_pat.match(s):
                    val = _then_pat.match(s).group(1).strip()
                    current_scenario["then"].append(val)
                    if not current_scenario["expected"]:
                        current_scenario["expected"] = val

                # Global expected result: **Résultat global attendu:** ...
                # Always overrides the partial expected captured from table rows
                elif _global_exp.search(s):
                    m = _global_exp.search(s)
                    current_scenario["expected"] = m.group(1).strip().strip("*").strip()

                # Test data hint: **Données de test suggérées:** `Username: Admin, ...`
                elif _testdata_pat.search(s):
                    m = _testdata_pat.search(s)
                    if not current_scenario["test_data"]:
                        current_scenario["test_data"] = m.group(1).strip()

                # Bullet step items
                elif s.startswith(("-", "*", "+")):
                    item  = s.lstrip("-*+ ").strip()
                    sm    = _step_pat.match(item)
                    em    = _exp_pat.match(item)
                    if sm:
                        current_scenario["steps"].append({
                            "field": sm.group(1).strip(),
                            "value": sm.group(2).strip(),
                        })
                    elif em and not current_scenario["expected"]:
                        current_scenario["expected"] = em.group(1).strip()

            # ── Content outside any TC ────────────────────────────────────────
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
