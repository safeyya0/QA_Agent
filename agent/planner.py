import re
from tools.llm import generate_test_cases, generate_tests_from_spec
from tools.auth import detect_auth_forms


class Planner:
    def __init__(self):
        pass

    def plan(self, fields: list, url: str, page_info: dict | None = None) -> list:
        page_text = (page_info or {}).get("page_text", "")
        auth_info = detect_auth_forms(fields, url, page_text)

        print(f"[THINK] Auth detection — has_login={auth_info['has_login']}, has_register={auth_info['has_register']}")
        print(f"[THINK] Generating test cases using LLM for {len(fields)} fields...")
        test_cases = generate_test_cases(fields, url, auth_info, page_text)
        print(f"[THINK] Generated {len(test_cases)} LLM test cases.")

        if auth_info["has_login"] or auth_info["has_register"]:
            test_cases = [self._auth_scenario(auth_info)] + test_cases

        return test_cases

    def plan_from_spec(self, spec_text: str, fields: list, url: str, page_info: dict | None = None) -> list:
        # Pre-pass: extract any explicitly structured TC blocks / requirements from markdown
        parsed = self.parse_markdown_spec(spec_text)
        md_cases = self.build_test_cases_from_parsed_md(parsed)
        if md_cases:
            print(f"[THINK] Extracted {len(md_cases)} test cases from structured markdown.")

        print(f"[THINK] Generating test cases from spec ({len(spec_text)} chars) — 3-pass LLM mode...")
        llm_cases = generate_tests_from_spec(spec_text, fields, url)
        print(f"[THINK] Total from LLM: {len(llm_cases)} test cases.")

        # Merge: structured-MD cases first (they have explicit requirements behind them),
        # then LLM cases that cover scenarios not already expressed structurally.
        seen = {tc["description"].lower() for tc in md_cases}
        deduped_llm = [tc for tc in llm_cases if tc.get("description", "").lower() not in seen]
        test_cases = md_cases + deduped_llm

        # Re-sequence IDs so there are no gaps
        for i, tc in enumerate(test_cases, 1):
            tc["id"] = f"TC{i:03d}"

        print(f"[THINK] Total after merge: {len(test_cases)} test cases.")

        if fields:
            page_text = (page_info or {}).get("page_text", "")
            auth_info = detect_auth_forms(fields, url, page_text)
            if auth_info["has_login"] or auth_info["has_register"]:
                test_cases = [self._auth_scenario(auth_info)] + test_cases

        return test_cases

    # ── Markdown spec parsing ──────────────────────────────────────────────────

    def parse_markdown_spec(self, spec_text: str) -> dict:
        """Parse a markdown spec into structured sections, requirements, and BDD scenarios."""
        lines = spec_text.splitlines()

        result: dict = {
            "sections": [],
            "requirements": [],
            "scenarios": [],
            "user_stories": [],
        }

        _req_pat    = re.compile(r'^[-*]\s*(R\d+|REQ[-_]?\d+|FR\d+|NFR\d+)[:\s](.+)', re.IGNORECASE)
        _tc_pat     = re.compile(r'^#{1,4}\s*(TC[-_]?\d+|Test\s+Case\s*\d*)[:\s]?(.*)', re.IGNORECASE)
        _story_pat  = re.compile(r'\bAs a\b.+\bI want\b', re.IGNORECASE)
        _given_pat  = re.compile(r'^\*{0,2}Given\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _when_pat   = re.compile(r'^\*{0,2}When\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _then_pat   = re.compile(r'^\*{0,2}Then\*{0,2}[:\s]+(.+)', re.IGNORECASE)
        _step_pat   = re.compile(r'(?:Input|Step|Action)[:\s]+(.+?)\s*[=:]\s*(.+)', re.IGNORECASE)
        _exp_pat    = re.compile(r'Expected[:\s]+(.+)', re.IGNORECASE)

        current_section  = None
        current_scenario = None

        for line in lines:
            s = line.strip()
            if not s:
                continue

            if s.startswith('#'):
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
                    heading = re.sub(r'^#+\s*', '', s)
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
                elif s.startswith(('-', '*', '+')):
                    item = s.lstrip('-*+ ').strip()
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

    def build_test_cases_from_parsed_md(self, parsed: dict) -> list:
        """Convert parsed markdown structure into TC-format dicts."""
        test_cases = []
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
            desc = f"Verify: {req['text'][:70]}"
            test_cases.append({
                "id":          f"TC{idx:03d}",
                "description": desc,
                "steps":       [],
                "expected":    f"System satisfies {req['id']}: {req['text'][:60]}",
                "type":        "standard",
                "source_req":  req["id"],
            })
            idx += 1

        return test_cases

    def _is_valid_login_test(self, tc: dict) -> bool:
        text = ((tc.get("expected") or "") + " " + (tc.get("description") or "")).lower()
        has_success = bool(re.search(
            r'\b(success\w*|correct\w*|valid\b|dashboard|home|redirect\w*|log\w*\s+in|authenticat\w*|grant\w*|welcom\w*)\b',
            text
        ))
        has_failure = bool(re.search(
            r'\b(fail\w*|error\w*|invalid\w*|incorrect\w*|wrong\w*|empty|reject\w*|deny|denied|block\w*)\b',
            text
        ))
        return has_success and not has_failure

    def _auth_scenario(self, auth_info: dict) -> dict:
        return {
            "id": "AUTH_FLOW_001",
            "type": "auth_flow",
            "description": "Authentication Flow Test (Register + Login + Invalid Login + Logout)",
            "auth_info": auth_info,
            "steps": [],
            "expected": "User can register, login with valid credentials, and receive error on invalid credentials"
        }
