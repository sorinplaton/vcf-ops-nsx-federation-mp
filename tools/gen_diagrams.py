#!/usr/bin/env python3
"""Draw the diagrams of the install guides and of the release guide, in English and French.

Writes SVG files into docs/img. Each diagram is a set of boxes, numbered flows and a legend; an arrow starts at the
system that opens the connection. No dependency. The diagram of docs/INSTALL-QUICK.md is drawn by
tools/gen_quick_diagram.py, in the same style.
"""
import html
import os
import textwrap

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img")
INK, MUTED, LINE = "#18212b", "#5a6675", "#c9d1db"
BLUE, GREEN, PURPLE, AMBER = "#0b5cad", "#1e7a50", "#57478c", "#9a5b00"
MONO = 'font-family="ui-monospace,Menlo,Consolas,monospace"'
DASH = 'stroke-dasharray="6 5"'
BOLD = 'font-weight="600"'
e = html.escape

# the grid most diagrams share: an internet zone on the left, the organisation on the right, three columns, three rows
C1, C2, C3 = 344, 624, 880
R1, R2, R3 = 130, 250, 370
BW, BH = 210, 66


def head(w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="system-ui,-apple-system,Segoe UI,Arial,sans-serif" role="img" aria-label="{e(title)}">\n'
            f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker></defs>\n'
            f'<rect width="{w}" height="{h}" rx="14" fill="#ffffff" stroke="{LINE}"/>\n'
            f'<text x="28" y="40" font-size="20" font-weight="600" fill="{INK}">{e(title)}</text>\n')


def zone(x, y, w, h, label, internet=False):
    fill, stroke, ink = ("#fbf6ec", "#e6d9bd", AMBER) if internet else ("#f4f7fb", "#d5deea", BLUE)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x + 16}" y="{y + 26}" font-size="13" font-weight="600" fill="{ink}" letter-spacing=".06em">'
            f'{e(label.upper())}</text>\n')


def box(x, y, w, h, colour, title, *sub):
    o = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#ffffff" stroke="{colour}" stroke-width="2"/>'
         f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{colour}"/>'
         f'<text x="{x + 20}" y="{y + 28}" font-size="17" font-weight="600" fill="{INK}">{e(title)}</text>')
    for i, line in enumerate(sub):
        o += f'<text x="{x + 20}" y="{y + 48 + 16 * i}" font-size="12.5" fill="{MUTED}">{e(line)}</text>'
    return o + "\n"


def chip(x, y, w, *lines, mono=False):
    """A small grey card: the first line names the thing, the others describe it. Returns the SVG and its height."""
    h = 14 + 16 * len(lines)
    o = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#ffffff" stroke="{LINE}"/>'
    for i, line in enumerate(lines):
        first = i == 0
        o += (f'<text x="{x + 10}" y="{y + 19 + 16 * i}" font-size="{11.8 if mono and first else 12.3}" '
              f'fill="{INK if first else MUTED}" {BOLD if first and not mono else ""} '
              f'{MONO if mono and first else ""}>{e(line)}</text>')
    return o + "\n"


def nsx(x, y, w, h, t):
    return (box(x, y, w, h, GREEN, "NSX")
            + f'<text x="{x + 20}" y="{y + 62}" font-size="12.5" font-weight="600" fill="{INK}">Global Managers</text>'
            + f'<text x="{x + 20}" y="{y + 79}" font-size="11.5" fill="{MUTED}">{e(t["gm"])}</text>'
            + f'<text x="{x + 20}" y="{y + 128}" font-size="12.5" font-weight="600" fill="{INK}">Local Managers</text>'
            + f'<text x="{x + 20}" y="{y + 145}" font-size="11.5" fill="{MUTED}">{e(t["lm"])}</text>\n')


def flow(n, pts, dashed=False):
    """A numbered arrow along pts. The number sits on the middle of the longest segment."""
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    segs = [(abs(x2 - x1) + abs(y2 - y1), (x1 + x2) / 2, (y1 + y2) / 2) for (x1, y1), (x2, y2) in zip(pts, pts[1:])]
    _, bx, by = max(segs)
    return (f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="2" {DASH if dashed else ""} marker-end="url(#a)"/>'
            f'<circle cx="{bx}" cy="{by}" r="12" fill="{INK}" stroke="#ffffff" stroke-width="2.5"/>'
            f'<text x="{bx}" y="{by + 4.5}" font-size="13" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>\n')


def link(pts):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    return f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.6" marker-end="url(#a)"/>\n'


def legend(y, heading, lines, dashed_note=None):
    """The numbered list under the drawing. Returns the SVG and the y below it."""
    o = ""
    for part in textwrap.wrap(heading, 150):
        o += f'<text x="28" y="{y}" font-size="13.5" font-weight="600" fill="{INK}">{e(part)}</text>\n'
        y += 20
    y += 12
    for i, line in enumerate(lines):
        o += (f'<circle cx="40" cy="{y}" r="11" fill="{INK}"/><text x="40" y="{y + 4.5}" font-size="12.5" '
              f'font-weight="700" fill="#fff" text-anchor="middle">{i + 1}</text>')
        for part in textwrap.wrap(line, 160):
            o += f'<text x="60" y="{y + 4.5}" font-size="12.8" fill="{INK}">{e(part)}</text>\n'
            y += 19
        y += 8
    if dashed_note:
        o += (f'<path d="M28,{y + 4} L72,{y + 4}" stroke="{INK}" stroke-width="2" {DASH}/>'
              f'<text x="82" y="{y + 8}" font-size="12.5" fill="{MUTED}">{e(dashed_note)}</text>\n')
        y += 24
    return o, y


def finish(parts, title, w, legend_y, heading, lines, dashed_note):
    body, y = legend(legend_y, heading, lines, dashed_note)
    return head(w, y + 14, title) + "".join(parts) + body + "</svg>\n"


FR = {"gm": "actif et standby", "lm": "un par emplacement", "cp": "Proxy cloud", "internet": "Internet"}
EN = {"gm": "active and standby", "lm": "one per location", "cp": "Cloud proxy", "internet": "Internet"}


def install_option_b(fr):
    t = FR if fr else EN
    p = [zone(24, 62, 276, 418, "Internet", True), zone(320, 62, 756, 418, "Réseau de l'organisation" if fr else "Your network")]
    sites = ([("download.docker.com, dl.google.com", "dépôts de la distribution"), ("github.com", "clone, téléchargement de govc"),
              ("Docker Hub", "registry-1.docker.io"), ("pypi.org", "files.pythonhosted.org"), ("cgr.dev", "base et paquets Wolfi")]
             if fr else
             [("download.docker.com, dl.google.com", "distribution repositories"), ("github.com", "clone, govc download"),
              ("Docker Hub", "registry-1.docker.io"), ("pypi.org", "files.pythonhosted.org"), ("cgr.dev", "Wolfi base and packages")])
    for i, s in enumerate(sites):
        p.append(chip(40, 100 + 54 * i, 240, *s))
    p.append(f'<path d="M290,100 L296,100 L296,362 L290,362" fill="none" stroke="{MUTED}" stroke-width="1.6"/>\n')
    p.append(chip(40, 384, 240, "ghcr.io", "pkg-containers.githubusercontent.com", "image publiée" if fr else "released image"))
    p.append(box(C1, R1, BW, BH, GREEN, "Hôte de construction" if fr else "Build host", "(jumphost)" if fr else "(jump host)"))
    p.append(box(C2, R1, BW, BH, PURPLE, "VCF Operations", "console d'admin., Suite API" if fr else "admin console, Suite API"))
    p.append(box(C2, R2, BW, BH, BLUE, "vCenter, hôte ESXi" if fr else "vCenter, ESXi host", "de la VM du proxy cloud" if fr else "of the cloud proxy VM"))
    p.append(box(C1, R3, BW, BH, BLUE, "Registre" if fr else "Registry", "HTTPS"))
    p.append(box(C2, R3, BW, BH, PURPLE, t["cp"]))
    p.append(nsx(C3, R1, 180, 186, t))
    p += [flow(1, [(C1, 163), (300, 163)]), flow(2, [(420, 196), (420, 370)]), flow(3, [(554, 150), (624, 150)]),
          flow(4, [(500, 196), (500, 283), (624, 283)]), flow(5, [(500, 130), (500, 106), (970, 106), (970, 130)]),
          flow(6, [(624, 403), (554, 403)], True), flow(7, [(729, 436), (729, 462), (160, 462), (160, 446)], True),
          flow(8, [(834, 403), (970, 403), (970, 316)], True)]
    if fr:
        return finish(p, "Option B : flux réseau de l'hôte de construction et du proxy cloud", 1100, 512,
                      "Flux réseau. Chaque flèche part du système qui ouvre la connexion. Traits pleins : l'hôte de construction. Pointillés : le proxy cloud.",
                      ["TCP 443 vers Internet : Docker, Chrome, git, paquets Python et Node.js ; le clone (github.com) ; les images de base (Docker Hub) ; "
                       "les paquets du SDK et de l'adaptateur (PyPI) ; la base Wolfi de l'image de l'adaptateur (cgr.dev).",
                       "TCP 5000 : push de l'image (étape 3).",
                       "TCP 443 : pack, compte, scripts (étapes 6 à 10).",
                       "TCP 443 : govc vers vCenter, et govc guest.upload vers l'hôte ESXi de la VM du proxy cloud (étape 4).",
                       "TCP 443 : test local facultatif sur les Global Managers et les Local Managers (étape 5).",
                       "TCP 5000 : récupération de l'image par le proxy cloud (option B).",
                       "TCP 443 : récupération de l'image par le proxy cloud (option A).",
                       "TCP 443 : collecte."], None)
    return finish(p, "Option B: network flows of the build host and of the cloud proxy", 1100, 512,
                  "Network flows. Each arrow starts at the system that opens the connection. Solid: the build host. Dashed: the cloud proxy.",
                  ["TCP 443 to the internet: Docker, Chrome, git, Python and Node.js packages; the clone (github.com); base images (Docker Hub); "
                   "SDK and adapter packages (PyPI); the Wolfi base of the adapter image (cgr.dev).",
                   "TCP 5000: push the image (step 3).",
                   "TCP 443: upload the pack, account, scripts (steps 6-10).",
                   "TCP 443: govc to vCenter, and govc guest.upload to the ESXi host of the cloud proxy VM (step 4).",
                   "TCP 443: optional local test against the Global Managers and the Local Managers (step 5).",
                   "TCP 5000: the cloud proxy pulls the image (Option B).",
                   "TCP 443: the cloud proxy pulls the image (Option A).",
                   "TCP 443: collection."], None)


def three_sites(fr, github_sub, ghcr_sub):
    return [chip(40, 110, 240, "github.com", github_sub),
            chip(40, 190, 240, "Sigstore", "tuf-repo-cdn.sigstore.dev", "rekor.sigstore.dev"),
            chip(40, 290, 240, "ghcr.io", "pkg-containers.githubusercontent.com", ghcr_sub)]


def install_option_a(fr):
    t = FR if fr else EN
    p = [zone(24, 62, 276, 418, "Internet", True), zone(320, 62, 756, 418, "Réseau de l'organisation" if fr else "Your network")]
    p += three_sites(fr, "page des versions, cosign.pub" if fr else "release page, cosign.pub",
                     "image de l'adaptateur et sa signature" if fr else "adapter image and its signature")
    p.append(box(C1, 110, BW, 226, GREEN, "Poste d'administration" if fr else "Admin workstation", "navigateur, cosign" if fr else "browser, cosign"))
    p.append(box(C2, R1, BW, BH, PURPLE, "VCF Operations", "console d'administration," if fr else "admin console, product UI", *(["interface du produit"] if fr else [])))
    p.append(box(C1, R3, BW, BH, PURPLE, t["cp"]))
    p.append(nsx(C3, R1, 180, 186, t))
    p += [flow(1, [(C1, 133), (284, 133)]), flow(2, [(C1, 321), (284, 321)]), flow(3, [(C1, 221), (284, 221)]),
          flow(4, [(554, 163), (624, 163)]), flow(5, [(C1, 403), (160, 403), (160, 356)], True),
          flow(6, [(554, 403), (970, 403), (970, 316)], True)]
    if fr:
        return finish(p, "Option A : flux réseau du poste d'administration et du proxy cloud", 1100, 512,
                      "Flux réseau, tous en TCP 443. Chaque flèche part du système qui ouvre la connexion. Traits pleins : le poste d'administration. Pointillés : le proxy cloud.",
                      ["Pack, somme de contrôle, bundle, clé.", "Vérifier la signature de l'image.", "Journal de transparence.",
                       "Installer le pack, créer le compte (étapes 6 à 10).", "Le proxy cloud récupère l'image par son digest.", "Collecte."], None)
    return finish(p, "Option A: network flows of the admin workstation and of the cloud proxy", 1100, 512,
                  "Network flows, all on TCP 443. Each arrow starts at the system that opens the connection. Solid: the admin workstation. Dashed: the cloud proxy.",
                  ["Pack, checksum, bundle, key.", "Verify the image signature.", "Transparency log.",
                   "Install the pack, create the account (steps 6-10).", "The cloud proxy pulls the image by digest.", "Collection."], None)


def install_mirror(fr):
    t = FR if fr else EN
    p = [zone(24, 62, 276, 418, "Internet", True), zone(320, 62, 756, 418, "Réseau interne" if fr else "Internal network")]
    p += three_sites(fr, "page des versions, cosign.pub, dépôt" if fr else "release page, cosign.pub, repository",
                     "image de l'adaptateur et sa signature" if fr else "adapter image and its signature")
    p.append(box(C1, 110, BW, 226, GREEN, "Hôte de copie" if fr else "Mirror host", "skopeo, cosign, repack.py"))
    p.append(box(C2, R1, BW, BH, PURPLE, "VCF Operations"))
    p.append(box(C2, R2, BW, BH, BLUE, "Registre interne" if fr else "Internal registry", "HTTPS"))
    p.append(box(C2, R3, BW, BH, PURPLE, t["cp"], "sans accès à Internet" if fr else "no internet access"))
    p.append(nsx(C3, R1, 180, 186, t))
    p += [flow(1, [(C1, 133), (284, 133)]), flow(2, [(C1, 321), (284, 321)]), flow(3, [(C1, 221), (284, 221)]),
          flow(4, [(554, 283), (624, 283)]), flow(5, [(554, 163), (624, 163)]),
          flow(6, [(729, 370), (729, 320)], True), flow(7, [(834, 403), (970, 403), (970, 316)], True)]
    if fr:
        return finish(p, "Variante avec registre interne : flux réseau", 1100, 512,
                      "Flux réseau, tous en TCP 443. Chaque flèche part du système qui ouvre la connexion. Traits pleins : l'hôte de copie. Pointillés : le proxy cloud.",
                      ["Pack, clé, clone.", "Copier l'image par son digest.", "Journal de transparence.", "Push de l'image.",
                       "Installer le pack réécrit (étape 6).", "Le proxy cloud récupère l'image.", "Collecte."], None)
    return finish(p, "Mirror variant: network flows", 1100, 512,
                  "Network flows, all on TCP 443. Each arrow starts at the system that opens the connection. Solid: the mirror host. Dashed: the cloud proxy.",
                  ["Pack, key, clone.", "Copy the image by digest.", "Transparency log.", "Push the image.",
                   "Install the rewritten pack (step 6).", "The cloud proxy pulls the image.", "Collection."], None)


def enterprise_network(fr):
    t = FR if fr else EN
    p = [zone(24, 62, 276, 418, "Internet", True), zone(320, 62, 756, 418, "Réseau interne" if fr else "Internal network")]
    p.append(chip(40, 110, 240, "Docker Hub", "registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com"))
    p.append(chip(40, 250, 240, "Sigstore", "tuf-repo-cdn.sigstore.dev", "rekor.sigstore.dev"))
    p.append(chip(40, 372, 240, "github.com", "raw.githubusercontent.com", "repack.py, cosign"))
    p.append(box(C1, R1, BW, BH, BLUE, "Artifactory", "HTTPS"))
    p.append(box(C1, R3, BW, BH, GREEN, "Hôte de promotion" if fr else "Promotion host"))
    p.append(box(C2, R3, BW, BH, PURPLE, "VCF Operations"))
    p.append(box(C2, R1, BW, BH, PURPLE, t["cp"], "sans accès à Internet" if fr else "no internet access"))
    p.append(nsx(C3, R1, 180, 186, t))
    p += [flow(1, [(C1, 150), (284, 150)]), flow(2, [(430, 370), (430, 200)]),
          flow(3, [(C1, 386), (314, 386), (314, 281), (284, 281)]), flow(4, [(C1, 420), (284, 420)]),
          flow(5, [(554, 403), (624, 403)]), flow(6, [(624, 163), (558, 163)], True), flow(7, [(834, 163), (876, 163)], True)]
    if fr:
        return finish(p, "Installation en entreprise : flux réseau", 1100, 512,
                      "Flux réseau, tous en TCP 443. Chaque flèche part du système qui ouvre la connexion. Traits pleins : Artifactory et l'hôte de promotion. Pointillés : le proxy cloud.",
                      ["Dépôt distant.", "Pull, push (étapes 3 à 7).", "Journal de transparence (étape 4).", "Outils, une seule fois (étape 2).",
                       "Installation (étape 9).", "Le proxy cloud récupère l'image de l'adaptateur.", "Collecte."], None)
    return finish(p, "Enterprise install: network flows", 1100, 512,
                  "Network flows, all on TCP 443. Each arrow starts at the system that opens the connection. Solid: Artifactory and the promotion host. Dashed: the cloud proxy.",
                  ["Remote repository.", "Pull, push (steps 3 to 7).", "Transparency log (step 4).", "Tools, once (step 2).",
                   "Install (step 9).", "The cloud proxy pulls the adapter image.", "Collection."], None)


def enterprise_path(fr):
    t = FR if fr else EN
    p = [zone(24, 62, 246, 418, "GitHub : référence" if fr else "GitHub: source of truth"),
         zone(290, 62, 270, 418, "Docker Hub", True), zone(580, 62, 576, 418, "Organisation")]
    p.append(box(40, 110, 214, 50, BLUE, "Tag Git v1.x.y" if fr else "Git tag v1.x.y"))
    p.append(box(40, 200, 214, BH, BLUE, "Workflow de version" if fr else "Release workflow", "build, push, signature" if fr else "build, push, sign"))
    p.append(box(40, 310, 214, BH, BLUE, "Version GitHub" if fr else "GitHub release", ".pak, somme de contrôle, SBOM" if fr else ".pak, checksum, SBOM"))
    p += [link([(147, 160), (147, 196)]), link([(147, 266), (147, 306)])]
    p.append(box(306, 170, 244, 156, AMBER, "Docker Hub"))
    p.append(chip(318, 208, 222, "sorinplaton/nsxfederation-pak", "le .pak dans une image" if fr else "the .pak in an image", mono=True))
    p.append(chip(318, 264, 222, "sorinplaton/nsxfederation", "image de l'adaptateur" if fr else "adapter image", mono=True))
    p.append(box(604, 130, 260, 206, BLUE, "Artifactory"))
    p.append(chip(620, 172, 228, "Dépôt distant Docker Hub" if fr else "Docker Hub remote repository",
                  "cache, non utilisé par la production" if fr else "cache, not used by production"))
    p.append(chip(620, 240, 228, "vcfops-approved", "dépôt local" if fr else "local repository", mono=True))
    p.append(box(604, 390, 260, BH, GREEN, "Hôte de promotion" if fr else "Promotion host",
                 "vérifier, analyser, copier, réécrire" if fr else "verify, scan, copy, rewrite"))
    p.append(box(930, 390, 206, BH, PURPLE, "VCF Operations"))
    p.append(box(930, 230, 206, BH, PURPLE, t["cp"]))
    p += [flow(1, [(254, 233), (302, 233)]), flow(2, [(604, 195), (554, 195)]), flow(3, [(670, 390), (670, 340)]),
          flow(4, [(800, 390), (800, 340)]), flow(5, [(864, 423), (926, 423)]), flow(6, [(930, 263), (868, 263)], True)]
    if fr:
        return finish(p, "Comment une version arrive dans l'organisation", 1180, 512,
                      "Chaque flèche part du système qui ouvre la connexion. Traits pleins : publication et promotion. Pointillés : le proxy cloud.",
                      ["Le workflow de publication fait le push des deux images sur Docker Hub, avec le même digest que sur ghcr.io.",
                       "Artifactory les récupère par son dépôt distant Docker Hub.",
                       "L'hôte de promotion récupère par tag, puis par digest, vérifie la signature et analyse.",
                       "L'hôte de promotion copie par digest dans vcfops-approved.",
                       "L'hôte de promotion installe le pack réécrit.",
                       "Le proxy cloud récupère l'image de l'adaptateur par son digest."], None)
    return finish(p, "How a release reaches the organisation", 1180, 512,
                  "Each arrow starts at the system that opens the connection. Solid: release and promotion. Dashed: the cloud proxy.",
                  ["The release workflow pushes both images to Docker Hub, with the same digest as on ghcr.io.",
                   "Artifactory fetches them through its Docker Hub remote repository.",
                   "The promotion host pulls by tag, then by digest, verifies the signature and scans.",
                   "The promotion host copies by digest into vcfops-approved.",
                   "The promotion host installs the rewritten pack.",
                   "The cloud proxy pulls the adapter image by digest."], None)


def release_path():
    p = [box(40, 110, 200, BH, GREEN, "Maintainer", "commit, tag, push"),
         box(300, 110, 200, BH, BLUE, "Tag v1.x.y", "on GitHub"),
         box(560, 96, 230, 94, BLUE, "release.yml", "mp-build, pack image,", "keyless signatures, SBOM"),
         box(850, 70, 226, BH, AMBER, "ghcr.io/sorinplaton", "nsxfederation, nsxfederation-pak"),
         box(850, 160, 226, BH, AMBER, "docker.io/sorinplaton", "nsxfederation, nsxfederation-pak"),
         box(560, 270, 230, BH, PURPLE, "Draft GitHub release"),
         box(850, 270, 226, BH, PURPLE, "Published release"),
         flow(1, [(240, 143), (296, 143)]), flow(2, [(500, 143), (556, 143)]), flow(3, [(790, 114), (846, 114)]),
         flow(4, [(790, 176), (846, 176)]), flow(5, [(675, 190), (675, 266)]), flow(6, [(790, 303), (846, 303)])]
    return finish(p, "From a Git tag to a published release", 1100, 376, "Release path.",
                  ["The maintainer commits, tags and pushes.",
                   "The tag starts release.yml: mp-build, pack image, keyless signatures, SBOM.",
                   "The workflow pushes both images to ghcr.io.",
                   "The workflow copies both images byte for byte to Docker Hub.",
                   "The workflow creates a draft GitHub release.",
                   "The maintainer checks the draft, adds the key signatures (transition) and publishes."], None)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    made = []
    for name, fn in (("install-option-b", install_option_b), ("install-option-a", install_option_a),
                     ("install-mirror", install_mirror), ("enterprise-path", enterprise_path),
                     ("enterprise-network", enterprise_network)):
        for fr in (False, True):
            made.append((name + (".fr" if fr else "") + ".svg", fn(fr)))
    made.append(("release-path.svg", release_path()))
    for name, text in made:
        open(os.path.join(OUT, name), "w").write(text)
    print("wrote", len(made), "diagrams to", OUT)
