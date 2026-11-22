#!/usr/bin/env python3
"""Generate docs/FEATURES.md: every object type, metric, property and alert of the pack.

The lists come from the object model in pack/app/adapter.py and from the tables of gen_alerts.py, gen_views.py
and gen_groups.py, so the document cannot drift from the pack."""
import os
import re
import sys
import typing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_alerts as ga  # noqa: E402
import gen_groups as gg  # noqa: E402
import gen_views as gv  # noqa: E402

_src = open(os.path.join(ROOT, "pack", "app", "adapter.py")).read()
_ns = {k: getattr(typing, k) for k in ("Dict", "Tuple", "List", "Optional", "Any")}
exec(_src[_src.index("RATES = {"):_src.index("def get_adapter_definition")], _ns)
MODEL, GROUPS, KPI, OUTAGE = _ns["MODEL"], _ns["_G"], _ns["KPI"], _ns["OUTAGE_KINDS"]
metric_group, fk, RATE_KEYS = _ns["metric_group"], _ns["fk"], _ns["RATE_KEYS"]
VERSION = re.search(r'"version": "([^"]+)"', open(os.path.join(ROOT, "pack", "manifest.txt")).read()).group(1)
DASHES = [d for d in os.listdir(os.path.join(ROOT, "pack", "content", "dashboards")) if not d.startswith(".")]
SEVERITY = ["info", "warning", "immediate", "critical"]
LANG = "en"
# French for the text this script writes around the names of the pack. Metric, property, alert and symptom names and
# the recommendations stay in English in both documents: that is how VCF Operations shows them.
FR = {
    "below": "inférieur à", "above": "supérieur à", "is": "vaut", "is not": "différent de", "at least": "au moins",
    "at most": "au plus", "contains": "contient", "does not contain": "ne contient pas", "matches": "correspond à",
    " and ": " et ", "; or ": " ; ou ", "(dynamic threshold)": "(seuil dynamique)",
    "learned by VCF Operations (dynamic threshold)": "appris par VCF Operations (seuil dynamique)",
    "quiet while a location it depends on is in outage": "silencieuse tant qu'un emplacement dont elle dépend est en panne",
    "quiet while its Global Manager does not answer": "silencieuse tant que son Global Manager ne répond pas",
    "after %s, clears after %s": "se déclenche après %s, s'efface après %s", "%d cycle%s": "%d cycle%s",
    "%d to %d cycles depending on the condition": "%d à %d cycles selon la condition", "%d to %d": "%d à %d",
    " per hour": " par heure", " per minute": " par minute", " hours": " heures", " days": " jours",
    "yes": "oui", "; ": " ; ", "Properties: ": "Propriétés : ",
}


def _(text):
    return FR.get(text, text) if LANG == "fr" else text


def keep(text):
    """Mark a product string (metric, alert, symptom name, recommendation): nb() leaves it as VCF Operations shows it."""
    return "\x00" + str(text) + "\x01" if LANG == "fr" else str(text)


def nb(text):
    """French typography: a non-breaking space before : ; ? ! % and inside « », outside code spans and links."""
    if LANG != "fr":
        return text
    parts = re.split(r"(\x00[^\x01]*\x01|`[^`]*`|\]\([^)]*\)|https?://\S+)", text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r" ([:;?!%])", "\u00a0\\1", parts[i]).replace("« ", "«\u00a0").replace(" »", "\u00a0»")
    return "".join(parts).replace("\x00", "").replace("\x01", "")
OPS = {"<": "below", ">": "above", "=": "is", "!=": "is not", ">=": "at least", "contains": "contains", "regex": "matches"}
cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")


NEG = {"=": "is not", "!=": "is", "<": "at least", ">": "at most", ">=": "below", "contains": "does not contain"}


def condition(key, negated=False):
    kind, name, sev, ctype, mkey, op, value = ga.S[key]
    if negated and ctype in ("metric", "property") and op in NEG:
        label = dict(MODEL[kind][1] + MODEL[kind][2]).get(mkey, mkey)
        return f"\"{keep(label)}\" {_(NEG[op])} {value}"
    if ctype == "event":
        return keep(name)
    if ctype == "dt":
        return f"{keep(name)} {_('(dynamic threshold)')}"
    label = dict(MODEL[kind][1] + MODEL[kind][2]).get(mkey, mkey) if mkey not in ("location_outage", "gm_outage") else mkey
    return f"{keep(name)} (\"{keep(label)}\" {_(OPS.get(op, op))} {value})"


def alert_rows(kind):
    rows = []
    for k, akind, name, desc, _typ, _sub, sets, rec in ga.A:
        if akind != kind:
            continue
        positive = [[s for s in group if not s.startswith("!")] for group in sets]
        quiet = any(s.startswith("!outage_") for group in sets for s in group)
        gm_quiet = any(s == "!chan_gm_outage" for group in sets for s in group)
        sev = max((ga.S[s][2] for group in positive for s in group), key=SEVERITY.index)
        waits = sorted({ga.WAIT.get(s, (1, 1)) for group in positive for s in group if not s.endswith("_up_now")})
        # conditions that must NOT hold are shown too, except the outage ones (see the notes)
        def part(group):
            shown = [condition(s) for s in group if not s.startswith("!")]
            shown += [condition(s[1:], negated=True) for s in group if s.startswith("!") and "outage" not in s]
            return _(" and ").join(shown)
        trigger = _("; or ").join(part(group) for group in sets)
        notes = []
        if waits != [(1, 1)]:
            if len(waits) == 1:
                notes.append(_("after %s, clears after %s") % ("%d cycle%s" % (waits[0][0], "" if waits[0][0] == 1 else "s"), waits[0][1]))
            else:  # the conditions of the alert do not all wait the same number of cycles
                cancel = sorted({c for _, c in waits})
                ws = sorted({w for w, _ in waits})
                fires = ("%d cycle%s" % (ws[0], "" if ws[0] == 1 else "s") if len(ws) == 1
                         else _("%d to %d cycles depending on the condition") % (ws[0], ws[-1]))
                notes.append(_("after %s, clears after %s") % (
                    fires, "%d" % cancel[0] if len(cancel) == 1 else _("%d to %d") % (cancel[0], cancel[-1])))
        if quiet:
            notes.append(_("quiet while a location it depends on is in outage"))
        if gm_quiet:
            notes.append(_("quiet while its Global Manager does not answer"))
        rows.append(f"| {keep(cell(name))} | {sev.capitalize()} | {'Risk' if k in ga.RISK else 'Health'} | {cell(trigger)}"
                    f"{'. ' + (lambda s: s[:1].upper() + s[1:])(_('; ').join(notes)) if notes else ''} | {keep(cell(ga.R[rec]))} |")
    return rows


UNIT_TEXT = [("_pct", "%"), ("_pct_of_max", "%"), ("_per_hour", " per hour"), ("_ms", " ms"), ("_per_min", " per minute"), ("_hours", " hours"), ("_days_to_expiry", " days"), ("_s", " s")]


def threshold_rows():
    """The symptoms that compare a metric with a number other than 0 or 1, and the dynamic ones: the thresholds
    a team may want to tune. The other symptoms test a state (down, in error, not configured)."""
    alert_of = {}
    for k, kind, name, *_rest in ga.A:
        for group in _rest[3]:
            for sk in group:
                if not sk.startswith("!"):
                    alert_of.setdefault(sk, name)
    rows = []
    for sk, (kind, name, sev, ctype, mkey, op, value) in ga.S.items():
        if not (ctype == "dt" or (ctype == "metric" and (value not in (0, 1) or mkey.endswith("_pct") or mkey in RATE_KEYS))):
            continue
        label = dict(MODEL[kind][1]).get(mkey, mkey)
        unit = _(next((u for suffix, u in UNIT_TEXT if mkey.endswith(suffix)), ""))
        if LANG == "fr" and unit == "%":
            unit = " %"
        limit = _("learned by VCF Operations (dynamic threshold)") if ctype == "dt" else f"{_(OPS.get(op, op))} {value}{unit}"
        wait, cancel = ga.WAIT.get(sk, (1, 1))
        rows.append(f"| {MODEL[kind][0]} | {keep(cell(label))} (`{cell(fk(kind, mkey))}`) | {limit} | {sev.capitalize()} | "
                    f"{wait} / {cancel} | {keep(cell(alert_of.get(sk, '')))} |")
    return rows


def metric_rows(k, header):
    out = []
    if MODEL[k][1]:
        out += header
        for key, label in MODEL[k][1]:
            g = metric_group(k, key)
            full = (g + "\\|" if g else "") + key  # the pipe of "group|key" is escaped for the table
            out.append(f"| {GROUPS[g] if g else ''} | {keep(cell(label))} | `{full}` | {_('yes') if (k, key) in KPI else ''} |")
        out.append("")
    props = list(MODEL[k][2])
    if k in OUTAGE:
        props.append(("location_outage", "Locations in outage"))
    if k == "sync_channel":
        props.append(("gm_outage", "Global Manager outage"))
    if props:
        out += [_("Properties: ") + _("; ").join(f"{keep(cell(label))} (`{key}`)" for key, label in props) + ".", ""]
    return out


def document():
    kinds = list(MODEL)
    n_alerts, n_symptoms = len(ga.A), len(ga.S)
    n_metrics, n_props = sum(len(MODEL[k][1]) for k in kinds), sum(len(MODEL[k][2]) for k in kinds) + len(OUTAGE) + 1
    n_risk = sum(1 for a in ga.A if a[0] in ga.RISK)
    if LANG == "fr":
        out = [f"# Fonctionnalités et alertes du pack NSX Federation, version {VERSION}", "",
               "Généré par `tools/gen_features.py` à partir du modèle d'objets et des tables d'alertes du pack. "
               "Ne pas modifier à la main. Version anglaise : [FEATURES.md](FEATURES.md).", "",
               "Les noms des métriques, des propriétés, des alertes et des symptômes, ainsi que les recommandations, "
               "restent en anglais : c'est ainsi que VCF Operations les affiche.", "",
               "## Résumé", "",
               f"- {len(kinds)} types d'objet, {n_metrics} métriques dans {len(GROUPS)} groupes, {n_props} propriétés, "
               f"{len(KPI)} indicateurs clés.",
               f"- {n_alerts} définitions d'alerte, {n_symptoms} symptômes, {len(ga.R)} recommandations. "
               f"{n_risk} alertes pèsent sur le badge Risk, les autres sur Health.",
               f"- {len(gg.GROUPS)} groupes personnalisés, {len(DASHES)} tableaux de bord, {len(gv.VIEWS)} vues en liste, 1 rapport, "
               "1 arborescence d'inventaire, 1 icône par type d'objet.",
               "- Collecte en lecture seule depuis NSX. Les réglages du compte, l'approbation des certificats, le "
               "dimensionnement et la mise à niveau sont dans [OPERATIONS.md](OPERATIONS.md) (en anglais).", "",
               "## Ce que fait la collecte", "",
               "- Trouve le Global Manager actif à chaque cycle et suit un basculement quand le compte liste les deux "
               "Global Managers.",
               "- Découvre les Local Managers depuis le Global Manager et compare chaque certificat à l'empreinte que "
               "détient le Global Manager, ou (confiance des certificats ca) à une autorité de certification de confiance "
               "et à l'adresse ; chaque connexion est liée au certificat vérifié.",
               "- Identifie les edge nodes et les tunnels RTEP par l'ID NSX du transport node, et les canaux de "
               "synchronisation par l'ID de site : un renommage garde l'objet et son historique.",
               "- Continue de collecter les Local Managers quand aucun Global Manager ne répond, à partir de la liste des "
               "emplacements des cycles précédents (cache de l'adaptateur, puis VCF Operations).",
               "- Signale un appel NSX en échec comme une donnée inconnue : rien n'est envoyé pour les valeurs concernées, "
               "et l'appel est listé sur l'objet fédération.",
               "- Respecte un quota d'appels par manager, ralentit sur les réponses HTTP 429 et 503, retente une fois après un "
               "dépassement de délai et ignore pour le reste du cycle un manager qui ne répond toujours pas.",
               "- Calcule des taux à partir des compteurs NSX, et des agrégats pour toute la fédération.",
               "- Lit la latence et le remplissage des files de synchronisation de chaque emplacement dans les détails "
               "des flux du Global Manager, et compare la latence au RTT maximal défini dans NSX.",
               "- Calcule la part des paquets rejetés par chaque tunnel RTEP, et le taux de coupures des sessions BGP "
               "du Tier-0 et des sessions BGP intersites.",
               "- Lit la réalisation de chaque objet étendu à chaque cycle jusqu'à 200 objets, et par balayage sur six "
               "cycles au-delà.",
               "- Laisse les alarmes de fédération d'un Local Manager à l'intégration NSX native quand celle-ci le "
               "supervise, et relie ses nœuds Edge et ses clusters Edge aux objets natifs.",
               "- Garde silencieuses les alertes des objets dépendants tant qu'un emplacement est coupé du Global Manager.",
               "- Dit si un Global Manager standby peut prendre le relais\u00a0: prêt, pas prêt avec les raisons (synchronisation avec l'actif, état du cluster, version, certificat expiré, mise à niveau en cours ou en pause) ou inconnu quand une valeur n'a pas pu être lue. La métrique vaut 1 seulement quand c'est confirmé.",
               "- Supprime en option les objets qui ont quitté NSX (option désactivée par défaut).", "",
               "## Types d'objet", "",
               "| Type d'objet | Clé | Métriques | Propriétés | Définitions d'alerte |", "| --- | --- | --- | --- | --- |"]
    else:
        out = [f"# Features and alerts of the NSX Federation pack, version {VERSION}", "",
               "Generated by `tools/gen_features.py` from the object model and the alert tables of the pack. "
               "Do not edit by hand. Version française : [FEATURES.fr.md](FEATURES.fr.md).", "",
               "## Summary", "",
               f"- {len(kinds)} object types, {n_metrics} metrics in {len(GROUPS)} groups, {n_props} properties, "
               f"{len(KPI)} key indicators.",
               f"- {n_alerts} alert definitions, {n_symptoms} symptoms, {len(ga.R)} recommendations. "
               f"{n_risk} alerts count against the Risk badge, the others against Health.",
               f"- {len(gg.GROUPS)} custom groups, {len(DASHES)} dashboards, {len(gv.VIEWS)} list views, 1 report, 1 inventory tree, "
               "an icon per object type.",
               "- Read-only collection from NSX. Account settings, certificate trust, sizing and upgrade are in "
               "[OPERATIONS.md](OPERATIONS.md).", "",
               "## What the collection does", "",
               "- Finds the active Global Manager in every cycle and follows a switchover when the account lists both "
               "Global Managers.",
               "- Discovers the Local Managers from the Global Manager and checks each certificate against the thumbprint "
               "the Global Manager holds, or (certificate trust ca) against a trusted CA and the address; every "
               "connection is bound to the certificate that was checked.",
               "- Keys edge nodes and RTEP tunnels by NSX transport node ID and sync channels by site ID, so a rename "
               "keeps the object and its history.",
               "- Keeps collecting the Local Managers when no Global Manager answers, from the location list of earlier "
               "cycles (adapter cache, then VCF Operations).",
               "- Reports a failed NSX call as unknown data: nothing is sent for the values behind it, and the call is "
               "listed on the federation object.",
               "- Works within a call budget per manager, backs off on HTTP 429 and 503, retries a timeout once and skips "
               "a manager that keeps timing out for the rest of the cycle.",
               "- Computes rates from the NSX counters, and rollups for the whole federation.",
               "- Reads the latency and the fill of the sync queues of each location from the flow details of the Global "
               "Manager, and compares the latency with the maximum RTT set in NSX.",
               "- Computes the share of packets each RTEP tunnel drops, and the drop rate of the Tier-0 BGP sessions and "
               "of the inter-site BGP sessions.",
               "- Reads the realization of every stretched object in every cycle up to 200 objects, and in a sweep of six "
               "cycles above that.",
               "- Leaves the federation alarms of a Local Manager to the built-in NSX integration when that integration "
               "monitors it, and links its edge nodes and edge clusters to the built-in objects.",
               "- Keeps the alerts of dependent objects quiet while a location is cut off from the Global Manager.",
               "- Tells whether a standby Global Manager can take over: ready, not ready with the reasons (sync with the active, cluster health, version, expired certificate, upgrade in progress or paused), or unknown when a value could not be read. The metric is 1 only when that is confirmed.",
               "- Optionally deletes the objects that left NSX (off by default).", "",
               "## Object types", "",
               "| Object type | Key | Metrics | Properties | Alert definitions |", "| --- | --- | --- | --- | --- |"]
    for k in kinds:
        props = len(MODEL[k][2]) + (k in OUTAGE) + (k == "sync_channel")
        out.append(f"| {MODEL[k][0]} | `{k}` | {len(MODEL[k][1])} | {props} | {sum(1 for a in ga.A if a[1] == k)} |")
    if LANG == "fr":
        out += ["", "## Seuils", "",
                "Les symptômes ci-dessous comparent une métrique à un nombre ou à la plage habituelle de la métrique. Tous "
                "les autres symptômes testent un état : un compteur supérieur à 0 (sessions tombées, nœuds hors ligne, objets "
                "en erreur), une valeur inférieure à 1 (non connecté, non établi, non réalisé) ou la valeur d'une propriété "
                "(état du cluster, état de la mise à niveau, mode). « Déclenchement / effacement » donne le nombre de cycles de "
                "collecte pendant lesquels la condition doit rester vraie avant que le symptôme se déclenche, puis le nombre "
                "de cycles pendant lesquels elle doit avoir disparu avant qu'il s'efface.", "",
                "| Type d'objet | Métrique | Seuil | Criticité | Déclenchement / effacement (cycles) | Alerte |",
                "| --- | --- | --- | --- | --- | --- |"] + threshold_rows() + [
                "",
                "Les alertes issues des alarmes de fédération de NSX utilisent les seuils de NSX lui-même, par exemple une "
                "latence au-dessus de la limite de NSX pendant plus de 2 minutes, ou une synchronisation entre emplacements "
                "en échec depuis 3 minutes (Warning) ou 15 minutes (Immediate). La façon de changer un seuil du pack est "
                "décrite dans [OPERATIONS.md](OPERATIONS.md#changing-a-threshold) (en anglais).", "",
                "## Alertes", "",
                "La criticité indiquée est la plus haute parmi celles des symptômes qui déclenchent l'alerte. Les « cycles » sont des "
                "cycles de collecte (5 minutes par défaut) ; sauf mention contraire, une alerte se déclenche et s'efface en un cycle.", ""]
        for k in kinds:
            rows = alert_rows(k)
            if rows:
                out += [f"### {MODEL[k][0]}", "", "| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |",
                        "| --- | --- | --- | --- | --- |"] + rows + [""]
        out += ["## Métriques et propriétés", ""]
        for k in kinds:
            out += [f"### {MODEL[k][0]}", ""] + metric_rows(k, ["| Groupe | Métrique | Clé | Indicateur clé |", "| --- | --- | --- | --- |"])
        out += ["## Tableaux de bord, vues, rapport et groupes", "",
                "- Tableaux de bord (dossier « NSX Federation ») : Federation Overview, Location Matrix, Switchover Readiness, Inter-site Connectivity, Stretched Objects.",
                "- Vues en liste : " + _("; ").join(keep(v[0]) for v in gv.VIEWS) + ".",
                f"- Rapport : {gv.REPORT[0]}.",
                "- Groupes personnalisés : " + _("; ").join(keep(g[0] if isinstance(g, (list, tuple)) else str(g)) for g in gg.GROUPS) + ".",
                "- Arborescence d'inventaire : NSX Federation (Inventory, Detailed View, dernier onglet).", ""]
        return "\n".join(nb(line) for line in out)
    out += ["", "## Thresholds", "",
            "The symptoms below compare a metric with a number or with the usual range of the metric. Every other "
            "symptom tests a state: a count above 0 (sessions down, nodes offline, objects in error), a value below 1 "
            "(not connected, not established, not realized) or a property value (cluster status, upgrade status, "
            "mode). \"Fires / clears\" is the number of collection cycles the condition has to hold before the "
            "symptom triggers, and has to be gone before it clears.", "",
            "| Object type | Metric | Threshold | Severity | Fires / clears (cycles) | Alert |",
            "| --- | --- | --- | --- | --- | --- |"] + threshold_rows() + [
            "",
            "The alerts for the federation alarms of NSX use the thresholds of NSX itself, for example a latency above "
            "the NSX limit for more than 2 minutes, or a sync between locations that failed for 3 minutes (warning) or "
            "15 minutes (immediate). How to change a threshold of the pack is in [OPERATIONS.md](OPERATIONS.md#changing-a-threshold).", ""]
    out += ["## Alerts", "",
            "Severity is the highest severity of the symptoms that trigger the alert. \"Cycles\" are collection "
            "cycles (5 minutes by default); without a note an alert fires and clears in one cycle.", ""]
    for k in kinds:
        rows = alert_rows(k)
        if rows:
            out += [f"### {MODEL[k][0]}", "", "| Alert | Severity (up to) | Badge | Fires when | Recommendation |",
                    "| --- | --- | --- | --- | --- |"] + rows + [""]
    out += ["## Metrics and properties", ""]
    for k in kinds:
        out += [f"### {MODEL[k][0]}", ""] + metric_rows(k, ["| Group | Metric | Key | Key indicator |", "| --- | --- | --- | --- |"])
    out += ["## Dashboards, views, report and groups", "",
            "- Dashboards (folder \"NSX Federation\"): Federation Overview, Location Matrix, Switchover Readiness, Inter-site Connectivity, Stretched Objects.",
            "- List views: " + "; ".join(v[0] for v in gv.VIEWS) + ".",
            f"- Report: {gv.REPORT[0]}.",
            "- Custom groups: " + "; ".join(g[0] if isinstance(g, (list, tuple)) else str(g) for g in gg.GROUPS) + ".",
            "- Inventory tree: NSX Federation (Inventory, Detailed View, last tab).", ""]
    return "\n".join(out)


if __name__ == "__main__":
    for LANG, name in (("en", "FEATURES.md"), ("fr", "FEATURES.fr.md")):
        path = os.path.join(ROOT, "docs", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(document())
        print("wrote", path)
