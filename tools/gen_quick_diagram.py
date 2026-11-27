#!/usr/bin/env python3
"""Draw the connectivity diagram of docs/INSTALL-QUICK.md, in English and French.

Writes docs/img/quick-install-flows.svg and docs/img/quick-install-flows.fr.svg. No dependency."""
import html, os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img")
L = {
 "en": dict(title="NSX Federation pack: what talks to what", internet="Internet", org="Your organisation",
   hub="Docker Hub", hub1="sorinplaton/nsxfederation-pak", hub2="sorinplaton/nsxfederation",
   art="Artifactory", r1="Docker Hub remote repository", r2="Approved repository", r2b="vcfops-approved",
   host="Linux host", host2="Docker, python3, repack.py", ops="VCF Operations", ops2="admin console, product UI",
   cp="Cloud proxy", cp2="runs the adapter container", nsx="NSX", gm="Global Managers", gm2="active and standby", lm="Local Managers", lm2="one per location",
   legend="Network flows. Each arrow starts at the system that opens the connection. Flows 1 to 4 and 6 use TCP 443.",
   f=["Artifactory pulls both images from Docker Hub (before this guide).",
      "The Linux host pulls the pack image and takes the .pak file out (steps 1 and 2).",
      "Each cloud proxy pulls the adapter image by digest, without a login (step 3).",
      "From the Linux host or a workstation, the administrator uploads the rewritten pack and creates the account (steps 4 and 5).",
      "VCF Operations and the cloud proxy use their existing collector link. Nothing to open.",
      "The adapter reads the Global Managers and the Local Managers, read-only (step 6)."]),
 "fr": dict(title="Pack NSX Federation : qui parle à qui", internet="Internet", org="Votre organisation",
   hub="Docker Hub", hub1="sorinplaton/nsxfederation-pak", hub2="sorinplaton/nsxfederation",
   art="Artifactory", r1="Dépôt distant Docker Hub", r2="Dépôt des images approuvées", r2b="vcfops-approved",
   host="Hôte Linux", host2="Docker, python3, repack.py", ops="VCF Operations", ops2="console d'administration, UI",
   cp="Proxy cloud", cp2="exécute l'adaptateur", nsx="NSX", gm="Global Managers", gm2="actif et standby", lm="Local Managers", lm2="un par emplacement",
   legend="Flux réseau. Chaque flèche part du système qui ouvre la connexion. Les flux 1 à 4 et 6 utilisent TCP 443.",
   f=["Artifactory récupère les deux images sur Docker Hub (avant ce guide).",
      "L'hôte Linux récupère l'image du pack et en sort le fichier .pak (étapes 1 et 2).",
      "Chaque proxy cloud récupère l'image de l'adaptateur par son digest, sans identifiant (étape 3).",
      "Depuis l'hôte Linux ou un poste de travail, l'administrateur envoie le pack réécrit et crée le compte (étapes 4 et 5).",
      "VCF Operations et le proxy cloud utilisent leur lien de collecte existant. Rien à ouvrir.",
      "L'adaptateur lit les Global Managers et les Local Managers, en lecture seule (étape 6)."]),
}
INK, MUTED, LINE = "#18212b", "#5a6675", "#c9d1db"
BLUE, GREEN, PURPLE, AMBER = "#0b5cad", "#1e7a50", "#57478c", "#9a5b00"
e = html.escape
def box(x, y, w, h, stroke, title, sub=None, fill="#ffffff"):
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    o += f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{stroke}"/>'
    o += f'<text x="{x+20}" y="{y+28}" font-size="17" font-weight="600" fill="{INK}">{e(title)}</text>'
    if sub: o += f'<text x="{x+20}" y="{y+48}" font-size="12.5" fill="{MUTED}">{e(sub)}</text>'
    return o
def chip(x, y, w, t1, t2=None, mono=False):
    h = 44 if t2 else 30
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#f3f6fa" stroke="{LINE}"/>'
    o += f'<text x="{x+10}" y="{y+19}" font-size="{11.8 if mono else 12.5}" fill="{INK}" {"font-family=\"ui-monospace,Menlo,Consolas,monospace\"" if mono else ""}>{e(t1)}</text>'
    if t2: o += f'<text x="{x+10}" y="{y+36}" font-size="12" fill="{MUTED}" font-family="ui-monospace,Menlo,Consolas,monospace">{e(t2)}</text>'
    return o
def arrow(points, n, bx, by, dashed=False, both=False):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    o = f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="2" {"stroke-dasharray=\"6 5\"" if dashed else ""} marker-end="url(#a)" {"marker-start=\"url(#b)\"" if both else ""}/>'
    o += f'<circle cx="{bx}" cy="{by}" r="12" fill="{INK}"/><text x="{bx}" y="{by+4.5}" font-size="13" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>'
    return o
def svg(lang):
    t = L[lang]; W, H = 1100, 716
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui,-apple-system,Segoe UI,Arial,sans-serif" role="img" aria-label="{e(t["title"])}">',
         f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
         f'<marker id="b" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="{INK}"/></marker></defs>',
         f'<rect width="{W}" height="{H}" rx="14" fill="#ffffff" stroke="{LINE}"/>',
         f'<text x="28" y="40" font-size="20" font-weight="600" fill="{INK}">{e(t["title"])}</text>',
         # zones
         f'<rect x="24" y="62" width="276" height="398" rx="12" fill="#fbf6ec" stroke="#e6d9bd"/><text x="40" y="88" font-size="13" font-weight="600" fill="{AMBER}" letter-spacing=".06em">{e(t["internet"].upper())}</text>',
         f'<rect x="320" y="62" width="756" height="398" rx="12" fill="#f4f7fb" stroke="#d5deea"/><text x="336" y="88" font-size="13" font-weight="600" fill="{BLUE}" letter-spacing=".06em">{e(t["org"].upper())}</text>']
    # Docker Hub
    o.append(box(40, 150, 244, 136, AMBER, t["hub"])); o.append(chip(54, 190, 222, t["hub1"], mono=True)); o.append(chip(54, 228, 222, t["hub2"], mono=True))
    # Artifactory
    o.append(box(344, 112, 250, 196, BLUE, t["art"])); o.append(chip(362, 154, 214, t["r1"])); o.append(chip(362, 196, 214, t["r2"], t["r2b"]))
    o.append(f'<path d="M469,184 L469,194" stroke="{MUTED}" stroke-width="1.5" marker-end="url(#a)"/>')
    # Linux host, VCF Operations, cloud proxy, NSX
    o.append(box(344, 372, 250, 66, GREEN, t["host"], t["host2"]))
    o.append(box(664, 372, 220, 66, PURPLE, t["ops"], t["ops2"]))
    o.append(box(664, 150, 220, 66, PURPLE, t["cp"], t["cp2"]))
    o.append(box(916, 112, 146, 196, GREEN, t["nsx"]))
    o.append(f'<text x="936" y="170" font-size="12.5" font-weight="600" fill="{INK}">{e(t["gm"])}</text><text x="936" y="187" font-size="11.5" fill="{MUTED}">{e(t["gm2"])}</text>')
    o.append(f'<text x="936" y="240" font-size="12.5" font-weight="600" fill="{INK}">{e(t["lm"])}</text><text x="936" y="257" font-size="11.5" fill="{MUTED}">{e(t["lm2"])}</text>')
    # flows
    o.append(arrow([(344, 172), (288, 172)], 1, 316, 150))                      # Artifactory -> Docker Hub
    o.append(arrow([(469, 372), (469, 312)], 2, 492, 342))                      # Linux host -> Artifactory
    o.append(arrow([(664, 200), (598, 200)], 3, 631, 178))                      # cloud proxy -> Artifactory (approved)
    o.append(arrow([(594, 405), (660, 405)], 4, 627, 383))                      # admin -> VCF Operations
    o.append(arrow([(774, 368), (774, 220)], 5, 797, 294, dashed=True, both=True))  # VCF Ops <-> cloud proxy
    o.append(arrow([(884, 183), (912, 183)], 6, 898, 161))                      # cloud proxy -> NSX
    # legend
    o.append(f'<text x="28" y="492" font-size="13.5" font-weight="600" fill="{INK}">{e(t["legend"])}</text>')
    for i, line in enumerate(t["f"]):
        x, y = 28, 520 + i * 27
        o.append(f'<circle cx="{x+12}" cy="{y}" r="11" fill="{INK}"/><text x="{x+12}" y="{y+4.5}" font-size="12.5" font-weight="700" fill="#fff" text-anchor="middle">{i+1}</text>')
        o.append(f'<text x="{x+32}" y="{y+4.5}" font-size="12.8" fill="{INK}">{e(line)}</text>')
    o.append(f'<path d="M28,690 L72,690" stroke="{INK}" stroke-width="2" stroke-dasharray="6 5"/><text x="82" y="694" font-size="12.5" fill="{MUTED}">{"lien existant de la plateforme" if lang=="fr" else "existing link of the platform"}</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"
for lang in L:
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, f"quick-install-flows.{lang}.svg".replace(".en.", ".")), "w").write(svg(lang))
