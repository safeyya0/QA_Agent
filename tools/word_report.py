import os
import logging
from datetime import datetime
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

logger = logging.getLogger(__name__)

# omnishore brand palette
C = {
    "navy":       "0D2B6B",
    "navy2":      "1F3864",
    "accent":     "2D5BE3",
    "light":      "E8EEF8",
    "white":      "FFFFFF",
    "dark":       "0A1E4A",
    "success":    "1B8A4C",
    "success_bg": "C6EFCE",
    "danger":     "C0392B",
    "danger_bg":  "FFC7CE",
    "warning":    "D4820A",
    "warning_bg": "FFEB9C",
    "neutral":    "8A97B0",
    "neutral_bg": "D9D9D9",
    "muted":      "64748B",
    "gray_bg":    "F8FAFC",
    "indigo":     "1A3F8F",
    "slate":      "334155",
}

STATUS_FR = {
    "PASSED":  "RÉUSSI",  "passed":  "RÉUSSI",
    "FAILED":  "ÉCHOUÉ",  "failed":  "ÉCHOUÉ",
    "PARTIAL": "PARTIEL", "partial": "PARTIEL",
    "SKIPPED": "IGNORÉ",  "skipped": "IGNORÉ",
    "PLANNED": "PLANIFIÉ","planned": "PLANIFIÉ",
    "UNKNOWN": "INCONNU", "unknown": "INCONNU",
}
STATUS_BG = {
    "PASSED":  "C6EFCE", "passed":  "C6EFCE",
    "FAILED":  "FFC7CE", "failed":  "FFC7CE",
    "PARTIAL": "FFEB9C", "partial": "FFEB9C",
    "SKIPPED": "D9D9D9", "skipped": "D9D9D9",
    "PLANNED": "DBEAFE", "planned": "DBEAFE",
    "UNKNOWN": "F1F5F9", "unknown": "F1F5F9",
}
STATUS_FG = {
    "PASSED":  "1B8A4C", "passed":  "1B8A4C",
    "FAILED":  "C0392B", "failed":  "C0392B",
    "PARTIAL": "D4820A", "partial": "D4820A",
    "SKIPPED": "64748B", "skipped": "64748B",
    "PLANNED": "2563EB", "planned": "2563EB",
    "UNKNOWN": "64748B", "unknown": "64748B",
}
STEP_FR = {
    "register":            "Inscription",
    "verify_registration": "Vérification inscription",
    "navigate_to_login":   "Navigation connexion",
    "login":               "Connexion",
    "verify_login":        "Vérification connexion",
    "invalid_login":       "Connexion invalide",
    "logout":              "Déconnexion",
}


# low-level helpers

import re as _re

def _safe(text) -> str:
    """Strip XML-incompatible characters (null bytes, control chars) from any value."""
    s = str(text) if text is not None else "—"
    return _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)


def _rgb(h: str) -> RGBColor:
    h = h.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _cell_bg(cell, hex_color: str):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color.lstrip("#"))
    tcPr.append(shd)


def _cell_border(cell, hex_color: str = "E2E8F0", width: str = "4"):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    bdr  = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), width)
        b.set(qn("w:color"), hex_color.lstrip("#"))
        bdr.append(b)
    tcPr.append(bdr)


def _run(para, text: str, bold=False, italic=False, size=10,
         color=None, font="Calibri"):
    r = para.add_run(text)
    r.bold   = bold
    r.italic = italic
    r.font.size = Pt(size)
    r.font.name = font
    if color:
        r.font.color.rgb = _rgb(color)
    return r


def _para(doc, text="", bold=False, size=10, color=None,
          align=WD_ALIGN_PARAGRAPH.LEFT, before=0, after=4, italic=False):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after  = Pt(after)
    if text:
        _run(p, text, bold=bold, italic=italic, size=size, color=color)
    return p


def _section_title(doc, number: str, title: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after  = Pt(6)
    nr = p.add_run(f"{number}.  ")
    nr.bold = True; nr.font.size = Pt(13); nr.font.name = "Calibri"
    nr.font.color.rgb = _rgb(C["accent"])
    tr = p.add_run(title.upper())
    tr.bold = True; tr.font.size = Pt(13); tr.font.name = "Calibri"
    tr.font.color.rgb = _rgb(C["navy"])
    pPr  = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "6")
    bot.set(qn("w:color"), "C8D5EC")
    pBdr.append(bot); pPr.append(pBdr)


def _subsection(doc, title: str):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(4)
    r = p.add_run(title)
    r.bold = True; r.font.size = Pt(10.5); r.font.name = "Calibri"
    r.font.color.rgb = _rgb(C["navy2"])


def _hr(doc, color="C8D5EC"):
    p    = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)
    pPr  = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "4")
    bot.set(qn("w:color"), color)
    pBdr.append(bot); pPr.append(pBdr)


def _info_box(doc, lines: list, bg=None, border=None):
    bg     = bg     or C["light"]
    border = border or C["accent"]
    tbl  = doc.add_table(rows=1, cols=1)
    tbl.style = "Table Grid"
    cell = tbl.cell(0, 0)
    _cell_bg(cell, bg)
    _cell_border(cell, border, "8")
    for i, (label, value) in enumerate(lines):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after  = Pt(3)
        lr = p.add_run(f"{label} : ")
        lr.bold = True; lr.font.size = Pt(9); lr.font.name = "Calibri"
        lr.font.color.rgb = _rgb(C["slate"])
        vr = p.add_run(value)
        vr.font.size = Pt(9); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["navy"])
    doc.add_paragraph()


def _scorecard(doc, total, passed, failed, partial, rate):
    tbl = doc.add_table(rows=2, cols=5)
    tbl.style = "Table Grid"
    cols_data = [
        ("TOTAL",           str(total),   C["navy"],   C["light"]),
        ("RÉUSSIS",         str(passed),  C["success"],C["success_bg"]),
        ("ÉCHOUÉS",         str(failed),  C["danger"], C["danger_bg"]),
        ("PARTIELS",        str(partial), C["warning"],C["warning_bg"]),
        ("TAUX DE RÉUSSITE",rate,         C["accent"], "E0E7FF"),
    ]
    for i, (label, value, fg, bg) in enumerate(cols_data):
        hc = tbl.rows[0].cells[i]
        vc = tbl.rows[1].cells[i]
        _cell_bg(hc, C["navy2"]); _cell_bg(vc, bg)
        _cell_border(hc, C["navy"]); _cell_border(vc, "E2E8F0")
        hp = hc.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hp.paragraph_format.space_before = Pt(5)
        hp.paragraph_format.space_after  = Pt(5)
        hr = hp.add_run(label)
        hr.bold = True; hr.font.size = Pt(7); hr.font.name = "Calibri"
        hr.font.color.rgb = _rgb(C["light"])
        vp = vc.paragraphs[0]
        vp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        vp.paragraph_format.space_before = Pt(8)
        vp.paragraph_format.space_after  = Pt(8)
        vr = vp.add_run(value)
        vr.bold = True; vr.font.size = Pt(22); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(fg)
    doc.add_paragraph()


# header / footer setup

def _setup_header(section, url: str):
    header = section.header
    p = header.paragraphs[0]
    p.clear()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r1 = p.add_run("OMNISHORE QA Agent")
    r1.bold = True; r1.font.size = Pt(8); r1.font.name = "Calibri"
    r1.font.color.rgb = _rgb(C["navy"])
    r2 = p.add_run(f"  |  {url[:70]}")
    r2.font.size = Pt(8); r2.font.name = "Calibri"
    r2.font.color.rgb = _rgb(C["muted"])


def _setup_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.clear()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("OMNISHORE QA Agent — Rapport Confidentiel")
    r.font.size = Pt(8); r.italic = True; r.font.name = "Calibri"
    r.font.color.rgb = _rgb(C["navy"])


# data normalisation

def _get_all_results(report_data: dict) -> list:
    """Return a normalised list of result dicts regardless of old/new format."""
    raw = report_data.get("results", {})
    browsers_reported = report_data.get("browsers", [report_data.get("browser", "chromium")])

    if isinstance(raw, list):
        out = []
        for r in raw:
            tc     = r.get("test_case") or {}
            status = (r.get("status") or "unknown").upper()
            out.append({
                "id":          tc.get("id", "?"),
                "title":       tc.get("description", ""),
                "description": tc.get("description", ""),
                "expected":    tc.get("expected", ""),
                "steps":       tc.get("steps", []),
                "type":        tc.get("type", "standard"),
                "overall":     status,
                "browsers_list": [{
                    "browser":                    browsers_reported[0] if browsers_reported else "chromium",
                    "status":                     status,
                    "error":                      r.get("error") or "",
                    "screenshot":                 r.get("screenshot") or "",
                    "primary_screenshot":         r.get("primary_screenshot") or "",
                    "primary_screenshot_context": r.get("primary_screenshot_context"),
                }],
                "auth_steps":  r.get("steps", []),
                "credentials": r.get("credentials_used"),
            })
        return out

    if isinstance(raw, dict):
        out = []
        for tid, data in raw.items():
            out.append({
                "id":          tid,
                "title":       data.get("title") or data.get("description", ""),
                "description": data.get("description", ""),
                "expected":    data.get("expected", ""),
                "steps":       data.get("steps", []),
                "type":        data.get("type", "standard"),
                "overall":     data.get("overall", "UNKNOWN"),
                "browsers_list": [
                    {
                        "browser":                    b,
                        "status":                     bd.get("status", "UNKNOWN"),
                        "error":                      bd.get("error") or "",
                        "screenshot":                 bd.get("screenshot") or "",
                        "primary_screenshot":         bd.get("primary_screenshot") or "",
                        "primary_screenshot_context": bd.get("primary_screenshot_context"),
                    }
                    for b, bd in data.get("browsers", {}).items()
                ],
                "auth_steps":  data.get("auth_steps", []),
                "credentials": data.get("credentials_used"),
            })
        return out

    return []


def _severity(overall: str, description: str, expected: str) -> tuple:
    text = (description + " " + expected).lower()
    if overall == "PASSED":
        return "N/A", C["neutral"]
    if any(k in text for k in ["sql", "xss", "injection", "script", "sécurité", "security"]):
        return "CRITIQUE — Faille de sécurité potentielle", C["danger"]
    if any(k in text for k in ["connexion", "login", "authentif", "password", "mot de passe"]):
        return "HAUTE — Bloquant sur le flux principal", C["danger"]
    if any(k in text for k in ["vide", "empty", "format", "invalide", "invalid", "validation"]):
        return "MOYENNE — Validation des saisies défaillante", C["warning"]
    return "FAIBLE — Comportement non conforme mineur", C["neutral"]


# section builders

def _cover_page(doc, url, date_fr, browsers_str, total, passed, failed, partial, rate, plan_only):
    cover = doc.add_table(rows=1, cols=1)
    cover.style = "Table Grid"
    cc = cover.cell(0, 0)
    _cell_bg(cc, C["navy"])

    def _cp(text, size, bold=True, color=C["white"], before=0, after=6, align=WD_ALIGN_PARAGRAPH.CENTER):
        p = cc.add_paragraph() if cc.paragraphs else cc.paragraphs[0]
        p = cc.add_paragraph()
        p.alignment = align
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after  = Pt(after)
        r = p.add_run(text)
        r.bold = bold; r.font.size = Pt(size); r.font.name = "Calibri"
        r.font.color.rgb = _rgb(color)

    # Logo-style title
    p0 = cc.paragraphs[0]
    p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p0.paragraph_format.space_before = Pt(36)
    p0.paragraph_format.space_after  = Pt(2)
    r0 = p0.add_run("OMNISHORE")
    r0.bold = True; r0.font.size = Pt(36); r0.font.name = "Calibri"
    r0.font.color.rgb = _rgb(C["white"])

    _cp("QA Agent", 14, bold=False, color="94A3B8", before=0, after=4)

    _cp("─" * 50, 10, bold=False, color="1E3A6A", before=4, after=4)

    _cp("RAPPORT DE TEST QUALITÉ", 24, before=8, after=6)
    _cp("Tests Automatisés Générés par Intelligence Artificielle", 11,
        bold=False, color="94A3B8", before=0, after=20)

    _cp("─" * 50, 10, bold=False, color="1E3A6A", before=0, after=12)

    meta = [
        ("Application testée", url[:80] + ("…" if len(url) > 80 else "")),
        ("Date d'exécution",    date_fr),
        ("Navigateur(s)",       browsers_str),
        ("Statut global",       "PLAN UNIQUEMENT (non exécuté)" if plan_only else "EXÉCUTÉ"),
        ("Résultats",           f"{passed} réussis / {total} tests — Taux : {rate}"),
        ("Outil",               "OMNISHORE QA Agent — Playwright + Groq LLM"),
    ]
    for label, value in meta:
        mp = cc.add_paragraph()
        mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        mp.paragraph_format.space_before = Pt(3)
        mp.paragraph_format.space_after  = Pt(3)
        ml = mp.add_run(f"{label} : ")
        ml.bold = True; ml.font.size = Pt(9.5); ml.font.name = "Calibri"
        ml.font.color.rgb = _rgb("64748B")
        mv = mp.add_run(value)
        mv.font.size = Pt(9.5); mv.font.name = "Calibri"
        mv.font.color.rgb = _rgb("E2E8F0")

    pe = cc.add_paragraph()
    pe.paragraph_format.space_after = Pt(36)

    doc.add_page_break()


def _toc(doc):
    _section_title(doc, "0", "Table des Matières")
    sections = [
        ("1", "Introduction et Contexte",           "3"),
        ("2", "Environnement de Test",               "4"),
        ("3", "Résumé Exécutif",                     "4"),
        ("4", "Méthodologie et Approche de Test",    "5"),
        ("5", "Résultats Détaillés des Cas de Test", "6"),
        ("6", "Analyse des Défauts",                 "—"),
        ("7", "Tests de Sécurité",                   "—"),
        ("8", "Matrice Cross-Browser",               "—"),
        ("9", "Recommandations Prioritisées",        "—"),
        ("10","Conclusion et Verdict Final",          "—"),
    ]
    tbl = doc.add_table(rows=len(sections), cols=3)
    tbl.style = "Table Grid"
    for ri, (num, title, page) in enumerate(sections):
        rc  = tbl.rows[ri].cells
        alt = ri % 2 == 0
        bg  = C["light"] if alt else C["white"]
        _cell_bg(rc[0], bg); _cell_bg(rc[1], bg); _cell_bg(rc[2], bg)
        _cell_border(rc[0], "E2E8F0"); _cell_border(rc[1], "E2E8F0"); _cell_border(rc[2], "E2E8F0")
        r0 = rc[0].paragraphs[0].add_run(num)
        r0.bold = True; r0.font.size = Pt(9); r0.font.name = "Calibri"
        r0.font.color.rgb = _rgb(C["accent"])
        r1 = rc[1].paragraphs[0].add_run(title)
        r1.font.size = Pt(9); r1.font.name = "Calibri"
        r1.font.color.rgb = _rgb(C["dark"])
        r2 = rc[2].paragraphs[0].add_run(page)
        r2.font.size = Pt(9); r2.font.name = "Calibri"
        r2.font.color.rgb = _rgb(C["muted"])
        rc[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()
    doc.add_page_break()


def _intro(doc, url, date_fr, browsers_str):
    _section_title(doc, "1", "Introduction et Contexte")

    _subsection(doc, "1.1  Contexte et objectifs")
    _para(doc,
        "Le présent rapport constitue la synthèse complète de la campagne de tests fonctionnels "
        "automatisés menée sur l'application cible par l'agent OMNISHORE QA Agent. "
        "L'objectif principal est de vérifier la conformité des fonctionnalités de l'interface "
        "utilisateur par rapport aux comportements attendus, d'identifier les anomalies présentes, "
        "de mesurer la robustesse de l'application face à des entrées valides et invalides, "
        "et de fournir une base de décision objective pour la validation ou le blocage de la mise "
        "en production.",
        size=9.5, color=C["slate"], before=2, after=6)

    _subsection(doc, "1.2  Application testée")
    _info_box(doc, [
        ("URL testée",      url),
        ("Date",            date_fr),
        ("Navigateur(s)",   browsers_str),
        ("Outil",           "OMNISHORE QA Agent v2.0"),
    ], bg=C["light"], border=C["navy"])

    _subsection(doc, "1.3  Périmètre des tests")
    _para(doc,
        "Les tests ont été générés et exécutés de manière entièrement automatisée. "
        "Le périmètre inclut les tests de validation des champs (formats, longueurs, "
        "caractères spéciaux), les tests aux limites, les tests de flux d'authentification "
        "complets (connexion valide, connexion invalide, déconnexion), les tests CRUD "
        "(création, modification, suppression d'enregistrements) et les tests de compatibilité "
        "multi-navigateurs lorsque plusieurs navigateurs sont sélectionnés.",
        size=9.5, color=C["slate"], before=2, after=4)

    scope_data = [
        ("Dans le périmètre",
         "Authentification · Validation des champs · Tests CRUD · "
         "Tests négatifs (mauvais identifiants, champs vides) · Compatibilité navigateurs"),
        ("Hors périmètre",
         "Tests de performance · Tests de charge · Accessibilité WCAG · "
         "Tests API back-end · Tests d'intégration système"),
    ]
    tbl = doc.add_table(rows=len(scope_data), cols=2)
    tbl.style = "Table Grid"
    for ri, (lbl, val) in enumerate(scope_data):
        lc = tbl.rows[ri].cells[0]
        vc = tbl.rows[ri].cells[1]
        _cell_bg(lc, C["navy2"]); _cell_bg(vc, C["gray_bg"])
        _cell_border(lc, C["navy"]); _cell_border(vc, "E2E8F0")
        lr = lc.paragraphs[0].add_run(lbl)
        lr.bold = True; lr.font.size = Pt(8.5); lr.font.name = "Calibri"
        lr.font.color.rgb = _rgb(C["white"])
        vr = vc.paragraphs[0].add_run(val)
        vr.font.size = Pt(8.5); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["slate"])
    doc.add_paragraph()


def _environment(doc, url, browsers_str, date_fr):
    _section_title(doc, "2", "Environnement de Test")
    env_rows = [
        ("URL testée",             url),
        ("Navigateur(s)",          browsers_str),
        ("Mode d'exécution",       "Headless (sans interface graphique)"),
        ("Outil d'automatisation", "Playwright (Python asyncio)"),
        ("Modèle IA — tests",      "Groq — Llama 3.3 70B Versatile"),
        ("Modèle IA — conclusion", "Groq — Llama 3.3 70B Versatile"),
        ("Outil de test",          "OMNISHORE QA Agent v2.0"),
        ("Date d'exécution",       date_fr),
        ("Rapport généré le",      datetime.now().strftime("%d/%m/%Y à %H:%M:%S")),
    ]
    tbl = doc.add_table(rows=len(env_rows), cols=2)
    tbl.style = "Table Grid"
    for ri, (k, v) in enumerate(env_rows):
        kc = tbl.rows[ri].cells[0]
        vc = tbl.rows[ri].cells[1]
        _cell_bg(kc, C["light"]); _cell_bg(vc, C["white"])
        _cell_border(kc, "E2E8F0"); _cell_border(vc, "E2E8F0")
        kr = kc.paragraphs[0].add_run(k)
        kr.bold = True; kr.font.size = Pt(9); kr.font.name = "Calibri"
        kr.font.color.rgb = _rgb(C["navy"])
        vr = vc.paragraphs[0].add_run(v)
        vr.font.size = Pt(9); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["slate"])
    doc.add_paragraph()


def _executive_summary(doc, total, passed, failed, partial, rate, plan_only, is_multi,
                        report_data, results):
    _section_title(doc, "3", "Résumé Exécutif")

    _scorecard(doc, total, passed, failed, partial, rate)

    _subsection(doc, "3.1  Évaluation globale")
    if plan_only:
        msg = (
            f"Ce rapport présente un plan de test fonctionnel généré automatiquement. "
            f"{total} cas de test ont été planifiés et sont prêts à être exécutés sur une URL cible. "
            "Aucune exécution n'a été réalisée dans cette session."
        )
    elif total == 0:
        msg = "Aucun test n'a été exécuté dans cette session."
    elif failed + partial == 0:
        msg = (
            f"Résultat excellent : la totalité des {total} cas de test ont été exécutés avec succès. "
            "L'application présente un comportement parfaitement conforme aux spécifications pour "
            "l'ensemble des scénarios testés, incluant les cas limites et les vecteurs de sécurité. "
            "Aucune anomalie bloquante n'a été détectée. La mise en production peut être envisagée "
            "avec confiance sous réserve de compléter les tests de performance hors périmètre."
        )
    elif total and passed / total >= 0.8:
        msg = (
            f"Résultat satisfaisant : {passed} tests réussis sur {total} ({rate}). "
            f"{failed + partial} anomalie(s) ont été identifiées et nécessitent une attention "
            "avant tout déploiement en production. Les fonctionnalités principales fonctionnent "
            "correctement. Des corrections ciblées permettront d'atteindre une qualité optimale."
        )
    else:
        msg = (
            f"Résultat insuffisant : {failed + partial} anomalie(s) sur {total} tests "
            f"({rate} de réussite). Des corrections significatives sont requises avant toute "
            "mise en production. Veuillez consulter les sections Analyse des Défauts et "
            "Recommandations pour le plan d'action."
        )
    _para(doc, msg, size=9.5, color=C["slate"], before=2, after=8)

    _subsection(doc, "3.2  Répartition des résultats")
    dist_tbl = doc.add_table(rows=2, cols=4)
    dist_tbl.style = "Table Grid"
    dist_data = [
        ("Réussis",  passed,  C["success"],  C["success_bg"]),
        ("Échoués",  failed,  C["danger"],   C["danger_bg"]),
        ("Partiels", partial, C["warning"],  C["warning_bg"]),
        ("Planifiés",max(0, total - passed - failed - partial), C["accent"], "E0E7FF"),
    ]
    for ci, (lbl, cnt, fg, bg) in enumerate(dist_data):
        hc = dist_tbl.rows[0].cells[ci]
        vc = dist_tbl.rows[1].cells[ci]
        _cell_bg(hc, bg); _cell_bg(vc, C["white"])
        _cell_border(hc, fg); _cell_border(vc, "E2E8F0")
        hr = hc.paragraphs[0].add_run(lbl)
        hr.bold = True; hr.font.size = Pt(9); hr.font.name = "Calibri"
        hr.font.color.rgb = _rgb(fg)
        hc.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        vr = vc.paragraphs[0].add_run(f"{cnt}  ({int(cnt/total*100) if total else 0} %)")
        vr.font.size = Pt(9); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["slate"])
        vc.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()

    if is_multi:
        _subsection(doc, "3.3  Résultats par navigateur")
        browsers_tested = report_data.get("browsers", [])
        if browsers_tested:
            bt_tbl = doc.add_table(rows=len(browsers_tested) + 1, cols=4)
            bt_tbl.style = "Table Grid"
            for ci, hdr in enumerate(["Navigateur", "Réussis", "Échoués", "Partiels"]):
                c = bt_tbl.rows[0].cells[ci]
                _cell_bg(c, C["navy2"])
                r = c.paragraphs[0].add_run(hdr)
                r.bold = True; r.font.size = Pt(8.5); r.font.name = "Calibri"
                r.font.color.rgb = _rgb(C["white"])
            for ri, bt in enumerate(browsers_tested, start=1):
                bt_pass = sum(1 for res in results for bl in res["browsers_list"]
                              if bl["browser"] == bt and bl["status"] == "PASSED")
                bt_fail = sum(1 for res in results for bl in res["browsers_list"]
                              if bl["browser"] == bt and bl["status"] == "FAILED")
                bt_part = sum(1 for res in results for bl in res["browsers_list"]
                              if bl["browser"] == bt and bl["status"] == "PARTIAL")
                row = bt_tbl.rows[ri].cells
                _cell_bg(row[0], C["light"])
                row[0].paragraphs[0].add_run(bt.capitalize()).font.size = Pt(9)
                for ci2, (cnt2, fg2) in enumerate(
                    [(bt_pass, C["success"]), (bt_fail, C["danger"]), (bt_part, C["warning"])],
                    start=1
                ):
                    _cell_bg(row[ci2], C["gray_bg"])
                    p2 = row[ci2].paragraphs[0]
                    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    rn = p2.add_run(str(cnt2))
                    rn.bold = True; rn.font.size = Pt(9); rn.font.name = "Calibri"
                    rn.font.color.rgb = _rgb(fg2)
            doc.add_paragraph()


def _methodology(doc):
    _section_title(doc, "4", "Méthodologie et Approche de Test")

    _subsection(doc, "4.1  Cycle de test OMNISHORE")
    _para(doc,
        "L'agent OMNISHORE QA Agent suit un cycle de test autonome en cinq phases distinctes, "
        "de l'observation initiale de l'interface jusqu'à la génération du rapport final. "
        "Cette approche garantit une couverture maximale avec un minimum d'intervention humaine.",
        size=9.5, color=C["slate"], before=2, after=6)

    steps_desc = [
        ("OBSERVER",
         "L'agent navigue automatiquement vers l'URL cible en mode headless et extrait l'ensemble "
         "des champs de formulaire présents sur la page (identifiant, nom, type, placeholder). "
         "Pour les SPAs Angular/React/Vue, des mécanismes d'attente progressive garantissent que "
         "le DOM est entièrement rendu avant l'extraction des éléments interactifs."),
        ("PLANIFIER",
         "Le modèle de langage Llama 3.1 (via Groq) analyse les champs détectés et génère une "
         "batterie complète de cas de test couvrant les chemins nominaux, les cas aux limites, "
         "les scénarios de sécurité (injection SQL, XSS) et les tests d'authentification. "
         "Pour les spécifications documentées, une génération en 3 passes assure une traçabilité "
         "totale entre les exigences et les cas de test."),
        ("EXÉCUTER",
         "Playwright remplit les formulaires, soumet les données et interagit avec l'interface "
         "de manière automatisée. Les tests d'authentification suivent un flux complet en "
         "7 étapes : inscription, vérification, navigation, connexion valide, connexion invalide, "
         "vérification et déconnexion. L'exécution multi-navigateurs est réalisée en parallèle "
         "via asyncio.gather pour optimiser les temps d'exécution."),
        ("VÉRIFIER",
         "L'agent analyse l'URL résultante, les messages d'erreur affichés (via sélecteurs CSS "
         "sémantiques et analyse textuelle) et l'état de la page pour déterminer si le "
         "comportement observé correspond au résultat attendu. Des mécanismes de retry avec "
         "délai progressif gèrent les latences réseau."),
        ("RAPPORTER",
         "Un rapport JSON structuré est persisté dans le répertoire output/, et le présent "
         "rapport Word est généré automatiquement avec captures d'écran, statuts détaillés, "
         "analyse des défauts et recommandations prioritisées. En cas d'échec, des cartes "
         "Trello sont créées automatiquement pour le suivi des anomalies."),
    ]
    meth_tbl = doc.add_table(rows=len(steps_desc), cols=2)
    meth_tbl.style = "Table Grid"
    for ri, (step, desc) in enumerate(steps_desc):
        lc = meth_tbl.rows[ri].cells[0]
        vc = meth_tbl.rows[ri].cells[1]
        _cell_bg(lc, C["navy"]); _cell_bg(vc, C["gray_bg"])
        _cell_border(lc, C["navy"]); _cell_border(vc, "E2E8F0")
        lr = lc.paragraphs[0].add_run(step)
        lr.bold = True; lr.font.size = Pt(9); lr.font.name = "Calibri"
        lr.font.color.rgb = _rgb(C["white"])
        lc.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        vr = vc.paragraphs[0].add_run(desc)
        vr.font.size = Pt(8.5); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["slate"])
    doc.add_paragraph()

    _subsection(doc, "4.2  Catégories de tests couverts")
    cats = [
        ("Tests d'authentification", "Connexion avec identifiants valides, connexion invalide (mauvais mot de passe, champs vides), déconnexion complète."),
        ("Tests de validation",      "Champs vides, formats invalides, longueurs extrêmes (> 200 caractères), caractères spéciaux et encodages inhabituels."),
        ("Tests aux limites",        "Valeurs minimales et maximales, espaces seuls, chaînes vides, caractères Unicode."),
        ("Tests négatifs",           "Identifiants erronés, mots de passe incorrects, combinaisons invalides multiples simultanées."),
        ("Tests CRUD",               "Création, modification et suppression d'enregistrements (employés, utilisateurs, congés) via l'interface."),
        ("Tests de compatibilité",   "Exécution parallèle sur Chromium, Firefox, WebKit, Chrome, Edge pour détecter les incohérences de rendu."),
    ]
    cat_tbl = doc.add_table(rows=len(cats), cols=2)
    cat_tbl.style = "Table Grid"
    for ri, (cat, desc) in enumerate(cats):
        lc = cat_tbl.rows[ri].cells[0]
        vc = cat_tbl.rows[ri].cells[1]
        _cell_bg(lc, C["light"]); _cell_bg(vc, C["white"])
        _cell_border(lc, "E2E8F0"); _cell_border(vc, "E2E8F0")
        lr = lc.paragraphs[0].add_run(cat)
        lr.bold = True; lr.font.size = Pt(9); lr.font.name = "Calibri"
        lr.font.color.rgb = _rgb(C["navy"])
        vr = vc.paragraphs[0].add_run(desc)
        vr.font.size = Pt(9); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(C["slate"])
    doc.add_paragraph()

    _subsection(doc, "4.3  Génération des données de test")
    _para(doc,
        "Les données de test sont générées par le modèle LLM Llama 3.1 8B Instant (Groq API) "
        "à partir de l'analyse des champs de formulaire détectés. Un mécanisme de récupération "
        "automatique des réponses JSON tronquées garantit la robustesse de la génération, même "
        "en cas de limitations de tokens. Pour les tests d'authentification, des identifiants "
        "aléatoires non persistés sont générés à chaque session afin d'éviter les collisions "
        "et de garantir l'isolation des tests.",
        size=9.5, color=C["slate"], before=2, after=4)

    _para(doc,
        "Pour les tests CRUD, l'agent localise les lignes dans les tableaux par texte visible "
        "et clique les icônes d'action (corbeille pour suppression, crayon pour modification). "
        "Tout comportement non conforme détecté est automatiquement classé par sévérité dans le rapport.",
        size=9.5, color=C["slate"], before=2, after=8)


def _test_card(doc, res: dict, index: int, seen_screenshots: set) -> None:
    """Render one test case.  All tables go to doc level — no nested table mixing.

    seen_screenshots: mutable set of already-inserted screenshot paths; used to
    deduplicate images across the report.
    """
    overall        = res.get("overall", "UNKNOWN")
    bg             = STATUS_BG.get(overall, "F8FAFC")
    fg             = STATUS_FG.get(overall, C["muted"])
    label          = STATUS_FR.get(overall, overall)
    sev, sev_color = _severity(overall, res.get("description", ""), res.get("expected", ""))
    is_auth        = res.get("type") == "auth_flow"
    browsers_list  = res.get("browsers_list", [])

    # Collect actual step-level results (needed in sections 3 and 6)
    exec_results = next(
        (bl.get("step_results", []) for bl in browsers_list if bl.get("step_results")),
        []
    )
    exec_by_idx = {sr.get("step", i + 1): sr for i, sr in enumerate(exec_results)}
    has_exec    = bool(exec_results)

    # 1. header table (id + description | status badge)
    h_tbl = doc.add_table(rows=1, cols=2)
    h_tbl.style = "Table Grid"
    lc = h_tbl.cell(0, 0)
    rc = h_tbl.cell(0, 1)
    _cell_bg(lc, C["navy2"]); _cell_bg(rc, bg)
    _cell_border(lc, fg, "8"); _cell_border(rc, fg, "8")

    lp = lc.paragraphs[0]
    lp.paragraph_format.space_before = Pt(5)
    lp.paragraph_format.space_after  = Pt(5)
    id_r = lp.add_run(f"#{index:02d}  {res.get('id', '?')}   ")
    id_r.bold = True; id_r.font.size = Pt(8); id_r.font.name = "Calibri"
    id_r.font.color.rgb = _rgb(C["neutral"])
    desc_r = lp.add_run(res.get("title") or res.get("description", ""))
    desc_r.bold = True; desc_r.font.size = Pt(10); desc_r.font.name = "Calibri"
    desc_r.font.color.rgb = _rgb(C["white"])

    rp = rc.paragraphs[0]
    rp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rp.paragraph_format.space_before = Pt(5)
    rp.paragraph_format.space_after  = Pt(5)
    badge = rp.add_run(f"● {label}")
    badge.bold = True; badge.font.size = Pt(11); badge.font.name = "Calibri"
    badge.font.color.rgb = _rgb(fg)

    # 2. info table (type / expected / severity) — doc level, not nested
    info_rows = [
        ("Type de test",      "Flux d'authentification complet" if is_auth else "Test fonctionnel"),
        ("Résultat attendu",  res.get("expected") or "—"),
        ("Sévérité",          sev),
    ]
    info_tbl = doc.add_table(rows=len(info_rows), cols=2)
    info_tbl.style = "Table Grid"
    for ri, (k, v) in enumerate(info_rows):
        kc = info_tbl.rows[ri].cells[0]
        vc = info_tbl.rows[ri].cells[1]
        _cell_bg(kc, C["light"]); _cell_bg(vc, C["white"])
        _cell_border(kc, "E2E8F0"); _cell_border(vc, "E2E8F0")
        kr = kc.paragraphs[0].add_run(k)
        kr.bold = True; kr.font.size = Pt(8.5); kr.font.name = "Calibri"
        kr.font.color.rgb = _rgb(C["muted"])
        vr = vc.paragraphs[0].add_run(_safe(v))
        vr.font.size = Pt(8.5); vr.font.name = "Calibri"
        vr.font.color.rgb = _rgb(sev_color if ri == 2 else C["slate"])

    # 3. steps table — show actual execution results
    steps = res.get("steps", [])

    if steps and not is_auth:
        is_narrative = any(s.get("action") == "describe" for s in steps)
        label = "Scénario narratif :" if is_narrative else "Étapes d'exécution (statut réel) :"
        _subsection(doc, label)

        if is_narrative:
            for si, step in enumerate(steps, 1):
                action = step.get("action", "describe")
                value  = _safe(step.get("value") or "")
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after  = Pt(2)
                p.paragraph_format.left_indent  = Pt(14)
                idx_r = p.add_run(f"{si}.  ")
                idx_r.bold = True; idx_r.font.size = Pt(8.5); idx_r.font.name = "Calibri"
                idx_r.font.color.rgb = _rgb(C["accent"])
                if action == "wait":
                    vr2 = p.add_run(f"[Délai : {value} s]")
                    vr2.italic = True; vr2.font.size = Pt(8.5)
                    vr2.font.color.rgb = _rgb(C["neutral"])
                else:
                    vr2 = p.add_run(value)
                    vr2.font.size = Pt(8.5); vr2.font.name = "Calibri"
                    vr2.font.color.rgb = _rgb(C["slate"])
        else:
            ncols = 5 if has_exec else 4
            hdrs  = ["#", "Action", "Champ / Cible", "Valeur"]
            if has_exec:
                hdrs.append("Résultat")

            st_tbl = doc.add_table(rows=len(steps) + 1, cols=ncols)
            st_tbl.style = "Table Grid"
            for ci, hdr in enumerate(hdrs):
                c = st_tbl.rows[0].cells[ci]
                _cell_bg(c, C["navy2"])
                r = c.paragraphs[0].add_run(hdr)
                r.bold = True; r.font.size = Pt(8); r.font.name = "Calibri"
                r.font.color.rgb = _rgb(C["white"])

            for si, step in enumerate(steps, 1):
                row    = st_tbl.rows[si].cells
                ex     = exec_by_idx.get(si, {})
                st_val = (ex.get("status") or "planned").upper()
                st_bg  = STATUS_BG.get(st_val, C["light"]) if has_exec else C["white"]
                st_fg  = STATUS_FG.get(st_val, C["muted"]) if has_exec else C["muted"]
                st_lbl = STATUS_FR.get(st_val, st_val) if has_exec else "—"
                err    = _safe(ex.get("error") or "")[:80]

                _cell_bg(row[0], C["light"])
                for ci2 in range(1, ncols):
                    _cell_bg(row[ci2], C["white"])

                row[0].paragraphs[0].add_run(str(si)).font.size = Pt(8)
                row[1].paragraphs[0].add_run(_safe(step.get("action", "—"))).font.size = Pt(8)

                # Champ / Cible: show row field for click_row_action
                field_val = (
                    step.get("row") or step.get("field") or
                    step.get("text") or step.get("selector") or "—"
                )
                row[2].paragraphs[0].add_run(_safe(field_val)[:60]).font.size = Pt(8)
                row[3].paragraphs[0].add_run(_safe(step.get("value", "—"))[:60]).font.size = Pt(8)

                if has_exec:
                    _cell_bg(row[4], st_bg)
                    sr_p = row[4].paragraphs[0]
                    sr_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    sr_run = sr_p.add_run(st_lbl)
                    sr_run.bold = True; sr_run.font.size = Pt(7.5); sr_run.font.name = "Calibri"
                    sr_run.font.color.rgb = _rgb(st_fg)
                    # Show error below if step failed
                    if err and st_val == "FAILED":
                        ep = row[4].add_paragraph()
                        er2 = ep.add_run(err)
                        er2.font.size = Pt(7); er2.italic = True
                        er2.font.color.rgb = _rgb(C["danger"])
        doc.add_paragraph()

    # 4. auth sub-steps table — doc level
    auth_steps = res.get("auth_steps", [])
    if is_auth and auth_steps:
        _subsection(doc, "Déroulement du flux d'authentification :")
        a_tbl = doc.add_table(rows=len(auth_steps) + 1, cols=4)
        a_tbl.style = "Table Grid"
        for ci, hdr in enumerate(["Étape", "Nom", "Statut", "Observation"]):
            c = a_tbl.rows[0].cells[ci]
            _cell_bg(c, C["indigo"])
            r = c.paragraphs[0].add_run(hdr)
            r.bold = True; r.font.size = Pt(8); r.font.name = "Calibri"
            r.font.color.rgb = _rgb(C["white"])
            c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for si, step in enumerate(auth_steps, 1):
            st   = (step.get("status") or "unknown").upper()
            sbg  = STATUS_BG.get(st, C["gray_bg"])
            sfg  = STATUS_FG.get(st, C["muted"])
            row  = a_tbl.rows[si].cells
            _cell_bg(row[0], C["light"]); _cell_bg(row[1], sbg)
            _cell_bg(row[2], sbg);        _cell_bg(row[3], C["white"])
            row[0].paragraphs[0].add_run(str(si)).font.size = Pt(8)
            row[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            step_name = STEP_FR.get(step.get("step", ""), step.get("step", "").replace("_", " ").title())
            row[1].paragraphs[0].add_run(step_name).font.size = Pt(8)
            sr2 = row[2].paragraphs[0].add_run(STATUS_FR.get(st, st))
            sr2.bold = True; sr2.font.size = Pt(8); sr2.font.name = "Calibri"
            sr2.font.color.rgb = _rgb(sfg)
            row[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            row[3].paragraphs[0].add_run((step.get("note") or "")[:150]).font.size = Pt(7.5)
        doc.add_paragraph()

    # 5. per-browser results table — doc level
    if browsers_list:
        _subsection(doc, "Résultats par navigateur :")
        bw_tbl = doc.add_table(rows=len(browsers_list) + 1, cols=3)
        bw_tbl.style = "Table Grid"
        for ci, hdr in enumerate(["Navigateur", "Statut", "Erreur / Observation"]):
            c = bw_tbl.rows[0].cells[ci]
            _cell_bg(c, C["navy2"])
            r = c.paragraphs[0].add_run(hdr)
            r.bold = True; r.font.size = Pt(8); r.font.name = "Calibri"
            r.font.color.rgb = _rgb(C["white"])
        for bi, bl in enumerate(browsers_list, 1):
            bst = (bl.get("status") or "UNKNOWN").upper()
            bbg = STATUS_BG.get(bst, C["gray_bg"])
            bfg = STATUS_FG.get(bst, C["muted"])
            row = bw_tbl.rows[bi].cells
            _cell_bg(row[0], C["light"]); _cell_bg(row[1], bbg); _cell_bg(row[2], C["white"])
            row[0].paragraphs[0].add_run(bl.get("browser", "—").capitalize()).font.size = Pt(8.5)
            sr3 = row[1].paragraphs[0].add_run(STATUS_FR.get(bst, bst))
            sr3.bold = True; sr3.font.size = Pt(8.5); sr3.font.name = "Calibri"
            sr3.font.color.rgb = _rgb(bfg)
            row[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            er = row[2].paragraphs[0].add_run(_safe(bl.get("error") or "—")[:120])
            er.font.size = Pt(7.5); er.italic = True; er.font.name = "Calibri"
            er.font.color.rgb = _rgb(C["danger"] if bst == "FAILED" else C["slate"])
        doc.add_paragraph()

    # 6. result summary line
    result_p = doc.add_paragraph()
    result_p.paragraph_format.space_before = Pt(2)
    result_p.paragraph_format.space_after  = Pt(4)
    lr = result_p.add_run("Résultat obtenu : ")
    lr.bold = True; lr.font.size = Pt(8.5); lr.font.name = "Calibri"
    lr.font.color.rgb = _rgb(C["muted"])

    if overall == "PASSED":
        result_text = "Test réussi — comportement conforme au résultat attendu."
        res_color   = C["success"]
    elif overall == "PARTIAL":
        # Find which specific steps failed
        failed_steps = [
            sr for sr in exec_results if (sr.get("status") or "").upper() == "FAILED"
        ]
        if failed_steps:
            details = "; ".join(
                f"step {s.get('step')} ({s.get('action','?')}): {(s.get('error') or '')[:80]}"
                for s in failed_steps[:3]
            )
            result_text = f"Partiel — actions exécutées, vérification échouée. Détail : {details}"
        else:
            result_text = "Partiel — certaines étapes n'ont pas pu être vérifiées."
        res_color = C["warning"]
    else:
        err_msg = next((bl.get("error") for bl in browsers_list if bl.get("error")), None)
        result_text = err_msg[:150] if err_msg else "Test échoué — voir détails ci-dessus."
        res_color = C["danger"]

    vr_res = result_p.add_run(result_text)
    vr_res.font.size  = Pt(8.5); vr_res.font.name = "Calibri"
    vr_res.italic     = overall != "PASSED"
    vr_res.font.color.rgb = _rgb(res_color)

    # screenshot: prefer primary (failure point or final state), fall back to any available
    primary_bl = next(
        (bl for bl in browsers_list
         if bl.get("primary_screenshot")
         and os.path.exists(bl["primary_screenshot"])
         and bl["primary_screenshot"] not in seen_screenshots),
        None,
    )
    if primary_bl:
        screenshot = primary_bl["primary_screenshot"]
        ctx        = primary_bl.get("primary_screenshot_context") or {}
    else:
        # Fallback: any screenshot not yet shown
        fallback_bl = next(
            (bl for bl in browsers_list
             if bl.get("screenshot")
             and os.path.exists(bl["screenshot"])
             and bl["screenshot"] not in seen_screenshots),
            None,
        )
        screenshot = fallback_bl["screenshot"] if fallback_bl else None
        ctx        = {}

    if screenshot:
        seen_screenshots.add(screenshot)
        try:
            doc.add_picture(screenshot, width=Inches(5.5))
            ctx_label = ctx.get("label") or ""
            if ctx_label:
                cap_text = (
                    f"Figure — {res.get('id', '')} · "
                    f"{STATUS_FR.get(overall, overall)} — {ctx_label}"
                )
            else:
                cap_text = f"Figure — {res.get('id', '')} · {STATUS_FR.get(overall, overall)}"
            cap = doc.add_paragraph(cap_text)
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.runs[0].font.size = Pt(7.5)
            cap.runs[0].italic    = True
            cap.runs[0].font.color.rgb = _rgb(C["muted"])
        except Exception as exc:
            logger.warning("Could not insert screenshot %s: %s", screenshot, exc)

    doc.add_paragraph()


def _defect_analysis(doc, failed_results: list):
    _section_title(doc, "6", "Analyse des Défauts")

    if not failed_results:
        _info_box(doc, [
            ("Résultat",   "Aucun défaut détecté lors de cette session de tests."),
            ("Conclusion", "L'application respecte les comportements attendus pour tous les scénarios testés."),
        ], bg=C["success_bg"], border=C["success"])
        return

    _para(doc,
        f"{len(failed_results)} anomalie(s) ont été détectées et sont synthétisées ci-dessous "
        "par ordre de sévérité. Chaque défaut est documenté avec son identifiant, sa description, "
        "la sévérité évaluée, le(s) navigateur(s) affecté(s) et le message d'erreur observé.",
        size=9.5, color=C["slate"], before=2, after=6)

    tbl = doc.add_table(rows=len(failed_results) + 1, cols=5)
    tbl.style = "Table Grid"
    for ci, hdr in enumerate(["ID", "Description", "Statut", "Sévérité", "Erreur observée"]):
        c = tbl.rows[0].cells[ci]
        _cell_bg(c, C["danger"])
        r = c.paragraphs[0].add_run(hdr)
        r.bold = True; r.font.size = Pt(8.5); r.font.name = "Calibri"
        r.font.color.rgb = _rgb(C["white"])

    for ri, res in enumerate(failed_results, start=1):
        overall = res.get("overall", "FAILED")
        sev, sev_color = _severity(overall, res.get("description", ""), res.get("expected", ""))
        err = next(
            (bl.get("error") for bl in res.get("browsers_list", []) if bl.get("error")),
            "—"
        )[:100]
        row = tbl.rows[ri].cells
        _cell_bg(row[0], C["light"]); _cell_bg(row[1], C["white"])
        _cell_bg(row[2], STATUS_BG.get(overall, C["gray_bg"]))
        _cell_bg(row[3], C["white"]); _cell_bg(row[4], C["danger_bg"])
        row[0].paragraphs[0].add_run(res.get("id", "")).font.size = Pt(8.5)
        row[1].paragraphs[0].add_run(res.get("description", "")[:60]).font.size = Pt(8.5)
        sr = row[2].paragraphs[0].add_run(STATUS_FR.get(overall, overall))
        sr.bold = True; sr.font.size = Pt(8.5); sr.font.name = "Calibri"
        sr.font.color.rgb = _rgb(STATUS_FG.get(overall, C["muted"]))
        row[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        svr = row[3].paragraphs[0].add_run(sev[:50])
        svr.font.size = Pt(8); svr.font.color.rgb = _rgb(sev_color); svr.font.name = "Calibri"
        er = row[4].paragraphs[0].add_run(err)
        er.font.size = Pt(7.5); er.italic = True
        er.font.color.rgb = _rgb(C["danger"]); er.font.name = "Calibri"
    doc.add_paragraph()


def _security_section(doc, results: list):
    _section_title(doc, "7", "Tests de Sécurité")

    security_kw = ["sql", "xss", "injection", "script", "sécurité", "security",
                   "alert(", "select ", "union", "drop ", "'or'", "onerror"]
    sec_results = [
        r for r in results
        if any(kw in (r.get("description", "") + r.get("expected", "")).lower()
               for kw in security_kw)
    ]

    _para(doc,
        "Cette section regroupe les cas de test spécifiquement orientés vers la détection de "
        "vulnérabilités de sécurité applicative. Les vecteurs d'attaque testés incluent l'injection "
        "SQL (manipulation de requêtes base de données), le Cross-Site Scripting (XSS, injection "
        "de scripts malveillants dans les champs de saisie) et d'autres vecteurs d'entrées "
        "malveillantes. Tout comportement non conforme sur ces tests représente un risque de "
        "sécurité potentiellement critique nécessitant une correction avant déploiement.",
        size=9.5, color=C["slate"], before=2, after=6)

    if not sec_results:
        _info_box(doc, [
            ("Observation",
             "Aucun cas de test dédié à la sécurité n'a été explicitement identifié dans cette session. "
             "Des tests de sécurité peuvent néanmoins être intégrés dans les cas de test standards "
             "(champs vides, formats invalides). Relancer l'agent avec une spécification détaillée "
             "pour une couverture sécurité exhaustive."),
        ], bg=C["warning_bg"], border=C["warning"])
        return

    tbl = doc.add_table(rows=len(sec_results) + 1, cols=4)
    tbl.style = "Table Grid"
    for ci, hdr in enumerate(["ID", "Type de vulnérabilité", "Statut", "Observations"]):
        c = tbl.rows[0].cells[ci]
        _cell_bg(c, C["navy2"])
        r = c.paragraphs[0].add_run(hdr)
        r.bold = True; r.font.size = Pt(8.5); r.font.name = "Calibri"
        r.font.color.rgb = _rgb(C["white"])

    vuln_types = {
        "sql":      "Injection SQL",
        "xss":      "Cross-Site Scripting (XSS)",
        "script":   "Injection de script",
        "alert(":   "XSS — alert injection",
        "union":    "Injection SQL — UNION",
        "drop ":    "Injection SQL — DROP",
    }
    for ri, res in enumerate(sec_results, start=1):
        overall  = res.get("overall", "UNKNOWN")
        desc     = res.get("description", "").lower()
        vt       = next((v for k, v in vuln_types.items() if k in desc), "Entrée malveillante")
        row      = tbl.rows[ri].cells
        _cell_bg(row[0], C["light"])
        _cell_bg(row[1], C["white"])
        _cell_bg(row[2], STATUS_BG.get(overall, C["gray_bg"]))
        _cell_bg(row[3], C["white"])
        row[0].paragraphs[0].add_run(res.get("id", "")).font.size = Pt(8.5)
        row[1].paragraphs[0].add_run(vt).font.size = Pt(8.5)
        sr = row[2].paragraphs[0].add_run(STATUS_FR.get(overall, overall))
        sr.bold = True; sr.font.size = Pt(8.5); sr.font.name = "Calibri"
        sr.font.color.rgb = _rgb(STATUS_FG.get(overall, C["muted"]))
        row[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        note = "Vecteur correctement rejeté." if overall == "PASSED" else "VECTEUR NON REJETÉ — RISQUE CRITIQUE"
        nr = row[3].paragraphs[0].add_run(note)
        nr.font.size = Pt(8.5); nr.font.name = "Calibri"
        nr.font.color.rgb = _rgb(C["danger"] if overall != "PASSED" else C["success"])
    doc.add_paragraph()


def _cross_browser_matrix(doc, results: list, browsers: list):
    _section_title(doc, "8", "Matrice Cross-Browser")

    if not browsers or len(browsers) <= 1:
        _para(doc, "Test exécuté sur un seul navigateur — matrice non applicable.",
              size=9.5, color=C["muted"], before=2, after=6)
        return

    ncols = 2 + len(browsers)
    tbl   = doc.add_table(rows=len(results) + 2, cols=ncols)
    tbl.style = "Table Grid"

    headers = ["ID", "Description"] + [b.capitalize() for b in browsers]
    for ci, hdr in enumerate(headers):
        c = tbl.rows[0].cells[ci]
        _cell_bg(c, C["navy"])
        r = c.paragraphs[0].add_run(hdr)
        r.bold = True; r.font.size = Pt(8); r.font.name = "Calibri"
        r.font.color.rgb = _rgb(C["white"])

    for ri2, res in enumerate(results, start=1):
        row = tbl.rows[ri2]
        _cell_bg(row.cells[0], C["light"])
        row.cells[0].paragraphs[0].add_run(res.get("id", "")).font.size = Pt(8)
        _cell_bg(row.cells[1], C["white"])
        row.cells[1].paragraphs[0].add_run(res.get("description", "")[:60]).font.size = Pt(8)
        for ci3, bt in enumerate(browsers, start=2):
            bl  = next((b for b in res.get("browsers_list", []) if b["browser"] == bt), {})
            st  = (bl.get("status") or "UNKNOWN").upper()
            cell3 = row.cells[ci3]
            _cell_bg(cell3, STATUS_BG.get(st, C["gray_bg"]))
            p3 = cell3.paragraphs[0]
            p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sr3 = p3.add_run(STATUS_FR.get(st, st)[:4])
            sr3.bold = True; sr3.font.size = Pt(7.5); sr3.font.name = "Calibri"
            sr3.font.color.rgb = _rgb(STATUS_FG.get(st, C["muted"]))

    # Totals row
    tot_row = tbl.rows[len(results) + 1]
    _cell_bg(tot_row.cells[0], C["light"]); _cell_bg(tot_row.cells[1], C["light"])
    tot_row.cells[1].paragraphs[0].add_run("TOTAL RÉUSSIS").font.size = Pt(8)
    for ci4, bt in enumerate(browsers, start=2):
        cnt4 = sum(1 for r in results
                   for bl in r.get("browsers_list", [])
                   if bl["browser"] == bt and bl["status"] == "PASSED")
        cell4 = tot_row.cells[ci4]
        _cell_bg(cell4, C["success_bg"])
        cell4.paragraphs[0].add_run(str(cnt4)).font.size = Pt(9)
        cell4.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()


def _recommendations(doc, failed_results: list, total: int):
    _section_title(doc, "9", "Recommandations Prioritisées")

    if not failed_results:
        _info_box(doc, [
            ("Résultat", "Aucune recommandation corrective — tous les tests ont réussi."),
            ("Conseil",  "Maintenir la couverture de tests à chaque nouvelle version."),
        ], bg=C["success_bg"], border=C["success"])
    else:
        recs = []
        for res in failed_results:
            desc = (res.get("description", "") + " " + res.get("expected", "")).lower()
            err  = next((bl.get("error") for bl in res.get("browsers_list", []) if bl.get("error")), "non précisée")
            tid  = res.get("id", "?")
            if any(k in desc for k in ["sql", "xss", "injection", "script"]):
                recs.insert(0, ("P1 — URGENT", C["danger"], C["danger_bg"],
                    f"Corriger la vulnérabilité de sécurité détectée ({tid})",
                    f"Le test '{tid}' a révélé une faille potentielle (injection ou XSS). "
                    "Implémenter immédiatement la validation et l'échappement des entrées côté "
                    "serveur. Ne pas déployer en production avant correction complète."))
            elif any(k in desc for k in ["connexion", "login", "password", "authentif"]):
                recs.append(("P1 — URGENT", C["danger"], C["danger_bg"],
                    f"Corriger le flux d'authentification ({tid})",
                    f"Le scénario '{res.get('description', '')}' a échoué. "
                    f"Erreur : {str(err)[:150]}. Vérifier la logique de validation des identifiants."))
            elif any(k in desc for k in ["vide", "empty", "format", "invalide", "invalid"]):
                recs.append(("P2 — IMPORTANT", C["warning"], C["warning_bg"],
                    f"Renforcer la validation des saisies ({tid})",
                    f"Le formulaire ne gère pas correctement : '{res.get('description', '')}'. "
                    "Ajouter des contrôles de validation côté client et serveur."))
            else:
                recs.append(("P3 — NORMAL", C["accent"], "E0E7FF",
                    f"Analyser et corriger ({tid})",
                    f"Anomalie sur : '{res.get('description', '')}'. "
                    f"Résultat obtenu : {str(err)[:120]}."))

        for i, (priority, fg, bg, title, detail) in enumerate(recs, start=1):
            r_tbl = doc.add_table(rows=1, cols=1)
            r_tbl.style = "Table Grid"
            rcc = r_tbl.cell(0, 0)
            _cell_bg(rcc, bg); _cell_border(rcc, fg, "8")
            rp = rcc.paragraphs[0]
            rp.paragraph_format.space_before = Pt(4)
            pr = rp.add_run(f"[{priority}]  R{i} — {title}")
            pr.bold = True; pr.font.size = Pt(10); pr.font.name = "Calibri"
            pr.font.color.rgb = _rgb(fg)
            dp = rcc.add_paragraph()
            dp.paragraph_format.space_after = Pt(4)
            dr = dp.add_run(detail)
            dr.font.size = Pt(9); dr.font.name = "Calibri"
            dr.font.color.rgb = _rgb(C["slate"])
            doc.add_paragraph()

    _subsection(doc, "Prochaines étapes recommandées")
    next_steps = [
        "Intégrer l'agent OMNISHORE QA dans la pipeline CI/CD pour exécution automatique à chaque déploiement.",
        "Étendre les tests à d'autres pages et flux (panier, profil, paiement, back-office).",
        "Activer le test multi-navigateurs (Firefox, WebKit, Edge) pour valider la compatibilité cross-platform.",
        "Compléter avec des tests de performance et de charge sur les flux critiques identifiés.",
        "Mettre en place un suivi continu des anomalies via l'intégration Trello / Jira.",
    ]
    if failed_results:
        next_steps.insert(0, "Corriger en priorité les anomalies classées P1 (URGENT) identifiées en section 9.")
    for step in next_steps:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after  = Pt(2)
        r = p.add_run(step)
        r.font.size = Pt(9.5); r.font.name = "Calibri"
        r.font.color.rgb = _rgb(C["slate"])
    doc.add_paragraph()


def _conclusion(doc, report_data: dict, total, passed, failed, partial, rate, plan_only, url):
    _section_title(doc, "10", "Conclusion et Verdict Final")

    # Try LLM-generated conclusion first
    conclusion_text = None
    try:
        from tools.llm import generate_conclusion_with_llm
        conclusion_text = generate_conclusion_with_llm({
            "total_tests": total, "passed": passed, "failed": failed,
            "partial": partial, "url": url,
        })
    except Exception:
        pass

    if not conclusion_text:
        if plan_only:
            conclusion_text = (
                f"Ce document constitue un plan de test fonctionnel généré automatiquement "
                f"à partir du document de spécification fourni. Il recense {total} cas de test "
                "couvrant les principales fonctionnalités décrites. Ces cas sont prêts à être "
                "exécutés dès qu'une URL cible sera spécifiée. Il est recommandé de les intégrer "
                "dans une pipeline d'intégration continue pour un suivi régulier de la qualité."
            )
        elif failed + partial == 0:
            conclusion_text = (
                f"La campagne de tests automatisés menée sur {url} a produit des résultats "
                f"excellents : {passed} cas de test sur {total} ont été validés avec succès, "
                f"soit un taux de réussite de {rate}. Aucune anomalie n'a été identifiée. "
                "L'application démontre un comportement robuste et conforme aux spécifications "
                "pour l'ensemble des scénarios testés, incluant les cas limites et les tests "
                "de sécurité. La mise en production peut être envisagée avec confiance, sous "
                "réserve de la validation des tests de performance hors périmètre de cette campagne."
            )
        else:
            conclusion_text = (
                f"La campagne de tests automatisés sur {url} a permis d'identifier "
                f"{failed + partial} anomalie(s) sur {total} scénarios ({rate} de réussite). "
                f"{passed} cas ont été validés avec succès. Les défauts identifiés ont été "
                "classés par sévérité et des recommandations correctives ont été formulées "
                "en section 9. Il est impératif de traiter en priorité les anomalies P1 "
                "avant toute mise en production. Une campagne de tests de non-régression "
                "devra être conduite après correction pour valider les correctifs apportés."
            )

    _para(doc, conclusion_text, size=10, color=C["slate"], before=2, after=12)

    # Verdict box
    if plan_only:
        verdict, bg, border = "PLAN DE TEST GÉNÉRÉ", C["light"], C["navy"]
    elif failed + partial == 0:
        verdict, bg, border = "VERDICT : APPROUVÉ POUR MISE EN PRODUCTION", C["success_bg"], C["success"]
    elif total and passed / total >= 0.8:
        verdict, bg, border = "VERDICT : CORRECTIONS MINEURES REQUISES", C["warning_bg"], C["warning"]
    else:
        verdict, bg, border = "VERDICT : MISE EN PRODUCTION BLOQUÉE", C["danger_bg"], C["danger"]

    _info_box(doc, [("", verdict)], bg=bg, border=border)

    _hr(doc)
    fp = doc.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = fp.add_run(
        f"Rapport généré automatiquement par OMNISHORE QA Agent   ·   "
        f"{total} tests   ·   Taux de réussite : {rate}"
    )
    fr.font.size = Pt(8); fr.italic = True; fr.font.name = "Calibri"
    fr.font.color.rgb = _rgb(C["muted"])


# main entry point

def generate_word_report(report_data: dict, browser: str = "chromium",
                          output_dir: str | None = None) -> str:
    """Generate complete OMNISHORE QA Word report. Returns path to saved .docx file."""
    from config import REPORT_DIR
    if output_dir is None:
        output_dir = REPORT_DIR
    doc = Document()

    # Page setup
    for section in doc.sections:
        section.top_margin    = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)
        section.different_first_page_header_footer = True

    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    # Setup header/footer for all pages (except cover = first page)
    url = report_data.get("url", "")
    _setup_header(doc.sections[0], url)
    _setup_footer(doc.sections[0])

    # Metadata
    ts = report_data.get("timestamp", datetime.now().isoformat())
    try:
        dt = datetime.fromisoformat(ts)
        date_fr = dt.strftime("%d %B %Y  —  %H:%M")
    except Exception:
        date_fr = ts

    total     = report_data.get("total_tests", 0)
    passed    = report_data.get("passed", 0)
    failed    = report_data.get("failed", 0)
    partial   = report_data.get("partial", 0)
    rate      = f"{int(passed / total * 100)} %" if total else "N/A"
    plan_only = report_data.get("plan_only", False)
    is_multi  = report_data.get("multi_browser", False)
    browsers  = report_data.get("browsers") or [browser]
    browsers_str = ", ".join(b.capitalize() for b in browsers)

    raw_results = _get_all_results(report_data)

    # Deduplicate by test ID
    seen_ids:         set[str] = set()
    deduped:          list     = []
    seen_screenshots: set[str] = set()

    for r in raw_results:
        tid = r.get("id", "?")
        if tid in seen_ids:
            logger.warning("Duplicate test ID '%s' — skipping.", tid)
            continue
        seen_ids.add(tid)
        deduped.append(r)

    # Sort sequentially: auth_flow first, then by numeric ID (TC-001, TC-002…)
    def _sort_key(r):
        tid = r.get("id", "")
        if "auth_flow" in tid.lower():
            return (0, 0, tid)
        m = _re.search(r'\d+', tid)
        return (1, int(m.group()) if m else 9999, tid)

    results: list = sorted(deduped, key=_sort_key)

    failed_res = [r for r in results if r.get("overall") in ("FAILED", "PARTIAL")]

    _cover_page(doc, url, date_fr, browsers_str, total, passed, failed, partial, rate, plan_only)
    _toc(doc)

    _intro(doc, url, date_fr, browsers_str)
    doc.add_page_break()

    _environment(doc, url, browsers_str, date_fr)
    doc.add_page_break()

    _executive_summary(doc, total, passed, failed, partial, rate,
                        plan_only, is_multi, report_data, results)
    doc.add_page_break()

    _methodology(doc)
    doc.add_page_break()

    _section_title(doc, "5", "Résultats Détaillés des Cas de Test")
    _para(doc,
        f"Cette section présente le détail complet de chacun des {len(results)} cas de test exécutés, "
        "incluant les actions effectuées, le résultat attendu, le résultat obtenu par navigateur, "
        "la sévérité de l'anomalie le cas échéant, et les captures d'écran associées.",
        size=9.5, color=C["slate"], before=2, after=8)
    for idx, res in enumerate(results, start=1):
        _test_card(doc, res, idx, seen_screenshots)
    doc.add_page_break()

    _defect_analysis(doc, failed_res)
    doc.add_page_break()

    _security_section(doc, results)
    doc.add_page_break()

    _cross_browser_matrix(doc, results, browsers)
    doc.add_page_break()

    _recommendations(doc, failed_res, total)
    doc.add_page_break()

    _conclusion(doc, report_data, total, passed, failed, partial, rate, plan_only, url)

    # Save Word report
    import json
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"rapport_omnishore_{ts}.docx")
    doc.save(path)
    logger.info("Word report saved: %s", path)
    print(f"[REPORT] Word report saved: {path}")

    # Also save JSON snapshot alongside
    json_path = os.path.join(output_dir, f"rapport_omnishore_{ts}.json")
    try:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report_data, fh, indent=2, ensure_ascii=False)
        logger.info("JSON report saved: %s", json_path)
    except Exception as exc:
        logger.warning("Could not save JSON report: %s", exc)

    return path
