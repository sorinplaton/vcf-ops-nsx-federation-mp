"""Remove what an upgrade of the NSX Federation pack leaves behind in VCF Operations.

An install adds and updates the alert, symptom and recommendation definitions of the pack, but it does not delete
the ones a newer version no longer ships. They stay as orphans: a renamed symptom keeps its old copy, and that old
copy can stay active on objects. This script compares what VCF Operations holds for the adapter kind NSXFederation
with what the PAK ships, and deletes the difference. It only touches the IDs the pack itself generates
(AlertDefinition-NSXFederation-*, SymptomDefinition-NSXFederation-*, Recommendation-df-NSXFederation-*), never a
definition someone created in the UI.

With --legacy-objects it also deletes the objects that pack versions before 1.9.0 keyed by name (edge nodes, RTEP
tunnels, sync channels). From 1.9.0 they are keyed by NSX ID, so the old objects get no more data; deleting them
removes their history.

Without --delete it only reports. Needs OPS_HOST and OPS_PW (an administrator) in the environment; OPS_USER
(default admin) and OPS_AUTH_SOURCE (default local) are optional. TLS is verified against the system store, or
against OPS_CA (a PEM file); --insecure skips the check (lab only).

usage: cleanup_content.py <pak or nsxfederation_alerts.xml> [--delete] [--legacy-objects] [--insecure]
"""
import argparse
import html
import os
import re
import sys
import zipfile

import requests

PREFIXES = {
    "alert": "AlertDefinition-NSXFederation-",
    "symptom": "SymptomDefinition-NSXFederation-",
    "recommendation": "Recommendation-df-NSXFederation-",
}
LEGACY = {"edge_node": ":edge:", "rtep_tunnel": ":rtep:", "sync_channel": ":chan:"}  # identifiers before 1.9.0


def shipped(path):
    """IDs of the alert, symptom and recommendation definitions the PAK (or the alert XML) ships."""
    if path.endswith(".xml"):
        xml = open(path, encoding="utf-8").read()
    else:
        with zipfile.ZipFile(path) as z:
            xml = z.read("content/alertdefs/nsxfederation_alerts.xml").decode("utf-8")
    return {
        "alert": set(re.findall(r'<AlertDefinition [^>]*\bid="([^"]+)"', xml)),
        "symptom": set(re.findall(r'<SymptomDefinition [^>]*\bid="([^"]+)"', xml)),
        "recommendation": set(re.findall(r'<Recommendation [^>]*\bkey="([^"]+)"', xml)),
    }


class Ops:
    def __init__(self, host, user, password, source, verify):
        self.base = f"https://{host}/suite-api"
        self.s = requests.Session()
        self.s.verify = verify
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        r = self.s.post(self.base + "/api/auth/token/acquire",
                        json={"username": user, "password": password, "authSource": source}, timeout=60)
        r.raise_for_status()
        self.s.headers["Authorization"] = "OpsToken " + r.json()["token"]

    def get(self, path, params=None):
        r = self.s.get(self.base + path, params=params, timeout=120)
        r.raise_for_status()
        return r.json()

    def delete(self, path):
        try:
            r = self.s.delete(self.base + path, timeout=120)
        except requests.RequestException as e:  # counted as a failure, the run goes on
            return 0, type(e).__name__
        return r.status_code, r.text[:200]


def installed(ops):
    """(IDs VCF Operations holds per kind, every alert definition of every adapter kind)."""
    alerts = ops.get("/api/alertdefinitions", {"pageSize": 20000}).get("alertDefinitions", [])
    symptoms = ops.get("/api/symptomdefinitions", {"adapterKind": "NSXFederation", "pageSize": 5000})
    recs = ops.get("/api/recommendations", {"pageSize": 20000})
    return {
        "alert": {a["id"] for a in alerts if a.get("adapterKindKey") == "NSXFederation"},
        "symptom": {s["id"] for s in symptoms.get("symptomDefinitions", [])},
        "recommendation": {r["id"] for r in recs.get("recommendations", [])},
    }, alerts


def references(alert):
    """Symptom and recommendation IDs an alert definition uses, at any depth (a "!" negates a symptom)."""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "recommendationPriorityMap" and isinstance(v, dict):
                    out.update(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.lstrip("!").startswith("SymptomDefinition-"):
            out.add(x.lstrip("!"))
    walk(alert.get("states"))
    return out


def plan(ship, have, alerts):
    """What to delete per kind: {kind: (current, orphans to delete, orphans kept because in use, refusal or "")}.
    An orphan that a kept alert definition (made in the UI, or of another pack) still uses is kept. A kind where
    nothing VCF Operations holds matches the PAK, or where most of it would go, is left alone: the IDs are then
    not stored the way this script expects, and deleting would be a guess."""
    orphan_alerts = {i for i in have["alert"] - ship["alert"] if i.startswith(PREFIXES["alert"])}
    used = set()
    for a in alerts:
        if a.get("id") not in orphan_alerts:
            used |= references(a)
    out = {}
    for what in ("alert", "symptom", "recommendation"):
        current = have[what] & ship[what]
        orphans = sorted(i for i in have[what] - ship[what] if i.startswith(PREFIXES[what]))
        in_use = [i for i in orphans if i in used]
        orphans = [i for i in orphans if i not in used]
        refusal = ""
        if orphans and not current:
            refusal = "nothing installed matches the PAK"
        elif len(orphans) > max(5, len(current) // 2):
            refusal = f"{len(orphans)} orphans for {len(current)} current definitions is more than expected"
        out[what] = (current, orphans, in_use, refusal)
    return out


def legacy_objects(ops):
    out = []
    for kind, marker in LEGACY.items():
        page = 0
        while True:
            d = ops.get("/api/resources", {"adapterKind": "NSXFederation", "resourceKind": kind,
                                           "pageSize": 1000, "page": page})
            items = d.get("resourceList", [])
            for res in items:
                ident = next((i.get("value", "") for i in res["resourceKey"].get("resourceIdentifiers", [])
                              if (i.get("identifierType") or {}).get("name") == "id"), "")
                if marker in ident:
                    out.append((kind, res["identifier"], html.unescape(res["resourceKey"].get("name") or "")))
            total = int((d.get("pageInfo") or {}).get("totalCount", 0))
            page += 1
            if not items or page * 1000 >= total:
                break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("content", help="the PAK, or content/alertdefs/nsxfederation_alerts.xml")
    ap.add_argument("--delete", action="store_true", help="delete what is reported (default: report only)")
    ap.add_argument("--legacy-objects", action="store_true",
                    help="also handle the objects keyed by name before 1.9.0 (their history is deleted)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the VCF Operations certificate (lab only)")
    a = ap.parse_args()
    host, password = os.environ.get("OPS_HOST"), os.environ.get("OPS_PW")
    if not host or not password:
        sys.exit("set OPS_HOST and OPS_PW")
    verify = False if a.insecure else (os.environ.get("OPS_CA") or True)
    if a.insecure:
        requests.packages.urllib3.disable_warnings()
    ops = Ops(host, os.environ.get("OPS_USER", "admin"), password, os.environ.get("OPS_AUTH_SOURCE", "local"), verify)

    ship = shipped(a.content)
    if not ship["alert"] or not ship["symptom"]:
        sys.exit("no definitions found in " + a.content)
    have, alerts = installed(ops)
    failed = 0
    todo = plan(ship, have, alerts)
    # alerts first: their symptoms and recommendations are then no longer in use
    for what, path in (("alert", "/api/alertdefinitions/"), ("symptom", "/api/symptomdefinitions/"),
                       ("recommendation", "/api/recommendations/")):
        current, orphans, in_use, refusal = todo[what]
        print(f"{what} definitions: {len(current)} current, {len(orphans) + len(in_use)} orphan")
        for i in in_use:
            print(f"   kept {i}: an alert definition that is not part of the pack uses it")
        if refusal:
            print(f"   NOT cleaned: {refusal}; check by hand")
            failed += 1
            continue
        for i in orphans:
            if not a.delete:
                print("   orphan", i)
                continue
            code, text = ops.delete(path + i)
            ok = code < 300
            failed += not ok
            print(f"   {'deleted' if ok else 'NOT deleted'} {i}" + ("" if ok else f" (HTTP {code} {text})"))
    if a.legacy_objects:
        old = legacy_objects(ops)
        print(f"objects keyed by name (before 1.9.0): {len(old)}")
        for kind, rid, name in old:
            if not a.delete:
                print(f"   {kind}: {name}")
                continue
            code, text = ops.delete(f"/api/resources/{rid}")
            ok = code < 300
            failed += not ok
            print(f"   {'deleted' if ok else 'NOT deleted'} {kind}: {name}" + ("" if ok else f" (HTTP {code} {text})"))
    if not a.delete:
        print("report only: run again with --delete to remove them")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
