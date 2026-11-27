#!/usr/bin/env python3
"""Draw the two diagrams of docs/BASE-IMAGE.md, in English and French.

Writes docs/img/base-image.svg (the adapter image on Debian 12 and on Wolfi) and docs/img/repository-and-proxy.svg
(what goes into the organisation's repository and what runs on the cloud proxy), each with a .fr.svg version, in
the style of tools/gen_diagrams.py. No dependency.
"""
import html
import os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img")
INK, MUTED, LINE = "#18212b", "#5a6675", "#c9d1db"
BLUE, GREEN, PURPLE, AMBER, ORANGE, RED = "#0b5cad", "#1e7a50", "#57478c", "#9a5b00", "#b45309", "#b3261e"
MONO = 'font-family="ui-monospace,Menlo,Consolas,monospace"'
DASH = 'stroke-dasharray="6 5"'
e = html.escape


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


def label(x, y, text, colour=MUTED, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-size="12" font-weight="600" fill="{colour}" text-anchor="{anchor}" '
            f'letter-spacing=".04em">{e(text.upper())}</text>\n')


def box(x, y, w, h, colour, title, *lines, dashed=False, mono_first=False):
    o = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#ffffff" stroke="{colour}" stroke-width="2" '
         f'{DASH if dashed else ""}/>'
         f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="{colour}"/>'
         f'<text x="{x + 18}" y="{y + 25}" font-size="15" font-weight="600" fill="{INK}">{e(title)}</text>')
    for i, line in enumerate(lines):
        mono = mono_first and i == 0
        o += (f'<text x="{x + 18}" y="{y + 44 + 16 * i}" font-size="{11.3 if mono else 12.3}" fill="{MUTED}" '
              f'{MONO if mono else ""}>{e(line)}</text>')
    return o + "\n"


def result(x, y, w, colour, big, small):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="62" rx="10" fill="#ffffff" stroke="{colour}" stroke-width="2"/>'
            f'<text x="{x + w / 2}" y="{y + 28}" font-size="19" font-weight="700" fill="{colour}" text-anchor="middle">'
            f'{e(big)}</text>'
            f'<text x="{x + w / 2}" y="{y + 49}" font-size="12.5" fill="{MUTED}" text-anchor="middle">{e(small)}</text>\n')


def arrow(pts, n=None, dashed=False):
    """An arrow along pts; with n, a numbered disc on its middle."""
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    o = f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="2" {DASH if dashed else ""} marker-end="url(#a)"/>'
    if n is not None:
        bx, by = (pts[0][0] + pts[-1][0]) / 2 - 2, (pts[0][1] + pts[-1][1]) / 2
        o += disc(bx, by, n, ring=True)
    return o + "\n"


def disc(x, y, n, ring=False):
    halo = 'stroke="#ffffff" stroke-width="2.5"' if ring else ""  # a white ring where the disc sits on an arrow
    return (f'<circle cx="{x}" cy="{y}" r="{12 if ring else 11}" fill="{INK}" {halo}/>'
            f'<text x="{x}" y="{y + 4.5}" font-size="13" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>')


# ---- diagram 1: the adapter image on Debian 12 and on Wolfi -------------------------------------------------------
BASE = {
    "en": {
        "title": "The adapter image on Debian 12 and on Wolfi",
        "left": "Up to 1.13.6: one stage", "left_stage": "The whole image is shipped",
        "right": "From 1.13.7: two stages", "stage1": "Stage 1 · build only, not shipped", "stage2": "Stage 2 · shipped",
        "code": "Adapter code", "repo": "this repository", "same": "the same files",
        "pkgs": "Python packages", "pkgs_sub": "the SDK server and the adapter's, locked",
        "deb_full": ("the SDK's base-adapter image:", "a full operating system, with its", "package tools and system libraries"),
        "deb_stage": ("the SDK's base-adapter image,", "unchanged: mp-build builds here", "as the SDK expects"),
        "wolfi": ("pinned by digest: Python 3.11", "and the few libraries it needs,", "nothing else"),
        "copied": "copied", "left_behind": ("left", "behind"),
        "r_deb": ("102 HIGH or CRITICAL", "no fixed version in Debian 12 · 405 MB"),
        "r_stage": ("Stays on the build host", "its findings do not reach the cloud proxy"),
        "r_wolfi": ("No finding", "Trivy report of 1.13.7 · about 220 MB"),
        "legend": ("The same in every release", "Debian 12, the SDK's base", "Wolfi, the base that ships", "Build stage, not shipped"),
        "note": "Figures: Trivy reports of releases 1.13.5 (Debian 12) and 1.13.7 (Wolfi), HIGH and CRITICAL findings; "
                "image sizes as in the changelog.",
    },
    "fr": {
        "title": "L'image de l'adaptateur sur Debian 12 et sur Wolfi",
        "left": "Jusqu'à 1.13.6 : une étape", "left_stage": "Toute l'image est livrée",
        "right": "À partir de 1.13.7 : deux étapes", "stage1": "Étape 1 · construction, non livrée", "stage2": "Étape 2 · livrée",
        "code": "Code de l'adaptateur", "repo": "ce dépôt", "same": "les mêmes fichiers",
        "pkgs": "Paquets Python", "pkgs_sub": "serveur du SDK et adaptateur, figés",
        "deb_full": ("l'image base-adapter du SDK :", "un système complet, avec ses outils", "de paquets et ses bibliothèques"),
        "deb_stage": ("l'image base-adapter du SDK,", "inchangée : mp-build construit", "ici, comme le SDK l'attend"),
        "wolfi": ("épinglée par digest : Python 3.11", "et les quelques bibliothèques", "qu'il lui faut, rien d'autre"),
        "copied": "copié", "left_behind": ("laissé", "de côté"),
        "r_deb": ("102 HIGH ou CRITICAL", "sans version corrigée · 405 Mo"),
        "r_stage": ("Reste à la construction", "ses failles n'arrivent pas au proxy cloud"),
        "r_wolfi": ("Aucune vulnérabilité", "rapport Trivy de 1.13.7 · environ 220 Mo"),
        "legend": ("Identique dans chaque version", "Debian 12, la base du SDK", "Wolfi, la base livrée",
                   "Étape de construction, non livrée"),
        "note": "Chiffres : rapports Trivy des versions 1.13.5 (Debian 12) et 1.13.7 (Wolfi), vulnérabilités HIGH et "
                "CRITICAL ; tailles d'image du journal des modifications.",
    },
}


def base_image(t):
    w, h, zh = 1120, 590, 412
    o = head(w, h, t["title"])
    o += zone(28, 64, 330, zh, t["left"], internet=True)
    o += label(193, 118, t["left_stage"], AMBER, "middle")
    o += box(48, 132, 290, 58, BLUE, t["code"], t["repo"])
    o += box(48, 198, 290, 58, BLUE, t["pkgs"], t["pkgs_sub"])
    o += box(48, 264, 290, 92, RED, "Debian 12", *t["deb_full"])
    o += result(48, 394, 290, RED, *t["r_deb"])

    o += zone(386, 64, 706, zh, t["right"])
    o += label(551, 118, t["stage1"], MUTED, "middle")
    o += label(927, 118, t["stage2"], GREEN, "middle")
    o += box(406, 132, 290, 58, BLUE, t["code"], t["repo"], dashed=True)
    o += box(406, 198, 290, 58, BLUE, t["pkgs"], t["pkgs_sub"], dashed=True)
    o += box(406, 264, 290, 92, RED, "Debian 12", *t["deb_stage"], dashed=True)
    o += box(782, 132, 290, 58, BLUE, t["code"], t["same"])
    o += box(782, 198, 290, 58, BLUE, t["pkgs"], t["same"])
    o += box(782, 264, 290, 92, GREEN, "Wolfi", *t["wolfi"])
    for y in (161, 227):
        o += arrow([(696, y), (778, y)])
        o += f'<text x="737" y="{y - 9}" font-size="12" fill="{INK}" text-anchor="middle">{e(t["copied"])}</text>\n'
    for i, word in enumerate(t["left_behind"]):
        o += f'<text x="739" y="{306 + 16 * i}" font-size="12" fill="{RED}" text-anchor="middle">{e(word)}</text>\n'
    o += result(406, 394, 290, MUTED, *t["r_stage"])
    o += result(782, 394, 290, GREEN, *t["r_wolfi"])

    ly = 506
    marks = ((BLUE, False), (RED, False), (GREEN, False), (MUTED, True))
    for i, ((colour, dashed), text) in enumerate(zip(marks, t["legend"])):
        x = 28 + 266 * i
        o += (f'<rect x="{x}" y="{ly}" width="26" height="16" rx="4" fill="#ffffff" stroke="{colour}" stroke-width="2" '
              f'{DASH if dashed else ""}/>'
              f'<text x="{x + 36}" y="{ly + 13}" font-size="12.5" fill="{INK}">{e(text)}</text>\n')
    o += f'<text x="28" y="{ly + 50}" font-size="12" fill="{MUTED}">{e(t["note"])}</text>\n'
    return o + "</svg>\n"


# ---- diagram 2: what goes into the repository and what runs on the cloud proxy ------------------------------------
REPO = {
    "en": {
        "title": "What goes into your repository and what runs on the cloud proxy",
        "published": "Published", "hub": "Docker Hub and ghcr.io",
        "art": "Your Artifactory", "remote": "Remote: Docker Hub cache", "approved": "vcfops-approved: local",
        "site": "Your VCF Operations site",
        "pak": "Pack image", "adapter": "Adapter image", "sigs": "Signatures",
        "pak_pub": ("the .pak in an image", "built FROM scratch"),
        "adapter_pub": ("the collector: Wolfi, Python,", "SDK server, adapter code"),
        "sigs_pub": ("sha256-<digest>.sig and .att", "signature, SBOM and", "build provenance"),
        "cached": "cached on first pull", "read": "read in the check",
        "pak_ok": (".pak and SHA256SUMS,", "cannot run"),
        "adapter_ok": ("same digest as published,", "about 220 MB"),
        "sigs_ok": ("optional, with cosign copy,", "to check again later"),
        "between": ("Between the two: the signatures are checked (step 4) and the images",
                    "scanned (step 5), then both are copied by digest (step 6)."),
        "ops": ("the .pak, pointed at Artifactory:", "content, alerts, dashboards and", "the address of the adapter image"),
        "proxy": "Cloud proxy", "container": "Adapter container",
        "container_sub": ("the adapter image, pulled by digest", "Wolfi · Python 3.11 · SDK server",
                          "Python packages · adapter code", "collects from NSX every cycle"),
        "cert": "Registry certificate", "cert_sub": "only if Artifactory has an internal CA",
        "steps": (
            "Artifactory pulls the release from Docker Hub through its remote repository: two images and their signatures.",
            "After the signature check and the scan, both images are copied by digest into vcfops-approved.",
            "The .pak is taken out of the pack image, pointed at Artifactory (repack.py) and uploaded to VCF Operations.",
            "When the account starts, the cloud proxy pulls the adapter image by digest from vcfops-approved and runs it.",
        ),
        "never": "Never on the cloud proxy",
        "never_sub": ("The pack image and the .pak file (the .pak goes to VCF Operations), the signatures, and the build "
                      "stage of the adapter image (Debian 12 and the SDK tools).",),
    },
    "fr": {
        "title": "Ce qui entre dans votre dépôt et ce qui s'exécute sur le proxy cloud",
        "published": "Publié", "hub": "Docker Hub et ghcr.io",
        "art": "Votre Artifactory", "remote": "Distant : cache de Docker Hub", "approved": "vcfops-approved : local",
        "site": "Votre site VCF Operations",
        "pak": "Image du pack", "adapter": "Image de l'adaptateur", "sigs": "Signatures",
        "pak_pub": ("le .pak dans une image", "construite FROM scratch"),
        "adapter_pub": ("le collecteur : Wolfi, Python,", "serveur du SDK, code"),
        "sigs_pub": ("sha256-<digest>.sig et .att", "signature, SBOM et", "provenance de construction"),
        "cached": "en cache au premier accès", "read": "lues pour la vérification",
        "pak_ok": (".pak et SHA256SUMS,", "ne peut pas s'exécuter"),
        "adapter_ok": ("même digest que l'image publiée,", "environ 220 Mo"),
        "sigs_ok": ("facultatif, avec cosign copy,", "pour revérifier plus tard"),
        "between": ("Entre les deux : les signatures sont vérifiées (étape 4 du guide) et les",
                    "images analysées (étape 5), puis copiées par digest (étape 6)."),
        "ops": ("le .pak, pointé vers Artifactory :", "contenu, alertes, tableaux de bord", "et adresse de l'image de l'adaptateur"),
        "proxy": "Proxy cloud", "container": "Conteneur de l'adaptateur",
        "container_sub": ("l'image de l'adaptateur, par digest", "Wolfi · Python 3.11 · serveur du SDK",
                          "paquets Python · code de l'adaptateur", "collecte NSX à chaque cycle"),
        "cert": "Certificat du registre", "cert_sub": "si Artifactory a une autorité interne",
        "steps": (
            "Artifactory récupère la version sur Docker Hub via son dépôt distant : deux images et leurs signatures.",
            "Après la vérification des signatures et l'analyse, les deux images sont copiées par digest dans vcfops-approved.",
            "Le .pak est sorti de l'image du pack, pointé vers Artifactory (repack.py) et téléversé dans VCF Operations.",
            "Au démarrage du compte, le proxy cloud récupère l'image de l'adaptateur par digest dans vcfops-approved et l'exécute.",
        ),
        "never": "Jamais sur le proxy cloud",
        "never_sub": ("L'image du pack et le fichier .pak (le .pak va dans VCF Operations), les signatures, et l'étape de",
                      "construction de l'image de l'adaptateur (Debian 12 et les outils du SDK)."),
    },
}


def repository_and_proxy(t):
    w = 1232
    zy, zh, bh = 64, 452, 88
    r1, r2, r3 = 132, 236, 340
    ax, aw, bx, bw, cx, cw = 28, 264, 316, 520, 860, 344
    a_x, a_w = ax + 16, aw - 32
    r_x, r_w = bx + 16, 200
    p_x, p_w = bx + 16 + 200 + 52, 236
    c_x, c_w = cx + 20, cw - 40
    never_h = 48 + 18 * len(t["never_sub"])
    h = 548 + 26 * 4 + 12 + never_h + 24
    o = head(w, h, t["title"])

    o += zone(ax, zy, aw, zh, t["published"], internet=True)
    o += label(a_x, 112, t["hub"])
    o += box(a_x, r1, a_w, bh, PURPLE, t["pak"], "nsxfederation-pak:<release>", *t["pak_pub"], mono_first=True)
    o += box(a_x, r2, a_w, bh, ORANGE, t["adapter"], "nsxfederation@sha256:…", *t["adapter_pub"], mono_first=True)
    o += box(a_x, r3, a_w, bh, MUTED, t["sigs"], *t["sigs_pub"], mono_first=True)

    o += zone(bx, zy, bw, zh, t["art"])
    o += label(r_x, 112, t["remote"])
    o += label(p_x, 112, t["approved"], BLUE)
    for y, title, line in ((r1, t["pak"], t["cached"]), (r2, t["adapter"], t["cached"]), (r3, t["sigs"], t["read"])):
        o += box(r_x, y, r_w, bh, MUTED, title, line, dashed=True)
    o += box(p_x, r1, p_w, bh, PURPLE, t["pak"], "nsxfederation-pak:<release>", *t["pak_ok"], mono_first=True)
    o += box(p_x, r2, p_w, bh, ORANGE, t["adapter"], "nsxfederation:<release>", *t["adapter_ok"], mono_first=True)
    o += box(p_x, r3, p_w, bh, MUTED, t["sigs"], *t["sigs_ok"], dashed=True)
    for i, line in enumerate(t["between"]):
        o += f'<text x="{r_x}" y="{462 + 18 * i}" font-size="12.3" fill="{MUTED}">{e(line)}</text>\n'

    for i, y in enumerate((r1, r2, r3)):
        cy = y + bh / 2
        o += arrow([(a_x + a_w, cy), (r_x - 4, cy)], 1 if i == 1 else None)
        o += arrow([(r_x + r_w, cy), (p_x - 4, cy)], 2 if i == 1 else None, dashed=(i == 2))

    o += zone(cx, zy, cw, zh, t["site"])
    o += box(c_x, r1, c_w, bh, BLUE, "VCF Operations", *t["ops"])
    py = r2 - 4
    o += (f'<rect x="{c_x}" y="{py}" width="{c_w}" height="248" rx="10" fill="#ffffff" stroke="{GREEN}" stroke-width="2"/>'
          f'<rect x="{c_x}" y="{py}" width="6" height="248" rx="3" fill="{GREEN}"/>'
          f'<text x="{c_x + 18}" y="{py + 25}" font-size="15" font-weight="600" fill="{INK}">{e(t["proxy"])}</text>\n')
    i_x, i_w = c_x + 18, c_w - 34
    o += box(i_x, py + 38, i_w, 112, ORANGE, t["container"], *t["container_sub"])
    o += box(i_x, py + 160, i_w, 74, MUTED, t["cert"], "/etc/docker/certs.d/…/ca.crt", t["cert_sub"], mono_first=True)
    o += arrow([(p_x + p_w, r1 + bh / 2), (c_x - 4, r1 + bh / 2)], 3)
    o += arrow([(p_x + p_w, r2 + 54), (i_x - 4, r2 + 54)], 4)

    ly = 548
    for i, text in enumerate(t["steps"]):
        y = ly + 26 * i
        o += disc(40, y, i + 1) + f'<text x="60" y="{y + 4.5}" font-size="13.5" fill="{INK}">{e(text)}</text>\n'
    ny = ly + 26 * 4 + 12
    o += (f'<rect x="28" y="{ny}" width="{w - 56}" height="{never_h}" rx="10" fill="#fdf2f2" stroke="#e8c4c1"/>'
          f'<text x="46" y="{ny + 26}" font-size="14" font-weight="600" fill="{RED}">{e(t["never"])}</text>')
    for i, line in enumerate(t["never_sub"]):
        o += f'<text x="46" y="{ny + 48 + 18 * i}" font-size="13" fill="{INK}">{e(line)}</text>'
    return o + "\n</svg>\n"


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, texts, draw in (("base-image", BASE, base_image), ("repository-and-proxy", REPO, repository_and_proxy)):
        for lang, t in texts.items():
            path = os.path.join(OUT, name + ("" if lang == "en" else ".fr") + ".svg")
            with open(path, "w", encoding="utf-8") as fd:
                fd.write(draw(t))
            print("wrote", os.path.relpath(path))


if __name__ == "__main__":
    main()
