import ast
import io
import os
import re
import json
import time
import logging
from langchain_groq import ChatGroq
try:
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
except ImportError:  # older installs expose them via the langchain meta-package
    from langchain.messages import SystemMessage, HumanMessage, AIMessage
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def get_llm(max_tokens: int = 4096):
    """Fast small model — used for simple/single-shot tasks."""
    return ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.1-8b-instant",
        max_tokens=max_tokens,
    )


def get_llm_spec(max_tokens: int = 2000):
    """Larger model used for spec generation — kept at 2000 to stay within Groq 6k TPM."""
    return ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model="llama-3.3-70b-versatile",
        max_tokens=max_tokens,
    )


# json extraction helpers

def _repair_truncated_array(text: str) -> list | None:
    """Recover completed objects from a truncated JSON array or dict-wrapped array."""
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
    chunk = _repair_json_syntax(_normalize_literals(text[start : last_obj_end + 1] + "]"))
    try:
        result = json.loads(chunk)
        if isinstance(result, list) and result:
            return result
        return None
    except Exception:
        return None


def _repair_json_syntax(text: str) -> str:
    """Fix trailing commas before ] or } — common LLM formatting mistake."""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _normalize_literals(text: str) -> str:
    """Replace Python/JS literals with JSON equivalents."""
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    # Replace JS string expressions like "a".repeat(20) → "aaaaaaaa" (capped at 20 chars)
    def _replace_repeat(m):
        char = m.group(1)
        n    = min(int(m.group(2)), 20)
        return f'"{char * n}"'
    text = re.sub(r'"(.)"\s*\.\s*repeat\s*\(\s*(\d+)\s*\)', _replace_repeat, text)
    # Replace JS template literals `abc` → "abc"
    text = re.sub(r'`([^`]*)`', r'"\1"', text)
    return text


def _clean_raw(raw: str) -> str:
    """Strip markdown fences and preamble text before the first JSON structure."""
    text = raw
    for fence in ("```json", "```JSON", "```"):
        text = text.replace(fence, "")
    text = text.strip()
    # Remove any preamble text before the opening [ or {
    m = re.search(r'[\[{]', text)
    if m and m.start() > 0:
        text = text[m.start():]
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
    """Invoke the model with rate-limit retry and context fallback."""
    wait_times = [65, 90, 120]
    for attempt, wait in enumerate(wait_times):
        try:
            return model.invoke(messages).content
        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower() or "rate_limit" in err.lower():
                logger.warning("Rate limit — waiting %ds (attempt %d/%d)…", wait, attempt + 1, len(wait_times))
                time.sleep(wait)
                continue
            if "413" in err or "too large" in err.lower() or "context length" in err.lower():
                logger.warning("Context too large — retrying with smaller model…")
                return get_llm(max_tokens=2000).invoke(messages).content
            raise
    raise ValueError("[LLM] Rate limit still active after retries — skipping this pass.")


def _parse_json_array(raw: str, model, messages: list, retries: int = 2) -> list:
    """Extract and parse a JSON array from LLM output, retrying on failure."""
    current = raw
    for attempt in range(retries + 1):
        text = _clean_raw(current)
        result = _extract_json_array(text)

        if result is not None:
            if attempt > 0:
                logger.info("Recovered %d test cases on attempt %d.", len(result), attempt + 1)
            return result

        snippet = text[:200].replace("\n", " ")
        logger.warning("Parse attempt %d failed. Response starts with: %r", attempt + 1, snippet)

        if attempt < retries:
            if _is_truncated(text):
                logger.info("Response truncated — retrying with fewer cases…")
                fix_msg = (
                    "Your response was cut off. Regenerate with ONLY 4 test cases total. "
                    "Each case: description ≤ 40 chars, 2-3 steps max, expected ≤ 40 chars. "
                    'Return ONLY: {"test_cases": [{"id":"TC001","description":"...","steps":[...],"expected":"..."},...]}'
                    " — no markdown, no text outside the JSON."
                )
            else:
                logger.info("JSON parse failed — retrying with format reminder…")
                fix_msg = (
                    "Your previous response could not be parsed as JSON. "
                    'Return ONLY: {"test_cases": [{"id":"TC001","description":"...","steps":[{"action":"fill","field":"x","value":"y"},{"action":"submit"}],"expected":"..."}]}. '
                    "No markdown, no code fences, no text outside the JSON."
                )
            current = _invoke(model, messages + [
                AIMessage(content=current),
                HumanMessage(content=fix_msg),
            ])

    raise ValueError(f"LLM returned invalid JSON after {retries + 1} attempts.")


# public llm functions

def generate_test_cases(fields: list, url: str, auth_info: dict | None = None,
                        page_text: str = "") -> list:
    """Autonomous test generation for URL mode — delegates to the 2-phase approach."""
    page_context: dict = {}
    if auth_info:
        if auth_info.get("has_login"):
            page_context["auth_type"] = "login"
        elif auth_info.get("has_register"):
            page_context["auth_type"] = "register"
    return generate_quick_test_cases(fields, url, page_text, page_context or None)


def generate_tests_from_spec(
    spec_text: str,
    fields: list,
    url: str,
    sections: list | None = None,
) -> list:
    """Generate test cases from a spec file + live page context.

    Args:
        spec_text: raw text of the requirements/spec file
        fields:    real form fields observed on the live page
        url:       base URL of the application
        sections:  nav sections discovered by Phase 2 (text + href pairs)
                   — used so the LLM can write correct navigate steps
    """
    model    = get_llm_spec(max_tokens=2000)
    has_url  = bool(url and url.strip())

    fields_section = (
        f"Detected form fields on {url}:\n{json.dumps(fields, indent=2)}"
        if fields else "No live page fields — generate a narrative test plan from the spec."
    )
    url_section = f"Target URL: {url}" if has_url else "No URL — this is a test plan document only."

    # Include real section URLs so LLM generates correct navigate steps
    sections_section = ""
    if sections:
        lines = "\n".join(
            f"  - {s.get('text', '').strip()}: {s.get('href', '')}"
            for s in sections[:20]
            if s.get("href")
        )
        sections_section = f"\nDiscovered app sections (use these EXACT hrefs in navigate steps):\n{lines}\n"

    spec_body     = spec_text[:2500]
    common_footer = (
        f"\n{url_section}\n\n{fields_section}\n{sections_section}\n"
        f"Specification:\n---\n{spec_body}\n---\n"
    )

    if has_url:
        # with url: interactive steps (fill/submit/navigate)
        _step_fmt = (
            'Steps use action objects:\n'
            '  {"action":"navigate","value":"EXACT_HREF_FROM_sections"}  — navigate to a section (use exact href above)\n'
            '  {"action":"fill","field":"fieldName","value":"val"}\n'
            '  {"action":"submit"}\n'
            '  {"action":"wait","value":"2"}\n'
            '  {"action":"clear","field":"fieldName"}\n'
            'IMPORTANT: For scenarios that require navigating to a specific module, '
            'use {"action":"navigate"} as the FIRST step with the exact href from "Discovered app sections".\n'
            'Every scenario MUST have at least 3 steps.\n'
        )
        _json_fmt = (
            'Return ONLY valid JSON — no markdown, no text outside it:\n'
            '{"test_cases":[{"id":"TC001","description":"...","steps":[{"action":"fill","field":"...","value":"..."},{"action":"submit"}],"expected":"..."}]}'
        )
    else:
        # plan only: narrative describe steps — no real form interactions
        _step_fmt = (
            'Since there is no live URL, steps must be NARRATIVE DESCRIPTIONS of system behavior.\n'
            'Use this step format:\n'
            '  {"action":"describe","value":"Plain English description of what happens in this step"}\n'
            '  {"action":"wait","value":"10"}  — when a timeout or delay is part of the scenario\n'
            'Every scenario MUST have at least 4 describe steps that tell the complete story.\n'
            'Steps should describe: precondition → trigger → system reaction → verification.\n'
        )
        _json_fmt = (
            'Return ONLY valid JSON — no markdown, no text outside it:\n'
            '{"test_cases":[{"id":"TC001","description":"...","steps":[{"action":"describe","value":"Step description here"}],"expected":"..."}]}'
        )

    _rules = (
        "RÈGLES POUR TOUTES LES PASSES — respecter impérativement :\n"
        "- Rédige les descriptions ET les résultats attendus EN FRANÇAIS\n"
        "- Chaque scénario est un flux multi-étapes complet (minimum 4 étapes)\n"
        "- description ≤ 70 chars — décrit le scénario, pas juste un nom de fonctionnalité\n"
        "- expected ≤ 70 chars — décrit l'état observable final\n"
        "- Couvre les chemins succès ET échec\n"
        "- Référence des valeurs, délais et conditions précises issus de la spec\n"
        "VALIDATION LOGIQUE OBLIGATOIRE (corrige si violé avant de retourner) :\n"
        "- Ne pas inventer de comportements système absents de la spécification\n"
        "- Deux entités ne partagent PAS la même ressource exclusive sans spécification explicite\n"
        "- Chaque requête crée une entité système INDÉPENDANTE sauf mention contraire\n"
        "- L'état final doit être valide, cohérent et atteignable depuis l'état initial\n"
        "- Les délais doivent être réalistes (≥ 2s pour les opérations asynchrones réseau)\n"
        "- Les rôles et permissions doivent respecter strictement la spécification\n\n"
    )

    # pass 1: one success + one failure test case per requirement
    sys_p1 = (
        "You are a senior QA Engineer. Generate exactly 8 system-level scenarios "
        "derived directly from the specification.\n\n"
        + _rules +
        "Coverage rules for this pass:\n"
        "- For EVERY functional requirement: one success flow AND one failure flow\n"
        "- For EVERY user story: a complete happy-path scenario\n"
        "- For EVERY role mentioned: at least one scenario from that role's perspective\n"
        "- For EVERY business rule or constraint: a dedicated scenario\n\n"
        + _step_fmt + "\n" + _json_fmt
    )
    msgs_p1 = [SystemMessage(content=sys_p1), HumanMessage(content=common_footer + "\nGenerate 8 scenarios.")]
    pass1 = _parse_json_array(_invoke(model, msgs_p1), model, msgs_p1)
    for i, tc in enumerate(pass1, 1):
        tc["id"] = f"TC{i:03d}"
    logger.info("Pass 1 (requirements): %d scenarios.", len(pass1))

    logger.info("Waiting 65s between passes to stay within Groq TPM quota…")
    time.sleep(65)

    # pass 2: validation, boundaries, security (7 cases)
    covered = json.dumps([tc.get("description", "")[:50] for tc in pass1])
    n2 = len(pass1) + 1
    sys_p2 = (
        "You are a senior QA Engineer. Generate exactly 7 ADDITIONAL scenarios — "
        "NOT already covered — focused on validation, boundaries, and security.\n\n"
        + _rules +
        "Coverage rules for this pass:\n"
        "- Empty / blank input flows\n"
        "- Maximum length boundary values\n"
        "- Invalid formats (wrong email, wrong date, negative numbers)\n"
        "- Special characters in text fields\n"
        "- Missing mandatory fields\n\n"
        f"Do NOT repeat: {covered}\n"
        f"Start IDs at TC{n2:03d}.\n\n"
        + _step_fmt + "\n" + _json_fmt
    )
    msgs_p2 = [SystemMessage(content=sys_p2), HumanMessage(content=common_footer + "\nGenerate 7 security/boundary scenarios.")]
    try:
        pass2 = _parse_json_array(_invoke(model, msgs_p2), model, msgs_p2)
        for i, tc in enumerate(pass2):
            tc["id"] = f"TC{n2 + i:03d}"
        logger.info("Pass 2 (security/boundary): %d scenarios.", len(pass2))
    except Exception as e:
        logger.warning("Pass 2 failed — skipping. (%s)", e)
        pass2 = []

    logger.info("Waiting 70s between passes to stay within Groq TPM quota…")
    time.sleep(70)

    # pass 3: end-to-end journeys & integration flows (6 cases)
    all_so_far = json.dumps([tc.get("description", "")[:50] for tc in pass1 + pass2])
    n3 = n2 + len(pass2)
    sys_p3 = (
        "You are a senior QA Engineer. Generate exactly 6 end-to-end JOURNEY scenarios — "
        "complete user flows spanning multiple steps and states.\n\n"
        + _rules +
        "Coverage rules for this pass:\n"
        "- Full lifecycle flows (create → use → cancel / delete)\n"
        "- Cross-role interactions (e.g. rider requests → driver accepts → completes)\n"
        "- Timeout and retry behaviors (e.g. no response within N seconds → fallback)\n"
        "- State transitions (pending → active → completed → rated)\n"
        "- Error recovery journeys (failure → retry → success)\n"
        "- Cancellation flows mid-process\n\n"
        f"Do NOT repeat: {all_so_far}\n"
        f"Start IDs at TC{n3:03d}.\n\n"
        + _step_fmt + "\n" + _json_fmt
    )
    msgs_p3 = [SystemMessage(content=sys_p3), HumanMessage(content=common_footer + "\nGenerate 6 journey scenarios.")]
    try:
        pass3 = _parse_json_array(_invoke(model, msgs_p3), model, msgs_p3)
        for i, tc in enumerate(pass3):
            tc["id"] = f"TC{n3 + i:03d}"
        logger.info("Pass 3 (journeys): %d scenarios.", len(pass3))
    except Exception as e:
        logger.warning("Pass 3 failed — skipping. (%s)", e)
        pass3 = []

    logger.info("Waiting 70s between passes to stay within Groq TPM quota…")
    time.sleep(70)

    # pass 4: edge cases, concurrency, non-functional (5 cases)
    all_covered = json.dumps([tc.get("description", "")[:50] for tc in pass1 + pass2 + pass3])
    n4 = n3 + len(pass3)
    sys_p4 = (
        "You are a senior QA Engineer. Generate exactly 5 ADDITIONAL scenarios covering "
        "edge cases and non-functional aspects — NOT already covered.\n\n"
        + _rules +
        "Coverage rules for this pass:\n"
        "- Concurrency: two users performing same action simultaneously\n"
        "- Data integrity: verify no side effects after a failed operation\n"
        "- Performance boundary: action under time constraint\n"
        "- Permission denied: unauthorized role attempts restricted action\n"
        "- Network interruption simulation: wait mid-flow then continue\n\n"
        f"Do NOT repeat: {all_covered}\n"
        f"Start IDs at TC{n4:03d}.\n\n"
        + _step_fmt + "\n" + _json_fmt
    )
    msgs_p4 = [SystemMessage(content=sys_p4), HumanMessage(content=common_footer + "\nGenerate 5 edge-case scenarios.")]
    try:
        pass4 = _parse_json_array(_invoke(model, msgs_p4), model, msgs_p4)
        for i, tc in enumerate(pass4):
            tc["id"] = f"TC{n4 + i:03d}"
        logger.info("Pass 4 (edge cases): %d scenarios.", len(pass4))
    except Exception as e:
        logger.warning("Pass 4 failed — skipping. (%s)", e)
        pass4 = []

    total = pass1 + pass2 + pass3 + pass4
    logger.info("Total from spec: %d scenarios across 4 passes.", len(total))
    return total


def convert_spec_scenarios_to_steps(
    parsed: dict,
    fields: list,
    url: str,
    sections: list | None = None,
) -> list:
    """Convert scenarios/requirements extracted from a spec file into executable Playwright steps.

    This is the PRIMARY path when the user uploads a spec file — the agent executes
    exactly the scenarios written in the file, not LLM-generated ones.

    Args:
        parsed:   output of Planner.parse_markdown_spec()
        fields:   real form fields observed on the live page
        url:      base URL of the application
        sections: nav sections from Phase 2 discovery (text + href)
    """
    scenarios    = parsed.get("scenarios", [])
    requirements = parsed.get("requirements", [])
    user_stories = parsed.get("user_stories", [])
    sections_raw = parsed.get("sections", [])

    if not scenarios and not requirements and not user_stories and not sections_raw:
        logger.info("Spec has no structured content — convert_spec_scenarios_to_steps returns empty.")
        return []

    model = get_llm_spec(max_tokens=2500)

    # build navigation context
    sections_ctx = ""
    if sections:
        lines = "\n".join(
            f"  - {s.get('text', '').strip()}: {s.get('href', '')}"
            for s in sections[:20] if s.get("href")
        )
        sections_ctx = f"\nApp sections (use EXACT hrefs for navigate steps):\n{lines}\n"

    fields_ctx = ""
    if fields:
        names = [f.get("label") or f.get("name") or f.get("id", "") for f in fields[:12]]
        fields_ctx = f"\nForm fields on page: {', '.join(filter(None, names))}\n"

    url_ctx = f"Base URL: {url}\n" if url else ""

    # spec content serialiser (reused per batch)
    def _parse_testdata_credentials(raw: str) -> tuple[str, str]:
        """Extract (username, password) from test_data strings like 'user / pass'."""
        if not raw:
            return "", ""
        parts = [p.strip() for p in raw.split("/")]
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
        # key: value format
        import re as _re
        um = _re.search(r'(?:user(?:name)?|login|email)[:\s]+(\S+)', raw, _re.IGNORECASE)
        pm = _re.search(r'(?:pass(?:word)?|mot de passe)[:\s]+(\S+)', raw, _re.IGNORECASE)
        return (um.group(1) if um else ""), (pm.group(1) if pm else "")

    def _build_spec_content(batch_scenarios, batch_reqs, batch_stories, batch_secs):
        content = ""
        for i, sc in enumerate(batch_scenarios, 1):
            content += f"\n=== Scenario {i} [{sc.get('id','')}]: {sc.get('description', '')}\n"
            if sc.get("given"):
                content += "Given: " + "; ".join(sc["given"]) + "\n"
            if sc.get("when"):
                content += "When: " + "; ".join(sc["when"]) + "\n"
            if sc.get("then"):
                content += "Then: " + "; ".join(sc["then"]) + "\n"
            for step in sc.get("steps", []):
                content += f"  Step: {step.get('value', '')}\n"
            if sc.get("expected"):
                content += f"Expected: {sc['expected']}\n"
            if sc.get("test_data"):
                td = sc["test_data"]
                content += f"Test data: {td}\n"
                u, p = _parse_testdata_credentials(td)
                if u and p:
                    content += f"  → Username: {u}  Password: {p}\n"
        for req in batch_reqs:
            content += f"\n=== Requirement {req['id']}: {req['text']}\n"
        for story in batch_stories:
            content += f"\n=== User Story: {story}\n"
        for sec in batch_secs:
            if sec.get("content"):
                content += f"\n=== Section: {sec['title']}\n"
                content += "\n".join(sec["content"][:8]) + "\n"
        return content

    system_msg = (
        "You are a senior QA Engineer. Convert EACH scenario into an executable automated test case.\n\n"
        "LANGUE — RÈGLE ABSOLUE :\n"
        "Les champs 'description' et 'expected' de chaque cas de test DOIVENT OBLIGATOIREMENT être "
        "rédigés EN FRANÇAIS, quelle que soit la langue du fichier de spécification.\n"
        "Les valeurs dans les étapes verify_text doivent correspondre au texte réel de l'interface "
        "(qui peut être en français ou en anglais selon l'application).\n\n"
        "LANGUAGE RULE — UI MAPPING:\n"
        "The spec file may be in French OR English. The application UI may also be in French OR English.\n"
        "You must map the INTENT of each spec step to the actual UI element, regardless of language.\n"
        "Use the 'App sections' list (real discovered names + hrefs) as the source of truth for navigation.\n"
        "Common concept mappings (spec → UI element, whichever language the app uses):\n"
        "  logout / déconnexion     → button/link labeled 'Logout' or 'Se déconnecter'\n"
        "  add / ajouter            → button labeled 'Add' or 'Ajouter'\n"
        "  save / enregistrer       → button labeled 'Save' or 'Enregistrer'\n"
        "  delete / supprimer       → button labeled 'Delete' or 'Supprimer'\n"
        "  search / rechercher      → button/field labeled 'Search' or 'Rechercher'\n"
        "  edit / modifier          → button labeled 'Edit' or 'Modifier'\n"
        "  username / identifiant   → fill field='user-name' (SauceDemo) or field='username'\n"
        "  password / mot de passe  → fill field='password'\n"
        "  leave / congé            → navigate to Leave/Congés section href\n"
        "  employees / employés     → navigate to PIM/Employees section href\n\n"
        "SAUCEDEMO-SPECIFIC SELECTORS (use EXACTLY when testing saucedemo.com):\n"
        "  Username field          → fill field='user-name'\n"
        "  Password field          → fill field='password'\n"
        "  Login button            → submit (or click 'Login')\n"
        "  Sort dropdown           → {\"action\":\"select\",\"field\":\"product_sort_container\",\"value\":\"<option text>\"}\n"
        "    Options: 'Name (A to Z)', 'Name (Z to A)', 'Price (low to high)', 'Price (high to low)'\n"
        "  Hamburger menu (open)   → click value='Open Menu'\n"
        "  Hamburger menu (close)  → click value='Close Menu'\n"
        "  Cart icon               → navigate value='/cart.html'\n"
        "  Add to cart (any item)  → click value='Add to cart'\n"
        "  Remove from cart        → click value='Remove'\n"
        "  Checkout button         → click value='Checkout'\n"
        "  Continue button         → click value='Continue'\n"
        "  Finish button           → click value='Finish'\n"
        "  Logout link             → click value='Logout'\n"
        "  All Items link          → click value='All Items'\n"
        "  About link              → click value='About'\n"
        "  Reset App State         → click value='Reset App State'\n\n"
        "Step action types (use ONLY these):\n"
        '  {"action":"navigate","value":"EXACT_HREF"}   ← use hrefs from App sections list OR absolute URL\n'
        '  {"action":"fill","field":"fieldName","value":"testValue"}\n'
        '  {"action":"select","field":"dropdownName","value":"optionLabel"}  ← for <select> dropdowns\n'
        '  {"action":"click","value":"label as shown in the app UI"}\n'
        '  {"action":"click_row_action","row":"identifying text in the row","value":"delete|edit"}  ← icon button in table row\n'
        '  {"action":"submit"}\n'
        '  {"action":"verify_text","value":"short text visible on page in the app language"}\n'
        '  {"action":"wait","value":"2"}\n\n'
        "STRICT RULES:\n"
        "1. ONE test case per scenario — do NOT skip any\n"
        "2. LOGIN: fill username → fill password → submit → verify_text\n"
        "   submit is MANDATORY before verify_text in any form scenario\n"
        "3. LOGOUT on SauceDemo: click 'Open Menu' → click 'Logout' → verify_text 'Login'\n"
        "4. NAVIGATION: navigate with EXACT href from App sections, then verify_text with a page title visible on that page\n"
        "5. verify_text: a short keyword (MAX 20 chars) that ACTUALLY appears on the page.\n"
        "   NEVER use full sentences or long error messages — use the KEY WORD only.\n"
        "   Examples: 'Products', 'Dashboard', 'Successfully', 'Login', 'required', 'Invalid', 'THANK YOU'.\n"
        "   SauceDemo specifics:\n"
        "     Login success → verify_text 'Products'\n"
        "     Invalid login → verify_text 'Epic sadface'\n"
        "     Locked user   → verify_text 'locked out'\n"
        "     Logout        → verify_text 'Login'\n"
        "     Cart page     → verify_text 'Your Cart'\n"
        "     Checkout info → verify_text 'Checkout'\n"
        "     Order overview → verify_text 'Overview'\n"
        "     Order complete → verify_text 'THANK YOU'\n"
        "     Item added    → verify_text 'Remove'\n"
        "     Sort changed  → verify_text 'Products'\n"
        "   FORBIDDEN in verify_text: full sentences, any invented person name (John Doe, Jane Smith),\n"
        "   'Employee Created', 'Employee Deleted', 'Employee Updated', 'Leave Applied', 'Leave Approved',\n"
        "   'Record Saved', 'Action Completed', 'Leave Balance', 'Leave History'.\n"
        "6. Use test_data values (Username/Password) from the scenario's '→ Username / Password' lines\n"
        "   ALWAYS use the EXACT credentials from test_data — never invent or reuse credentials from other scenarios\n"
        "7. For navigate: use exact href from App sections OR absolute URL from the spec\n"
        "8. description ≤ 70 chars, expected ≤ 70 chars\n"
        "9. TABLE DELETE/EDIT: When the spec asks to delete or edit a row in a table, use click_row_action.\n"
        "   The 'row' field must contain unique text visible in that row (e.g. the username).\n"
        "   Example — delete user FMLName: {\"action\":\"click_row_action\",\"row\":\"FMLName\",\"value\":\"delete\"}\n"
        "   Example — edit user FMLName:  {\"action\":\"click_row_action\",\"row\":\"FMLName\",\"value\":\"edit\"}\n"
        "   NOTE: An admin user cannot delete itself — use a non-admin username in the 'row' field.\n"
        "10. SEARCH / VIEW EMPLOYEE: NEVER invent employee names like 'John Doe' or 'Jane Smith'.\n"
        "    To find an employee: navigate to PIM, do NOT fill the search field, click 'Search' directly\n"
        "    (empty search shows all employees), then use click_row_action with value='edit' to open the first row.\n"
        "    verify_text after opening employee: use 'Personal Details' (tab label visible on OrangeHRM employee page).\n"
        "    ALSO FORBIDDEN in verify_text: 'John Doe', 'Jane Smith', any invented person name, 'Leave Balance',\n"
        "    'Leave History', 'Employee Created', 'Employee Deleted', 'Employee Updated', 'Leave Applied'.\n"
        "    For search results: verify_text value must be 'Records Found' (shown in OrangeHRM table after search).\n"
        "11. LOGOUT: After clicking logout, verify_text 'Login' (the login page always shows 'Login' as heading).\n"
        "12. MULTI-STEP CHECKOUT (e.g. SauceDemo): Checkout has TWO required buttons:\n"
        "    - 'Continue' validates shipping info and goes to Order Overview page\n"
        "    - 'Finish' completes the order and shows the confirmation/thank-you page\n"
        "    ALWAYS generate BOTH clicks as separate steps: click 'Continue' THEN click 'Finish'.\n"
        "    Without clicking Finish, the confirmation never appears. verify_text after Finish: 'THANK YOU'\n"
        "    Checkout step order: navigate to cart → click 'Checkout' → fill form → click 'Continue' → click 'Finish' → verify_text 'THANK YOU'\n"
        "13. NEW TAB LINKS: Some links open in a new tab (e.g. 'About' in SauceDemo).\n"
        "    After clicking such a link the browser context stays on the CURRENT page.\n"
        "    verify_text must reference text visible on the CURRENT page — not the new tab.\n"
        "    Example: After clicking 'About' (opens saucelabs.com in new tab), verify_text 'Swag Labs'\n"
        "    (the heading still visible on the SauceDemo inventory page you never left).\n"
        "14. FORBIDDEN verify_text values: single digits ('1', '2', '3') and bare numbers.\n"
        "    Use meaningful visible text instead:\n"
        "    - After adding an item to cart: verify_text 'Remove' (the button that replaces 'Add to cart')\n"
        "    - After adding multiple items: verify_text 'Remove'\n"
        "    - NEVER use a cart badge count ('1', '2') as verify_text.\n"
        "15. RESET APP STATE (SauceDemo menu): After clicking Reset App State in the hamburger menu,\n"
        "    the cart is cleared but the page stays on inventory. verify_text 'Products'\n"
        "    (the inventory page heading is always 'Products' and always visible after reset).\n"
        "16. SELF-CONTAINED TESTS — CRITICAL RULE:\n"
        "    Each test case runs in isolation — there is NO guaranteed session state between tests.\n"
        "    If the scenario REQUIRES being logged in (Préconditions say 'Utilisateur connecté',\n"
        "    or the test does something that needs auth: browse products, add to cart, checkout,\n"
        "    open menu, logout), you MUST prepend login steps at the very beginning:\n"
        "      {\"action\":\"fill\",\"field\":\"user-name\",\"value\":\"standard_user\"}\n"
        "      {\"action\":\"fill\",\"field\":\"password\",\"value\":\"secret_sauce\"}\n"
        "      {\"action\":\"submit\"}\n"
        "    Exception: login tests (TC-001, TC-002, TC-003, TC-017) and access-control tests\n"
        "    (TC-013) must NOT prepend login — they test the login page state themselves.\n"
        "    For TC-013 (access without auth): navigate directly to the protected URL,\n"
        "    then verify_text 'Login' (redirected to login page).\n"
        "17. PRODUCT-SPECIFIC ADD TO CART:\n"
        "    To add a specific product (e.g. 'Sauce Labs Backpack'), click 'Add to cart' button —\n"
        "    SauceDemo shows 6 products; 'Add to cart' clicks the FIRST available button.\n"
        "    To add the Bike Light specifically: first click the product name 'Sauce Labs Bike Light'\n"
        "    (navigates to detail page), then click 'Add to cart' on the detail page.\n"
        "18. PERFORMANCE TEST (TC-017): Login with credentials 'performance_glitch_user' / 'secret_sauce',\n"
        "    then add a wait step of 5 seconds: {\"action\":\"wait\",\"value\":\"5\"},\n"
        "    then verify_text 'Products'. This simulates the slow login delay.\n"
        "19. TESTS THAT NEED A PRODUCT IN CART AS PRECONDITION (TC-010, TC-011, TC-012, TC-016, TC-018):\n"
        "    After the login steps (rule 16), add these setup steps BEFORE the actual test steps:\n"
        "      {\"action\":\"click\",\"value\":\"Add to cart\"}\n"
        "    This adds the first product so the cart is not empty when the test begins.\n"
        "    For TC-016 (Reset App State): add product, THEN open menu and click Reset App State.\n"
        "    For TC-018 (badge with 2 items): add 'Add to cart' twice in a row (two different buttons).\n\n"
        "Return ONLY valid JSON:\n"
        '{"test_cases":[{"id":"TC001","description":"...","steps":[...],"expected":"..."}]}'
    )

    # batched conversion — avoids output-token truncation
    # Each LLM call handles at most BATCH_SIZE scenarios so the JSON output
    # always fits within max_tokens=2500.
    BATCH_SIZE = 10
    all_results: list = []

    # Split scenarios into batches; if none exist, run one empty batch (for requirements/stories only)
    if scenarios:
        batches = [scenarios[i : i + BATCH_SIZE] for i in range(0, len(scenarios), BATCH_SIZE)]
    else:
        batches = [[]]
    for batch_idx, batch in enumerate(batches):
        # Include requirements / stories / free-text only in the first batch
        # (they provide global context once, not repeated every batch)
        reqs_b    = requirements  if batch_idx == 0 else []
        stories_b = user_stories  if batch_idx == 0 else []
        secs_b    = sections_raw  if batch_idx == 0 else []

        spec_content = _build_spec_content(batch, reqs_b, stories_b, secs_b)
        human_msg = (
            f"{url_ctx}{fields_ctx}{sections_ctx}\n"
            f"Spec scenarios and requirements to convert:\n{spec_content}\n\n"
            "Convert EVERY scenario above to an executable test case."
        )
        msgs = [SystemMessage(content=system_msg), HumanMessage(content=human_msg)]
        try:
            batch_result = _parse_json_array(_invoke(model, msgs), model, msgs)
            logger.info(
                "Batch %d/%d: converted %d/%d spec scenarios.",
                batch_idx + 1, len(batches), len(batch_result), len(batch),
            )
            all_results.extend(batch_result)
        except Exception as exc:
            logger.warning("convert_spec batch %d failed (%s) — skipping.", batch_idx + 1, exc)

        # Groq TPM cooldown between batches
        if batch_idx < len(batches) - 1:
            logger.info("Waiting 65s between batches (Groq TPM quota)…")
            time.sleep(65)

    # Repair pass — fuzzy=False: `fields` only reflects the entry page, so
    # fields referenced on post-login pages must not be rewritten by guesswork.
    all_results = _validate_test_cases(all_results, fields, fuzzy=False)

    logger.info("Converted %d spec scenarios total.", len(all_results))
    return all_results


def extract_spec_text(file_content: bytes, filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if lower.endswith((".docx", ".doc")):
        try:
            import docx
            from docx import Document
            doc = Document(io.BytesIO(file_content))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            return f"[Could not extract .docx content: {e}]"
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
        logger.warning("Conclusion generation failed: %s", e)
        return None


def _build_interface_context(fields: list, url: str,
                              page_text: str = "",
                              page_context: dict | None = None) -> str:
    """Build a rich textual description of the interface for the LLM."""
    ctx = page_context or {}

    field_details = []
    for f in fields:
        name = f.get("name") or f.get("id") or ""
        if not name:
            continue  # skip fields with no usable identifier — fill_form handles them dynamically
        parts = []
        if f.get("label"):       parts.append(f"label='{f['label']}'")
        if f.get("placeholder"): parts.append(f"placeholder='{f['placeholder']}'")
        if f.get("type"):        parts.append(f"type={f['type']}")
        if f.get("required"):    parts.append("REQUIRED")
        field_details.append(f"{name} ({', '.join(parts)})" if parts else name)

    lines = [f"URL: {url}"]

    # Auth context (highest priority — specialised prompts generated later)
    if ctx.get("auth_type") == "login":
        lines.append("Form type: LOGIN — test valid credentials, wrong password, empty fields, locked account, case sensitivity")
    elif ctx.get("auth_type") == "register":
        lines.append("Form type: REGISTER — test valid signup, duplicate email, weak password, required fields, format validation")

    # Generic page type label (from Planner.detect_page_context)
    if ctx.get("page_type_label"):
        lines.append(f"Page type: {ctx['page_type_label']}")
    elif ctx.get("page_type") and ctx["page_type"] not in ("auth",):
        type_labels = {
            "crud_list": "CRUD_LIST — page with data table and Add/Edit/Delete buttons. Focus on Create→Read→Update→Delete flows.",
            "data_list": "DATA_LIST — read-only data table. Focus on verifying data loads, search/filter if present.",
            "data_form": "DATA_FORM — form for creating or editing a record. Focus on valid submit, missing required fields, boundary values.",
            "general":   "GENERAL — informational page. Verify key content is visible.",
        }
        label = type_labels.get(ctx["page_type"])
        if label:
            lines.append(f"Page type: {label}")

    if ctx.get("title"):
        lines.append(f"Page title: {ctx['title']}")
    if ctx.get("headings"):
        lines.append(f"Headings: {' | '.join(ctx['headings'][:5])}")
    if ctx.get("labels"):
        lines.append(f"Labels: {' | '.join(ctx['labels'][:12])}")
    if ctx.get("buttons"):
        lines.append(f"Buttons/Links: {' | '.join(ctx['buttons'][:10])}")
    if ctx.get("action_links"):
        unique = list(dict.fromkeys(ctx["action_links"]))[:15]
        lines.append(f"All clickable: {' | '.join(unique)}")
    if ctx.get("icons"):
        lines.append(f"Icon buttons (no text): {' | '.join(ctx['icons'][:12])}")
    if ctx.get("nav_links"):
        nav = " | ".join(
            f"{l.get('text', '')}={l.get('href', '')}"
            for l in ctx["nav_links"][:10] if l.get("href")
        )
        lines.append(f"Navigation links (text=href — use the EXACT href in navigate steps): {nav}")
    if ctx.get("alerts"):
        lines.append(
            f"Messages currently visible on page (real assertion text): {' | '.join(ctx['alerts'][:5])}"
        )
    if ctx.get("selects"):
        for sel in ctx["selects"][:4]:
            opts = ", ".join(sel.get("options", [])[:8])
            # Pick the best identifier for the `select` action field parameter
            field_id = sel.get("id") or sel.get("name") or sel.get("class", "").split()[0] if sel.get("class") else sel.get("name", "")
            lines.append(f"Dropdown field='{field_id}' options=[{opts}]  (use this exact field id in select action)")
    if ctx.get("has_table"):
        row_info = f"{ctx.get('row_count', 0)} rows"
        header_info = ""
        if ctx.get("table_headers"):
            header_info = f", columns: {' | '.join(ctx['table_headers'])}"
        lines.append(f"Data table present ({row_info}{header_info}) — CRUD likely")
    if field_details:
        lines.append(f"Form fields: {json.dumps(field_details)}")
    if page_text:
        lines.append(f"Page text excerpt: {page_text[:250]}")

    return "\n".join(lines)


# deterministic post-validation of LLM-generated test cases

def _resolve_field_id(name: str, fields: list, fuzzy: bool = True) -> str | None:
    """Map an LLM-written field name to a real observed field identifier.

    Exact id/name match first (case-insensitive), then — if fuzzy — substring
    and label/placeholder matching. Returns None when nothing matches.
    """
    if not name or not fields:
        return None
    target = name.strip().lower()

    # 1. exact id/name match (the identifier fill_field resolves fastest)
    for f in fields:
        for key in ("id", "name"):
            v = (f.get(key) or "").strip()
            if v and v.lower() == target:
                return v

    if not fuzzy:
        return None

    # 2. relaxed: target appears in id/name/label/placeholder (or vice-versa)
    tokens = [t for t in re.split(r"[\s_\-]+", target) if len(t) >= 3]
    for f in fields:
        ident = f.get("id") or f.get("name") or ""
        if not ident:
            continue
        hay = " ".join(
            str(f.get(k) or "") for k in ("id", "name", "label", "placeholder")
        ).lower()
        if target in hay or (tokens and any(tok in hay for tok in tokens)):
            return ident
    return None


def _snap_select_value(field: str, value: str, selects: list) -> str:
    """Snap an LLM-written dropdown value to the closest real option label."""
    if not value or not selects:
        return value
    field_l = (field or "").lower()
    value_l = value.lower()
    for sel in selects:
        idents = {str(sel.get(k) or "").lower() for k in ("id", "name", "data_test")}
        if field_l and field_l not in idents and not any(field_l in i for i in idents if i):
            continue
        options = sel.get("options") or []
        for opt in options:            # exact (case-insensitive)
            if opt.lower() == value_l:
                return opt
        for opt in options:            # substring either way
            if value_l in opt.lower() or opt.lower() in value_l:
                return opt
    return value


def _validate_test_cases(cases: list, fields: list,
                         ctx: dict | None = None, fuzzy: bool = True) -> list:
    """Deterministically repair LLM-generated test cases against the real page.

    - fill/select/clear/check: resolve 'field' to an observed field identifier
    - select: snap the value to a real option label
    - verify_text: strip quotes, drop bare-number assertions, truncate to 30 chars
    - insert a missing {"action":"submit"} between a fill and its verify_text
    - drop cases with no usable steps
    """
    ctx      = ctx or {}
    selects  = ctx.get("selects") or []
    valid: list = []

    for tc in cases:
        if not isinstance(tc, dict):
            continue
        steps = tc.get("steps")
        if not isinstance(steps, list) or not steps:
            continue

        fixed_steps: list = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = step.get("action") or ("fill" if step.get("field") else "")

            if action in ("fill", "clear", "check", "select") and step.get("field"):
                resolved = _resolve_field_id(str(step["field"]), fields, fuzzy=fuzzy)
                if resolved and resolved != step["field"]:
                    logger.debug("Field repair: %r → %r in %s",
                                 step["field"], resolved, tc.get("id", "?"))
                    step["field"] = resolved

            if action == "select":
                step["value"] = _snap_select_value(
                    str(step.get("field") or ""), str(step.get("value") or ""), selects
                )

            if action == "verify_text":
                val = str(step.get("value") or step.get("text") or "").strip().strip("'\"")
                # bare numbers / single chars are useless assertions — drop the step
                if not val or val.isdigit() or len(val) < 2:
                    logger.debug("Dropping useless verify_text %r in %s", val, tc.get("id", "?"))
                    continue
                if len(val) > 30:
                    val = (val[:30].rsplit(" ", 1)[0] or val[:30]).strip()
                step["value"] = val

            if action == "fill" and len(str(step.get("value") or "")) > 60:
                step["value"] = str(step["value"])[:60]

            fixed_steps.append(step)

        # form filled but never submitted before the assertion → insert submit
        first_verify = next(
            (i for i, s in enumerate(fixed_steps) if s.get("action") == "verify_text"), None
        )
        if first_verify is not None:
            before      = fixed_steps[:first_verify]
            has_fill    = any(s.get("action") in ("fill", "fill_form", "select", "check") for s in before)
            has_trigger = any(s.get("action") in ("submit", "click", "click_row_action") for s in before)
            if has_fill and not has_trigger:
                logger.debug("Inserting missing submit before verify_text in %s", tc.get("id", "?"))
                fixed_steps.insert(first_verify, {"action": "submit"})

        if not fixed_steps:
            continue
        for k in ("description", "expected"):
            if isinstance(tc.get(k), str):
                tc[k] = tc[k].strip()
        tc["steps"] = fixed_steps
        valid.append(tc)

    dropped = len(cases) - len(valid)
    if dropped:
        logger.info("Validation dropped %d unusable test case(s).", dropped)
    return valid


_ACTIONS_DOC = (
    'Available step actions:\n'
    '  {"action":"fill","field":"EXACT_FIELD_ID","value":"val"}  — fill a KNOWN field by id/name\n'
    '  {"action":"fill_form","value":"hint text"}                — fill ALL visible fields on current page (use after click opens a form)\n'
    '  {"action":"select","field":"EXACT_FIELD_ID","value":"opt"}— pick dropdown option\n'
    '  {"action":"click","text":"Exact Button Text"}             — click button/link by text, OR icon label from "Icon buttons"\n'
    '  {"action":"click","text":"Cart"}                          — example: click the cart icon (use exact label from "Icon buttons")\n'
    '  {"action":"click","text":"Toggle Menu"}                   — open hamburger/sidebar toggle (use exact label from "Icon buttons")\n'
    '  {"action":"check","field":"checkboxName"}                 — tick checkbox\n'
    '  {"action":"navigate","value":"/path"}                     — go to URL path\n'
    '  {"action":"submit"}                                       — submit the current form\n'
    '  {"action":"verify_text","value":"keyword"}                — assert keyword appears on page (use after submit)\n'
    '\n'
    '=== MANDATORY PATTERNS — always follow these ===\n'
    '\n'
    'ADD/CREATE (when you see "Add", "New", "Create" buttons):\n'
    '  {"action":"click","text":"<exact Add button text>"},\n'
    '  {"action":"fill_form","value":"<test name or value>"},\n'
    '  {"action":"submit"},\n'
    '  {"action":"verify_text","value":"Successfully"}\n'
    '\n'
    'EDIT/UPDATE (when you see "Edit", "Modify" buttons):\n'
    '  {"action":"click","text":"<exact Edit button text>"},\n'
    '  {"action":"fill_form","value":"<updated value>"},\n'
    '  {"action":"submit"},\n'
    '  {"action":"verify_text","value":"Successfully"}\n'
    '\n'
    'DELETE/REMOVE (when you see "Delete", "Remove" buttons):\n'
    '  {"action":"click","text":"<exact Delete button text>"},\n'
    '  {"action":"verify_text","value":"Successfully"}\n'
    '  (dialogs are auto-confirmed — no extra step needed)\n'
    '\n'
    'FORM VALIDATION (fields already visible on page):\n'
    '  {"action":"fill","field":"<exact id>","value":"<value>"},\n'
    '  {"action":"submit"},\n'
    '  {"action":"verify_text","value":"<expected keyword>"}\n'
)

_OUTPUT_RULES = (
    'OUTPUT RULES (strictly enforced):\n'
    '- 2 to 5 steps per scenario\n'
    '- click: EXACT text from "Buttons/Links", "All clickable", OR label from "Icon buttons"\n'
    '- Icon buttons MUST be tested: if "Icon buttons" section lists Cart, Toggle Menu, etc., generate at least one scenario per icon\n'
    '- fill: ONLY when field id/name is listed in "Form fields" section\n'
    '- fill_form: use instead of fill when a click opens a NEW form (Add/Edit flows)\n'
    '- verify_text: end every CRUD test with this action\n'
    '- fill/select values: MAX 20 chars, literal strings only — NO JavaScript expressions\n'
    '- description: MAX 40 chars, French\n'
    '- expected: MAX 40 chars, French\n'
    '- Compact JSON, no indentation, no trailing commas\n'
)

_JSON_FMT = (
    'OUTPUT: JSON ONLY — no preamble, no explanation, no text before or after.\n'
    'Start your response with { and end with }.\n'
    '{"test_cases":[{"id":"TC001","description":"...","steps":[{"action":"fill","field":"f","value":"v"},{"action":"submit"}],"expected":"..."}]}'
)

_EXAMPLE_CASE = (
    'EXAMPLE — page with Form fields user-name, password and Heading "Products" after login:\n'
    '{"test_cases":[{"id":"TC001","description":"Connexion valide",'
    '"steps":[{"action":"fill","field":"user-name","value":"standard_user"},'
    '{"action":"fill","field":"password","value":"secret_sauce"},'
    '{"action":"submit"},'
    '{"action":"verify_text","value":"Products"}],'
    '"expected":"Utilisateur connecté, page Products affichée"}]}\n'
)

_SELF_CHECK = (
    'FINAL SELF-CHECK — silently fix any violation BEFORE returning:\n'
    '1. Every fill/select "field" value exists VERBATIM in the "Form fields" or "Dropdown" lines above.\n'
    '2. Every click text exists VERBATIM in "Buttons/Links", "All clickable" or "Icon buttons".\n'
    '3. Every navigate value is an EXACT href from "Navigation links".\n'
    '4. Any scenario that fills a form has {"action":"submit"} BEFORE its verify_text.\n'
    '5. verify_text is ≤ 20 chars of text that will REALLY be on screen — prefer words from\n'
    '   "Headings", "Messages currently visible", or generic \'Successfully\'/\'Invalid\'/\'required\'.\n'
    '6. Negative tests (invalid input) expect an ERROR keyword, never a success keyword.\n'
)


def _extract_scenarios_from_text(text: str) -> list[str]:
    """Fallback: extract scenario names from numbered/bulleted plain text."""
    scenarios = []
    for line in text.splitlines():
        line = line.strip()
        # Match: "1. Foo", "- Foo", "* Foo", "• Foo"
        m = re.match(r'^(?:\d+[.)]\s*|[-*•]\s*)(.+)', line)
        if m:
            s = m.group(1).strip().strip('"').strip("'")
            if 3 < len(s) < 80:
                scenarios.append(s)
    return scenarios


def _identify_scenarios(interface_ctx: str) -> list[str]:
    """
    Phase 1 — autonomous discovery: ask the LLM what should be tested.
    Returns a list of scenario names. Small call, never truncates.
    """
    model = get_llm(max_tokens=800)
    # Use a specialized prompt for auth forms to avoid wrong scenario generation
    is_login    = "Form type: LOGIN"    in interface_ctx
    is_register = "Form type: REGISTER" in interface_ctx

    _security_note = (
        "This is authorized QA security validation for our own application. "
        "Boundary and input validation tests are standard QA practice."
    )
    if is_login:
        sys_msg = (
            f"You are a senior QA Engineer. {_security_note}\n"
            "This is a LOGIN FORM. Generate ONLY authentication validation scenarios.\n"
            "Include: valid login, wrong password, empty username, empty password, "
            "both fields empty, special chars in username, very long username, "
            "spaces-only, case sensitivity check.\n"
            "Return ONLY a raw JSON array of short French descriptions (max 40 chars).\n"
            "No preamble — start directly with [\n"
            'Example: ["Connexion valide","Mauvais mot de passe","Champ login vide",'
            '"Caractères spéciaux","Login trop long","Espaces seulement"]'
        )
    elif is_register:
        sys_msg = (
            f"You are a senior QA Engineer. {_security_note}\n"
            "This is a REGISTRATION FORM. Generate ONLY registration validation scenarios.\n"
            "Include: valid registration, duplicate email, weak password, "
            "empty required fields, invalid email format, password mismatch, very long values.\n"
            "Return ONLY a raw JSON array of short French descriptions (max 40 chars).\n"
            "No preamble — start directly with [\n"
            'Example: ["Inscription valide","Email déjà utilisé","Mot de passe faible",'
            '"Champ requis vide","Format email invalide"]'
        )
    else:
        # Detect page type for specialised guidance
        is_crud = "CRUD_LIST" in interface_ctx
        is_form = "DATA_FORM" in interface_ctx
        is_list = "DATA_LIST" in interface_ctx

        if is_crud:
            crud_guidance = (
                "This is a CRUD LIST page (table + add/edit/delete buttons).\n"
                "Generate scenarios for: Create a record, Read/verify table loads, "
                "Edit an existing record, Delete a record, Search/filter, "
                "boundary values on any form fields, required field validation.\n"
                "Include at least 1 negative test (missing required field).\n"
            )
        elif is_form:
            crud_guidance = (
                "This is a DATA FORM page (form for creating/editing).\n"
                "Generate scenarios for: Valid submission, missing required field, "
                "boundary values (very long input, special characters), "
                "invalid format (wrong email, negative number), empty submission.\n"
                "Include at least 1 negative test.\n"
            )
        elif is_list:
            crud_guidance = (
                "This is a DATA LIST page (read-only table).\n"
                "Generate scenarios for: Verify data loads, search/filter if present, "
                "pagination if present, verify column headers visible.\n"
            )
        else:
            crud_guidance = (
                "Scan all buttons and links. For each CRUD button: generate a scenario.\n"
                "For forms: valid submit + at least 1 invalid input scenario.\n"
            )

        sys_msg = (
            f"You are a senior QA Engineer performing authorized QA testing. {_security_note}\n\n"
            f"{crud_guidance}\n"
            "ALSO check:\n"
            "  - 'All clickable' / 'Buttons/Links': Add/Edit/Delete/Search buttons → one scenario each\n"
            "  - 'Icon buttons': Cart, Toggle Menu, Search, User Profile, etc. → one scenario EACH (open it, verify it works)\n"
            "  - 'Form fields': empty required, invalid format, boundary length — fields marked REQUIRED get a dedicated empty-field test\n"
            "  - 'Navigation links': the 2-3 most important sections → one navigation scenario each\n"
            "  - 'Data table present': verify it loads\n\n"
            "Return ONLY a raw JSON array of short French descriptions (max 40 chars each).\n"
            "No preamble — start directly with [\n"
            'Example: ["Ajouter un employé","Ouvrir le panier","Ouvrir le menu latéral",'
            '"Rechercher par nom","Champ requis vide","Valeur limite"]'
        )
    try:
        msgs = [SystemMessage(content=sys_msg), HumanMessage(content=interface_ctx)]
        raw  = _invoke(model, msgs)
        text = _clean_raw(raw)
        logger.debug("Scenario discovery raw (%d chars): %r", len(text), text[:120])

        # Try JSON parse first
        result = _try_parse_array(text) or _extract_json_array(text)

        if isinstance(result, list) and result:
            # If the LLM returned test-case dicts instead of strings, extract descriptions
            if all(isinstance(s, dict) for s in result):
                scenarios = [
                    (s.get("description") or s.get("title") or "")[:50]
                    for s in result
                    if (s.get("description") or s.get("title") or "").strip()
                ]
            else:
                scenarios = [s for s in result if isinstance(s, str) and s.strip()]

            if scenarios:
                logger.info("Identified %d scenarios autonomously.", len(scenarios))
                return scenarios

        fallback = _extract_scenarios_from_text(text)
        if fallback:
            logger.info("Identified %d scenarios via text fallback.", len(fallback))
            return fallback

        logger.warning("Could not parse any scenarios — raw: %r", text[:300])
    except Exception as e:
        logger.warning("Scenario identification failed: %s", e)
    return []


def _generate_batch(scenarios: list[str], interface_ctx: str,
                    start_id: int) -> list[dict]:
    """
    Phase 2 — generate test cases for a small batch of scenario names.
    Batch of 2 max to avoid truncation.
    """
    model = get_llm(max_tokens=2500)
    scenario_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(scenarios))
    _VERIFY_RULE = (
        "verify_text RULE — value must be a SHORT keyword (max 20 chars) that WILL appear on-screen:\n"
        "  Success cases:  'Successfully', 'saved', 'added', 'deleted', 'updated', 'Welcome'\n"
        "  Error/invalid:  'Invalid', 'required', 'incorrect', 'error', 'failed'\n"
        "  Page headings:  use the EXACT word from 'Headings' section (e.g. 'Products', 'Dashboard')\n"
        "  Login success:  use heading from 'Headings' if present, else 'Dashboard' or 'Welcome'\n"
        "  Cart/checkout:  'Your Cart', 'Overview', 'THANK YOU'\n"
        "  NEVER use full sentences — short keyword only.\n"
        "  NEVER invent text you are not sure about; prefer 'Successfully' for success, 'Invalid' for error.\n"
    )
    _FIELD_RULE = (
        "FIELD ID RULE:\n"
        "  - fill: ONLY use identifiers listed in 'Form fields' section (exact id or name)\n"
        "  - select: ONLY use identifiers listed in 'Dropdown' lines — value MUST be one of the listed options\n"
        "  - Do NOT invent field IDs that are not in the context above.\n"
        "  - If 'Form fields' lists 'user-name', write: {\"action\":\"fill\",\"field\":\"user-name\",\"value\":\"...\"}\n"
    )
    sys_msg = (
        "You are a senior QA Engineer. Generate test cases for these specific scenarios.\n\n"
        + _ACTIONS_DOC + "\n"
        + _OUTPUT_RULES + "\n"
        + _VERIFY_RULE + "\n"
        + _FIELD_RULE + "\n"
        + _SELF_CHECK + "\n"
        + _EXAMPLE_CASE + "\n"
        + _JSON_FMT
    )
    user_msg = (
        f"{interface_ctx}\n\n"
        f"Generate test cases for EXACTLY these scenarios:\n{scenario_list}\n"
        f"Start IDs at TC{start_id:03d}.\n"
        f"IMPORTANT: use ONLY field IDs from the 'Form fields' section above. "
        f"Use ONLY dropdown values from the 'Dropdown' lines. "
        f"Use ONLY button text from 'Buttons/Links' or 'All clickable' sections."
    )
    try:
        msgs = [SystemMessage(content=sys_msg), HumanMessage(content=user_msg)]
        raw   = _invoke(model, msgs)
        cases = _parse_json_array(raw, model, msgs, retries=1)
        for i, tc in enumerate(cases):
            tc["id"] = f"TC{start_id + i:03d}"
        return cases
    except Exception as e:
        logger.warning("Batch generation failed: %s", e)
        return []


def generate_quick_test_cases(fields: list, url: str,
                               page_text: str = "",
                               page_context: dict | None = None) -> list:
    """
    Fully autonomous 2-phase test generation:
    Phase 1 — LLM identifies ALL scenarios worth testing (no count limit).
    Phase 2 — generates test cases in batches of 3 (never truncates).
    """
    interface_ctx = _build_interface_context(fields, url, page_text, page_context)
    logger.info("generate_quick_test_cases called for %s (%d fields)", url, len(fields))

    scenarios = _identify_scenarios(interface_ctx)
    if not scenarios:
        logger.info("No scenarios identified for %s — returning empty.", url)
        return []

    if len(scenarios) > 15:
        logger.info("Capping scenarios %d → 15", len(scenarios))
        scenarios = scenarios[:15]

    logger.info("Autonomous plan: %d scenarios for %s", len(scenarios), url)

    # Brief cooldown after the scenario-identification call before starting batches
    # — prevents back-to-back requests that immediately hit the 6k TPM limit
    time.sleep(3)

    # Phase 2: generate in batches of 2 (avoids truncation on verbose responses)
    all_cases: list = []
    batch_size = 2
    for i in range(0, len(scenarios), batch_size):
        batch = scenarios[i : i + batch_size]
        cases = _generate_batch(batch, interface_ctx, start_id=len(all_cases) + 1)
        all_cases.extend(cases)
        if i + batch_size < len(scenarios):
            time.sleep(5)  # pause between batches to stay within 6k TPM

    # Deterministic repair pass: fix field IDs, dropdown values, verify_text,
    # and missing submits against what actually exists on the page.
    all_cases = _validate_test_cases(all_cases, fields, page_context, fuzzy=True)
    for i, tc in enumerate(all_cases, 1):
        tc["id"] = f"TC{i:03d}"

    logger.info("Generated %d test cases for %s", len(all_cases), url)
    return all_cases
