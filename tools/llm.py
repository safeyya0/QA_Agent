import ast
import io
import os
import re
import json
from langchain_groq import ChatGroq
from langchain.messages import SystemMessage, HumanMessage, AIMessage
from dotenv import load_dotenv

load_dotenv()


def get_llm(max_tokens: int = 2000):
    """Fast small model — used for simple/single-shot tasks."""
    return ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.1-8b-instant",
        max_tokens=max_tokens,
    )


def get_llm_spec(max_tokens: int = 2000):
    """Larger model used for multi-pass spec generation — better JSON compliance."""
    return ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
    )


# ── JSON extraction helpers ───────────────────────────────────────────────────

def _repair_truncated_array(text: str) -> list | None:
    """Recover a valid JSON array even if the LLM response was cut off mid-output."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    last_obj_end = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if ch == "}" and depth == 1:
                last_obj_end = i
    if last_obj_end == -1:
        return None
    try:
        return json.loads(text[start : last_obj_end + 1] + "]")
    except Exception:
        return None


def _repair_json_syntax(text: str) -> str:
    """Fix trailing commas before ] or } — common LLM formatting mistake."""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _normalize_literals(text: str) -> str:
    """Replace Python literals with JSON equivalents."""
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    return text


def _clean_raw(raw: str) -> str:
    """Strip markdown fences and leading/trailing noise from LLM output."""
    text = raw
    for fence in ("```json", "```JSON", "```"):
        text = text.replace(fence, "")
    return text.strip()


def _try_parse_array(chunk: str) -> list | None:
    """
    Attempt to parse a string as a JSON array using multiple repair strategies.
    Also handles dict-wrapped arrays: {"test_cases": [...]} etc.
    """
    candidates = [
        chunk,
        _repair_json_syntax(chunk),
        _normalize_literals(chunk),
        _repair_json_syntax(_normalize_literals(chunk)),
    ]
    _WRAP_KEYS = ("test_cases", "tests", "cases", "items", "results", "data")
    for c in candidates:
        try:
            result = json.loads(c)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                for key in _WRAP_KEYS:
                    if isinstance(result.get(key), list):
                        return result[key]
                # If only one key and its value is a list, use that
                for val in result.values():
                    if isinstance(val, list) and val:
                        return val
        except (json.JSONDecodeError, ValueError):
            pass
    # Last resort: ast.literal_eval handles Python-style output (single quotes etc.)
    try:
        result = ast.literal_eval(chunk)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return None


def _extract_json_array(text: str) -> list | None:
    """
    Locate and parse a JSON array from arbitrary LLM output.

    Tries progressively looser strategies so that preamble text, trailing
    explanations, single-quoted Python output, and truncated responses are
    all handled before giving up.
    """
    # Strategy 1: the whole cleaned text is already a valid array/object
    result = _try_parse_array(text.strip())
    if result is not None:
        return result

    # Strategy 2: find the first [{  (avoids false matches on "[some label]")
    m = re.search(r"\[\s*\{", text)
    if m:
        idx = m.start()
        for end_idx in range(len(text) - 1, idx, -1):
            if text[end_idx] == "]":
                result = _try_parse_array(text[idx : end_idx + 1])
                if result is not None:
                    return result
                break

    # Strategy 3: find a JSON object wrapper { "test_cases": [ ... ] }
    m2 = re.search(r"\{", text)
    if m2:
        idx2 = m2.start()
        for end_idx in range(len(text) - 1, idx2, -1):
            if text[end_idx] == "}":
                result = _try_parse_array(text[idx2 : end_idx + 1])
                if result is not None:
                    return result
                break

    # Strategy 4: any [ … ] span (fallback for unusual shapes)
    start = text.find("[")
    if start != -1:
        for end_idx in range(len(text) - 1, start, -1):
            if text[end_idx] == "]":
                result = _try_parse_array(text[start : end_idx + 1])
                if result is not None:
                    return result
                break

    # Strategy 5: recover the last complete object from a truncated array
    return _repair_truncated_array(text)


def _is_truncated(text: str) -> bool:
    """True when the output looks like a JSON array/object that was cut off."""
    stripped = text.strip()
    # Bare array cut off
    if stripped.startswith("[") and not stripped.endswith("]"):
        return True
    # Object wrapper cut off
    if stripped.startswith("{") and not stripped.endswith("}"):
        return True
    m = re.search(r"\[\s*\{", stripped)
    if m and not stripped.rstrip().endswith("]"):
        return True
    return False


def _invoke(model, messages: list) -> str:
    """Invoke the model, retrying once with a smaller token budget on 413."""
    try:
        return model.invoke(messages).content
    except Exception as e:
        if "413" in str(e) or "too large" in str(e).lower() or "tokens" in str(e).lower():
            print("[LLM] Request too large — retrying with reduced token budget...")
            smaller = get_llm(max_tokens=1000)
            return smaller.invoke(messages).content
        raise


def _parse_json_array(raw: str, model, messages: list, retries: int = 2) -> list:
    """Extract and parse a JSON array from LLM output, retrying on failure."""
    current = raw
    for attempt in range(retries + 1):
        text = _clean_raw(current)
        result = _extract_json_array(text)

        if result is not None:
            if attempt > 0:
                print(f"[LLM] Recovered {len(result)} test cases on attempt {attempt + 1}.")
            return result

        # Log a snippet so we can see what the model actually returned
        snippet = text[:200].replace("\n", " ")
        print(f"[LLM] Parse attempt {attempt + 1} failed. Response starts with: {snippet!r}")

        if attempt < retries:
            if _is_truncated(text):
                print(f"[LLM] Response truncated — retrying with shorter output request...")
                fix_msg = (
                    "Your response was cut off before the JSON was complete. "
                    "Regenerate but keep each test case VERY SHORT: "
                    "description under 50 chars, steps array max 2 items with values under 25 chars, "
                    "expected under 50 chars. "
                    'Return ONLY: {"test_cases": [{...}, ...]} — no markdown, no text outside the JSON.'
                )
            else:
                print(f"[LLM] JSON parse failed — retrying with format reminder...")
                fix_msg = (
                    "Your previous response could not be parsed as JSON. "
                    'Return ONLY a JSON object like: {"test_cases": [{"id": "TC001", '
                    '"description": "...", "steps": [{"field": "x", "value": "y"}], "expected": "..."}]}. '
                    "No markdown, no code fences, no explanation, no text before or after."
                )
            current = _invoke(model, messages + [
                AIMessage(content=current),
                HumanMessage(content=fix_msg),
            ])

    raise ValueError(f"LLM returned invalid JSON after {retries + 1} attempts.")


# ── Public LLM functions ──────────────────────────────────────────────────────

def generate_test_cases(fields: list, url: str, auth_info: dict | None = None,
                        page_text: str = "") -> list:
    model = get_llm_spec(max_tokens=1800)
    has_register = (auth_info or {}).get("has_register", False)
    has_login    = (auth_info or {}).get("has_login",    False)
    is_auth_form = has_login or has_register

    field_names = json.dumps([f.get("name") or f.get("id") or f for f in fields])
    page_hint   = f"\nPage content snippet:\n{page_text[:600]}\n" if page_text else ""
    footer      = f"URL: {url}\nFields: {field_names}{page_hint}"
    _fmt        = (
        'Return ONLY a valid JSON object — no markdown, no text outside it:\n'
        '{"test_cases":[{"id":"TC001","description":"...","steps":[{"field":"...","value":"..."}],"expected":"..."}]}'
    )

    if is_auth_form:
        valid_login = (
            "10. Valid login — use credentials visible on the page if shown, else use standard_user/secret_sauce. Expected: redirect away from login page."
            if has_login else
            "10. Whitespace-only in all fields. Expected: form stays or shows validation error."
        )
        # ── Pass 1: core auth scenarios ──────────────────────────────────────
        sys_p1 = (
            "You are a senior QA Engineer. Generate exactly 10 test cases for this login/auth form.\n\n"
            "Cover:\n"
            "1. Wrong password (valid username, wrong password)\n"
            "2. Wrong username (non-existent user)\n"
            "3. Both fields empty\n"
            "4. Only username filled, password empty\n"
            "5. Only password filled, username empty\n"
            "6. SQL injection in username: ' OR '1'='1\n"
            "7. XSS in username: <script>alert(1)</script>\n"
            "8. Very long username (100+ chars)\n"
            "9. Very long password (100+ chars)\n"
            f"{valid_login}\n\n"
            "RULES: Use EXACT field names from the list. description ≤ 45 chars, values ≤ 30 chars, "
            "expected ≤ 45 chars. Steps: 2 items per test case. "
            "If credentials are visible in page content, use them for case 10.\n\n" + _fmt
        )
        msgs_p1 = [SystemMessage(content=sys_p1), HumanMessage(content=footer + "\nGenerate 10 test cases.")]
        pass1 = _parse_json_array(_invoke(model, msgs_p1), model, msgs_p1)
        for i, tc in enumerate(pass1, 1):
            tc["id"] = f"TC{i:03d}"
        print(f"[LLM] Pass 1 (auth core): {len(pass1)} test cases.")

        # ── Pass 2: security & boundary ───────────────────────────────────────
        covered = json.dumps([tc.get("description", "")[:40] for tc in pass1])
        n2 = len(pass1) + 1
        sys_p2 = (
            "You are a senior QA Engineer. Generate exactly 8 MORE test cases for this login form — "
            "scenarios NOT already covered.\n\n"
            "Cover ONLY:\n"
            "- Username with spaces / tabs\n"
            "- Unicode characters in username (e.g. 用户名)\n"
            "- Email format as username (user@domain.com)\n"
            "- Case sensitivity (USERNAME vs username)\n"
            "- Null bytes or control characters\n"
            "- Password with special chars: !@#$%\n"
            "- Repeated failed logins (brute-force simulation)\n"
            "- Leading/trailing spaces in credentials\n\n"
            f"Do NOT repeat: {covered}\n"
            f"Start IDs at TC{n2:03d}. Steps: 2 items. description/expected ≤ 45 chars.\n\n" + _fmt
        )
        msgs_p2 = [SystemMessage(content=sys_p2), HumanMessage(content=footer + "\nGenerate 8 security/boundary test cases.")]
        try:
            pass2 = _parse_json_array(_invoke(model, msgs_p2), model, msgs_p2)
            for i, tc in enumerate(pass2):
                tc["id"] = f"TC{n2 + i:03d}"
            print(f"[LLM] Pass 2 (auth security): {len(pass2)} test cases.")
        except ValueError as e:
            print(f"[LLM] Pass 2 failed — skipping. ({e})")
            pass2 = []

    else:
        # ── Pass 1: generic form functional + security ────────────────────────
        sys_p1 = (
            "You are a senior QA Engineer. Generate exactly 10 test cases for the form fields detected.\n\n"
            "Cover:\n"
            "1. Valid typical input — normal expected value\n"
            "2. Empty submission — all fields blank\n"
            "3. Very long input (100+ chars)\n"
            "4. Special characters: !@#$%^&*()\n"
            "5. SQL injection: ' OR '1'='1\n"
            "6. XSS: <script>alert(1)</script>\n"
            "7. Numeric-only input in text fields\n"
            "8. Unicode / emoji: 😀🔥\n"
            "9. Whitespace-only input\n"
            "10. Input with newlines or carriage returns\n\n"
            "RULES: Use EXACT field names from the list — never invent 'username' or 'password'. "
            "description ≤ 45 chars, values ≤ 30 chars, expected ≤ 45 chars. Steps: 1 item per case.\n\n" + _fmt
        )
        msgs_p1 = [SystemMessage(content=sys_p1), HumanMessage(content=footer + "\nGenerate 10 test cases.")]
        pass1 = _parse_json_array(_invoke(model, msgs_p1), model, msgs_p1)
        for i, tc in enumerate(pass1, 1):
            tc["id"] = f"TC{i:03d}"
        print(f"[LLM] Pass 1 (generic form): {len(pass1)} test cases.")

        # ── Pass 2: boundary & validation ─────────────────────────────────────
        covered = json.dumps([tc.get("description", "")[:40] for tc in pass1])
        n2 = len(pass1) + 1
        sys_p2 = (
            "You are a senior QA Engineer. Generate exactly 8 MORE test cases for this form — "
            "edge cases NOT already covered.\n\n"
            "Cover ONLY:\n"
            "- Maximum allowed length exactly\n"
            "- One character below minimum length\n"
            "- HTML tags without script: <b>bold</b>\n"
            "- URL as input value\n"
            "- Email format input\n"
            "- Negative numbers\n"
            "- Copy-paste with hidden characters\n"
            "- All caps input\n\n"
            f"Do NOT repeat: {covered}\n"
            f"Start IDs at TC{n2:03d}. Steps: 1 item. description/expected ≤ 45 chars.\n\n" + _fmt
        )
        msgs_p2 = [SystemMessage(content=sys_p2), HumanMessage(content=footer + "\nGenerate 8 edge case test cases.")]
        try:
            pass2 = _parse_json_array(_invoke(model, msgs_p2), model, msgs_p2)
            for i, tc in enumerate(pass2):
                tc["id"] = f"TC{n2 + i:03d}"
            print(f"[LLM] Pass 2 (generic edge): {len(pass2)} test cases.")
        except ValueError as e:
            print(f"[LLM] Pass 2 failed — skipping. ({e})")
            pass2 = []

    total = pass1 + pass2
    print(f"[LLM] Total generated: {len(total)} test cases.")
    return total


def generate_tests_from_spec(spec_text: str, fields: list, url: str) -> list:
    # llama3-70b-8192 has 8192 token context — keep inputs lean
    model = get_llm_spec(max_tokens=1800)

    fields_section = (
        f"Detected form fields on {url}:\n{json.dumps(fields, indent=2)}"
        if fields else "No live page fields detected — generate tests based on the spec alone."
    )
    url_section = f"Target URL: {url}" if url else "No URL provided — generate a test plan only."
    # Keep spec snippet short enough to stay comfortably within 8192 tokens
    spec_body = spec_text[:2500]
    common_footer = f"\n{url_section}\n\n{fields_section}\n\nSpecification excerpt:\n---\n{spec_body}\n---\n"

    _json_fmt = (
        'Return ONLY a valid JSON object in this exact format — no markdown, no text outside the JSON:\n'
        '{"test_cases": [{"id": "TC001", "description": "...", '
        '"steps": [{"field": "fieldName", "value": "testValue"}], "expected": "..."}, ...]}'
    )

    # Pass 1: happy paths + business rules
    sys_p1 = (
        "You are a senior QA Engineer. Generate functional test cases from the specification.\n\n"
        "Rules:\n"
        "- For EVERY requirement or user story: one success path AND one failure path.\n"
        "- For EVERY validation rule: one dedicated test case.\n"
        "- For EVERY form field: valid input AND one invalid input.\n"
        "- Target 8-12 test cases. Keep steps arrays to 2 items max.\n"
        "- Steps must be objects: {\"field\": \"fieldName\", \"value\": \"testValue\"}.\n\n"
        + _json_fmt
    )
    msgs_p1 = [SystemMessage(content=sys_p1), HumanMessage(content=common_footer + "\nGenerate test cases now.")]
    pass1 = _parse_json_array(_invoke(model, msgs_p1), model, msgs_p1)
    print(f"[LLM] Pass 1: {len(pass1)} test cases.")

    # Pass 2: boundaries, edge cases, security
    covered = json.dumps([tc.get("description", "")[:40] for tc in pass1])
    n2 = len(pass1) + 1
    sys_p2 = (
        "You are a senior QA Engineer doing a gap-analysis pass.\n\n"
        "Generate ONLY these types of test cases (gaps not yet covered):\n"
        "- Empty/blank fields\n"
        "- Max-length strings\n"
        "- XSS: <script>alert(1)</script>\n"
        "- SQL injection: ' OR '1'='1\n"
        "- Multiple invalid fields simultaneously\n\n"
        f"Generate exactly 5 test cases. Start IDs at TC{n2:03d}.\n"
        f"Do NOT repeat: {covered}\n"
        "Steps must be objects: {\"field\": \"fieldName\", \"value\": \"testValue\"}.\n\n"
        + _json_fmt
    )
    msgs_p2 = [SystemMessage(content=sys_p2), HumanMessage(content=common_footer + "\nGenerate gap test cases now.")]
    try:
        pass2 = _parse_json_array(_invoke(model, msgs_p2), model, msgs_p2)
        for i, tc in enumerate(pass2):
            tc["id"] = f"TC{n2 + i:03d}"
        print(f"[LLM] Pass 2: {len(pass2)} additional test cases.")
    except ValueError as e:
        print(f"[LLM] Pass 2 failed — skipping. ({e})")
        pass2 = []

    # Pass 3: end-to-end workflows & integration
    all_covered = json.dumps([tc.get("description", "")[:40] for tc in pass1 + pass2])
    n3 = n2 + len(pass2)
    sys_p3 = (
        "You are a senior QA Engineer doing a final workflow pass.\n\n"
        "Generate ONLY multi-step end-to-end scenarios not yet covered:\n"
        "- Complete user journeys from the spec\n"
        "- Role-based access if roles are mentioned\n"
        "- Out-of-order or skipped steps\n\n"
        f"Generate exactly 5 test cases. Start IDs at TC{n3:03d}.\n"
        f"Do NOT repeat: {all_covered}\n"
        "Steps must be objects: {\"field\": \"fieldName\", \"value\": \"testValue\"}.\n\n"
        + _json_fmt
    )
    msgs_p3 = [SystemMessage(content=sys_p3), HumanMessage(content=common_footer + "\nGenerate workflow test cases now.")]
    try:
        pass3 = _parse_json_array(_invoke(model, msgs_p3), model, msgs_p3)
        for i, tc in enumerate(pass3):
            tc["id"] = f"TC{n3 + i:03d}"
        print(f"[LLM] Pass 3: {len(pass3)} workflow test cases.")
    except ValueError as e:
        print(f"[LLM] Pass 3 failed — skipping. ({e})")
        pass3 = []

    total = pass1 + pass2 + pass3
    print(f"[LLM] Total from spec: {len(total)} test cases across 3 passes.")
    return total


def extract_spec_text(file_content: bytes, filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return file_content.decode("utf-8", errors="replace")


def generate_conclusion_with_llm(report_summary: dict) -> str | None:
    """Generate a French conclusion using llama3-70b-8192. Returns None on failure."""
    try:
        model = ChatGroq(
            api_key=os.getenv("GROQ_API_KEY"),
            model="llama-3.3-70b-versatile",
            max_tokens=600,
        )
        total   = report_summary.get("total_tests", 0)
        passed  = report_summary.get("passed", 0)
        failed  = report_summary.get("failed", 0)
        partial = report_summary.get("partial", 0)
        rate    = f"{int(passed / total * 100)} %" if total else "N/A"
        url     = report_summary.get("url", "l'application testée")

        prompt = (
            f"Tu es un expert QA senior. Rédige une conclusion professionnelle en français (4 à 6 phrases) "
            f"pour un rapport de test fonctionnel portant sur {url}.\n\n"
            f"Résultats : {total} tests exécutés — {passed} réussis, {failed} échoués, "
            f"{partial} partiels. Taux de réussite : {rate}.\n\n"
            "La conclusion doit : résumer objectivement les résultats, évaluer la qualité globale, "
            "indiquer si la mise en production est recommandée ou non, et proposer les prochaines "
            "étapes prioritaires. Retourne UNIQUEMENT le paragraphe, sans titre ni introduction."
        )
        result = model.invoke([HumanMessage(content=prompt)])
        return result.content.strip()
    except Exception as e:
        print(f"[LLM] Conclusion generation failed (llama3-70b): {e}")
        return None
