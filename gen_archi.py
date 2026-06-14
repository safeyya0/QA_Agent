import cairosvg

W, H = 1400, 1130

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def card(x, y, w, h, accent, fill, icon, title, lines, title_size=16, line_step=17):
    bx = x + 16
    by = y + (h - 46) / 2
    tx = x + 78
    out = []
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
               f'fill="{fill}" stroke="{accent}" stroke-width="2" filter="url(#sh)"/>')
    out.append(f'<rect x="{bx}" y="{by}" width="46" height="46" rx="12" fill="{accent}"/>')
    out.append(f'<use href="#{icon}" x="{bx+8}" y="{by+8}" width="30" height="30"/>')
    if lines:
        ty = y + 30
        out.append(f'<text x="{tx}" y="{ty}" font-size="{title_size}" font-weight="700" '
                   f'fill="#16243A">{esc(title)}</text>')
        for i, ln in enumerate(lines):
            out.append(f'<text x="{tx}" y="{ty + (i+1)*line_step}" font-size="12" '
                       f'fill="#46566B">{esc(ln)}</text>')
    else:
        out.append(f'<text x="{tx}" y="{y + h/2 + 5}" font-size="{title_size}" '
                   f'font-weight="700" fill="#16243A">{esc(title)}</text>')
    return "\n".join(out)

def arrow(x1, y1, x2, y2, color="#475569", dashed=False, marker="arrow", width=2.4):
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{width}"{dash} marker-end="url(#{marker})"/>')

def parrow(d, color="#475569", dashed=False, marker="arrow", width=2.4):
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"'
            f'{dash} marker-end="url(#{marker})"/>')

def panel(x, y, w, h, label):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
            f'fill="#FFFFFF" stroke="#D7E2EC" stroke-width="1.5" stroke-dasharray="2 6"/>'
            f'<text x="{x+22}" y="{y+26}" font-size="12" font-weight="700" '
            f'letter-spacing="2" fill="#9AAABC">{label}</text>')

defs = '''
<defs>
  <filter id="sh" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#1E293B" flood-opacity="0.10"/>
  </filter>
  <marker id="arrow" markerWidth="11" markerHeight="11" refX="8" refY="5" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 L3,5 Z" fill="#475569"/></marker>
  <marker id="arrowP" markerWidth="11" markerHeight="11" refX="8" refY="5" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 L3,5 Z" fill="#8B5CF6"/></marker>
  <marker id="arrowC" markerWidth="11" markerHeight="11" refX="8" refY="5" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 L3,5 Z" fill="#06B6D4"/></marker>

  <symbol id="ic-user" viewBox="0 0 40 40">
    <circle cx="20" cy="13" r="7" fill="white"/>
    <path d="M6,35 C6,23 34,23 34,35 Z" fill="white"/></symbol>
  <symbol id="ic-dash" viewBox="0 0 40 40">
    <rect x="6" y="6" width="12" height="11" rx="2" fill="white"/>
    <rect x="22" y="6" width="12" height="11" rx="2" fill="white"/>
    <rect x="6" y="21" width="12" height="13" rx="2" fill="white"/>
    <rect x="22" y="21" width="12" height="13" rx="2" fill="white"/></symbol>
  <symbol id="ic-hub" viewBox="0 0 40 40">
    <line x1="20" y1="20" x2="8" y2="8" stroke="white" stroke-width="2"/>
    <line x1="20" y1="20" x2="32" y2="8" stroke="white" stroke-width="2"/>
    <line x1="20" y1="20" x2="8" y2="32" stroke="white" stroke-width="2"/>
    <line x1="20" y1="20" x2="32" y2="32" stroke="white" stroke-width="2"/>
    <circle cx="8" cy="8" r="3.5" fill="white"/><circle cx="32" cy="8" r="3.5" fill="white"/>
    <circle cx="8" cy="32" r="3.5" fill="white"/><circle cx="32" cy="32" r="3.5" fill="white"/>
    <circle cx="20" cy="20" r="5.5" fill="white"/></symbol>
  <symbol id="ic-robot" viewBox="0 0 40 40">
    <line x1="20" y1="3" x2="20" y2="9" stroke="white" stroke-width="2"/>
    <circle cx="20" cy="3" r="2" fill="white"/>
    <rect x="7" y="9" width="26" height="23" rx="6" fill="white"/>
    <circle cx="15" cy="19" r="2.6" fill="#1E293B"/>
    <circle cx="25" cy="19" r="2.6" fill="#1E293B"/>
    <rect x="14" y="25" width="12" height="2.4" rx="1.2" fill="#1E293B"/></symbol>
  <symbol id="ic-ai" viewBox="0 0 40 40">
    <path d="M20,5 L23,15 L33,18 L23,21 L20,31 L17,21 L7,18 L17,15 Z" fill="white"/>
    <path d="M31,26 L32.4,29.6 L36,31 L32.4,32.4 L31,36 L29.6,32.4 L26,31 L29.6,29.6 Z" fill="white"/></symbol>
  <symbol id="ic-tool" viewBox="0 0 40 40">
    <circle cx="20" cy="20" r="7" fill="none" stroke="white" stroke-width="3"/>
    <circle cx="20" cy="20" r="2.5" fill="white"/>
    <rect x="18" y="3" width="4" height="6" rx="1" fill="white"/>
    <rect x="18" y="31" width="4" height="6" rx="1" fill="white"/>
    <rect x="3" y="18" width="6" height="4" rx="1" fill="white"/>
    <rect x="31" y="18" width="6" height="4" rx="1" fill="white"/></symbol>
  <symbol id="ic-play" viewBox="0 0 40 40">
    <rect x="5" y="8" width="30" height="24" rx="3" fill="white"/>
    <rect x="5" y="8" width="30" height="6" rx="3" fill="#1E293B"/>
    <path d="M17,17 L27,22 L17,27 Z" fill="#1E293B"/></symbol>
  <symbol id="ic-globe" viewBox="0 0 40 40">
    <circle cx="20" cy="20" r="13" fill="none" stroke="white" stroke-width="2.6"/>
    <ellipse cx="20" cy="20" rx="5.5" ry="13" fill="none" stroke="white" stroke-width="2"/>
    <line x1="7" y1="20" x2="33" y2="20" stroke="white" stroke-width="2"/></symbol>
  <symbol id="ic-doc" viewBox="0 0 40 40">
    <rect x="10" y="5" width="20" height="30" rx="2" fill="white"/>
    <line x1="14" y1="13" x2="26" y2="13" stroke="#64748B" stroke-width="2"/>
    <line x1="14" y1="19" x2="26" y2="19" stroke="#64748B" stroke-width="2"/>
    <line x1="14" y1="25" x2="26" y2="25" stroke="#64748B" stroke-width="2"/>
    <line x1="14" y1="31" x2="22" y2="31" stroke="#64748B" stroke-width="2"/></symbol>
</defs>
'''

s = []
s.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">')
s.append(defs)
s.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#F4F8FB"/>')

# ---- Title ----
s.append('<text x="70" y="58" font-size="32" font-weight="700" fill="#15795A">'
         'Architecture Globale — Plateforme QA Multi-Agents</text>')
s.append('<text x="72" y="88" font-size="15" fill="#64748B">'
         'Génération &amp; exécution automatisées de tests, pilotées par IA générative · OMNISHORE</text>')
s.append('<rect x="72" y="100" width="120" height="4" rx="2" fill="#1E9E6A"/>')
# OMNISHORE badge
s.append('<rect x="1175" y="40" width="155" height="46" rx="12" fill="#ECFDF5" stroke="#1E9E6A" stroke-width="1.5"/>')
s.append('<text x="1252" y="62" font-size="15" font-weight="700" fill="#15795A" text-anchor="middle">OMNISHORE</text>')
s.append('<text x="1252" y="78" font-size="10" fill="#5E8C78" text-anchor="middle" letter-spacing="1">QA PLATFORM</text>')

# ---- Top interface row ----
s.append(card(130, 150, 240, 92, "#6366F1", "#EEF2FF", "ic-user",
              "Utilisateur QA", ["Ingénieur de test", "Pilotage & supervision"]))
s.append(card(470, 146, 340, 104, "#1E9E6A", "#ECFDF5", "ic-dash",
              "Control Panel (OMNISHORE)",
              ["Next.js · React · SQLite — Kanban", "Supervision temps réel (SSE/WS) · RBAC"]))
s.append(arrow(370, 196, 466, 198))

# ---- Group panels ----
s.append(panel(110, 360, 300, 450, "ENTRÉES"))
s.append(panel(440, 360, 380, 450, "PIPELINE MULTI-AGENTS (ORCHESTRÉ)"))
s.append(panel(950, 360, 360, 450, "RESSOURCES IA PARTAGÉES"))
s.append(panel(110, 830, 1200, 280, "EXÉCUTION &amp; REPORTING"))

# ---- Orchestrator ----
s.append(card(475, 386, 310, 64, "#F59E0B", "#FFFBEB", "ic-hub",
              "Orchestrateur", ["Coordination · Dispatch (FastAPI)"]))
s.append(arrow(635, 250, 630, 386))

# ---- Agents (vertical pipeline) ----
s.append(card(460, 470, 340, 96, "#3B82F6", "#EFF6FF", "ic-robot",
              "Agent d'Analyse",
              ["Entrée : Document SFD", "Sortie : User Stories"]))
s.append(card(460, 588, 340, 96, "#6366F1", "#EEF2FF", "ic-robot",
              "Agent de Spécification",
              ["Entrée : Exigences fonctionnelles", "Sortie : .md + fichiers Excel"]))
s.append(card(460, 706, 340, 96, "#8B5CF6", "#F5F3FF", "ic-robot",
              "Agent de Test E2E",
              ["Entrée : Fichier .md", "Génère code Robot Framework + exécute"]))
# orchestrator -> A1, pipeline chain
s.append(arrow(630, 450, 630, 470))
s.append(arrow(630, 566, 630, 588))
s.append(arrow(630, 684, 630, 706))

# ---- Left inputs ----
s.append(card(130, 490, 250, 56, "#64748B", "#F8FAFC", "ic-doc",
              "Document SFD", ["Spécifications"], title_size=14, line_step=15))
s.append(card(130, 608, 250, 56, "#64748B", "#F8FAFC", "ic-doc",
              "Exigences fonct.", ["Cahier des charges"], title_size=14, line_step=15))
s.append(card(130, 726, 250, 56, "#64748B", "#F8FAFC", "ic-doc",
              "Fichier .md", ["issu de l'Agent 2"], title_size=14, line_step=15))
s.append(arrow(380, 518, 458, 510))
s.append(arrow(380, 636, 458, 628))
s.append(arrow(380, 754, 458, 746))

# ---- Right resources (LLM + tools) ----
s.append(card(970, 462, 330, 124, "#8B5CF6", "#F5F3FF", "ic-ai",
              "Moteur IA (LLM)",
              ["Groq · LPU (faible latence)",
               "Llama 3.3 70B · Llama 3.1 8B",
               "via LangChain (langchain-groq)"]))
s.append(card(970, 606, 330, 178, "#06B6D4", "#ECFEFF", "ic-tool",
              "Outils & Capacités",
              ["Tool calling :",
               "• Navigation URL · clic · saisie",
               "• Attente sélecteur · capture",
               "• Extraction DOM (vision DOM)",
               "• Robot Framework · Playwright"], line_step=19))
# dashed LLM links from each agent
s.append(arrow(800, 510, 968, 502, color="#8B5CF6", dashed=True, marker="arrowP", width=2))
s.append(arrow(800, 628, 968, 524, color="#8B5CF6", dashed=True, marker="arrowP", width=2))
s.append(arrow(800, 746, 968, 546, color="#8B5CF6", dashed=True, marker="arrowP", width=2))
# A3 -> tools
s.append(arrow(800, 760, 968, 670, color="#06B6D4", dashed=True, marker="arrowC", width=2))

# ---- Execution & reporting ----
s.append(parrow("M630,802 C630,840 360,830 300,862", width=2.6))
s.append('<text x="430" y="845" font-size="11" fill="#64748B">génère .robot + exécute</text>')
s.append(card(150, 866, 300, 92, "#0EA5E9", "#F0F9FF", "ic-play",
              "Robot Framework", ["Browser Library", "scénarios .robot"]))
s.append(card(510, 866, 300, 92, "#14B8A6", "#F0FDFA", "ic-play",
              "Playwright", ["Chromium · Firefox", "WebKit · auto-waiting"]))
s.append(card(870, 866, 300, 92, "#F59E0B", "#FFFBEB", "ic-globe",
              "Application Web cible", ["Multi-navigateurs", "tests End-to-End"]))
s.append(arrow(450, 912, 508, 912))
s.append(arrow(810, 912, 868, 912))
# reports
s.append(card(510, 990, 560, 80, "#10B981", "#ECFDF5", "ic-doc",
              "Sorties & Rapports",
              ["Rapports Word (.docx) / JSON · Cartes Trello (anomalies)"]))
s.append(parrow("M1020,958 C1020,985 880,975 850,990", width=2.4))

# ---- Legend ----
s.append('<rect x="1185" y="866" width="118" height="120" rx="10" fill="#FFFFFF" stroke="#D7E2EC" stroke-width="1.2"/>')
s.append('<text x="1196" y="888" font-size="11" font-weight="700" fill="#64748B">LÉGENDE</text>')
s.append('<line x1="1196" y1="908" x2="1224" y2="908" stroke="#475569" stroke-width="2.4" marker-end="url(#arrow)"/>')
s.append('<text x="1230" y="912" font-size="9.5" fill="#46566B">Contrôle</text>')
s.append('<line x1="1196" y1="936" x2="1224" y2="936" stroke="#8B5CF6" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#arrowP)"/>')
s.append('<text x="1230" y="940" font-size="9.5" fill="#46566B">Appel LLM</text>')
s.append('<line x1="1196" y1="964" x2="1224" y2="964" stroke="#06B6D4" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#arrowC)"/>')
s.append('<text x="1230" y="968" font-size="9.5" fill="#46566B">Outils</text>')

s.append('</svg>')

svg = "\n".join(s)
with open("architecture_globale.svg", "w") as f:
    f.write(svg)
cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                 write_to="architecture_globale.png",
                 output_width=W*2, output_height=H*2)
print("done")
