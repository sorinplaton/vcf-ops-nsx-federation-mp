#!/usr/bin/env python3
"""Collect what a support request about the NSX Federation pack needs, from VCF Operations, into one JSON file,
and print the problems it finds with what to check.

It reads, and never changes: the VCF Operations version, the installed pack version, each account of the pack (its
settings, state, collector and last collection), the collectors, the objects per type, the collection health of each
federation object (failed calls, data gaps, call budget, errors per Global Manager) and the open alerts on the
objects of the pack. It never reads a credential: the file holds the settings of each account, host names and
addresses, not user names or passwords. Read it before sending it outside the organization.

Needs OPS_HOST and OPS_PW in the environment (a read-only VCF Operations user is enough); OPS_USER,
OPS_AUTH_SOURCE and OPS_CA are optional, like cleanup_content.py.

usage: diagnostics.py [--out FILE] [--insecure]
"""
import argparse
import datetime
import json
import os
import sys
import time

import requests

from cleanup_content import Ops
from federation_groups import all_pages

AK = "NSXFederation"
HEALTH_STATS = ["collection|data_gaps", "collection|api_errors", "collection|api_calls", "collection|api_calls_throttled",
                "collection|managers_over_budget", "collection|collection_duration_s",
                "global_managers|global_managers_reachable", "global_managers|global_managers_configured",
                "global_managers|global_managers_active", "locations|locations_collected", "locations|locations_total"]
HEALTH_PROPS = ["collection_mode", "failed_calls", "global_manager_errors", "vcf_operations_api", "location_outage",
                "active_global_manager", "federation_id"]


def latest_stats(ops, rid):
    doc = ops.get(f"/api/resources/{rid}/stats/latest")
    out = {}
    for v in doc.get("values", []):
        for s in v.get("stat-list", {}).get("stat", []):
            if s.get("data"):
                out[s["statKey"]["key"]] = s["data"][-1]
    return out


def findings(report, now_ms):
    """[(problem, what to check)] read from the report."""
    out = []
    for inst in report["accounts"]:
        name = inst["name"]
        if inst["state"] not in ("STARTED", None):
            out.append((f"account {name} is {inst['state']}", "Integrations, Accounts: start it, or read its message"))
        if inst.get("message"):
            out.append((f"account {name}: {inst['message']}", "the account test (Validate Connection) names the cause"))
        last = inst.get("last_collected_ms")
        interval = (inst.get("interval_min") or 5) * 60_000
        if last and now_ms - last > 3 * interval:
            out.append((f"account {name} last collected {round((now_ms - last) / 60_000)} minutes ago",
                        "the collector of the account (Cloud Proxies page) and the adapter log on it"))
        if inst.get("collector_state") not in ("UP", None):
            out.append((f"collector {inst.get('collector')} of {name} is {inst['collector_state']}",
                        "the cloud proxy: power, network, and its page under Administration, Cloud Proxies"))
    stale = sum(report.get("objects_without_data", {}).values())
    if stale:
        out.append((f"{stale} object(s) of the pack receive no data: "
                     + ", ".join(f"{n} {k}" for k, n in report["objects_without_data"].items()),
                    "objects that left NSX (setting delete_stale_after_days), or edge nodes, RTEP tunnels and sync "
                    "channels keyed by name before 1.9.0 (cleanup_content.py --legacy-objects)"))
    for fed in report["federations"]:
        st, pr, name = fed["stats"], fed["properties"], fed["name"]
        if st.get("collection|data_gaps"):
            out.append((f"{name}: {int(st['collection|data_gaps'])} NSX API calls without an answer",
                        f"property failed_calls: {pr.get('failed_calls')}"))
        if st.get("collection|managers_over_budget"):
            out.append((f"{name}: a manager used its whole API call budget", "raise call_budget or set rule_policies empty"))
        if st.get("collection|api_calls_throttled"):
            out.append((f"{name}: NSX throttled API calls", "NSX API rate limits, and other clients of the same managers"))
        if pr.get("global_manager_errors") not in (None, "none"):
            out.append((f"{name}: {pr['global_manager_errors']}", "the address, certificate and account of that Global Manager"))
        if pr.get("collection_mode") not in (None, "normal"):
            out.append((f"{name}: collection mode {pr['collection_mode']}", "OPERATIONS.md, Collection health"))
        if pr.get("vcf_operations_api") not in (None, "ok"):
            out.append((f"{name}: VCF Operations API from the adapter: {pr['vcf_operations_api']}",
                        "OPERATIONS.md, What the adapter reads from VCF Operations"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=f"nsxfederation-diagnostics-{datetime.datetime.now():%Y%m%d-%H%M}.json")
    ap.add_argument("--insecure", action="store_true", help="do not verify the VCF Operations certificate (lab only; or OPS_INSECURE=1)")
    a = ap.parse_args()
    a.insecure = a.insecure or os.environ.get("OPS_INSECURE") == "1"  # same switch as install_unsigned.sh
    host, password = os.environ.get("OPS_HOST"), os.environ.get("OPS_PW")
    if not host or not password:
        sys.exit("set OPS_HOST and OPS_PW")
    verify = False if a.insecure else (os.environ.get("OPS_CA") or True)
    if a.insecure:
        requests.packages.urllib3.disable_warnings()
    ops = Ops(host, os.environ.get("OPS_USER", "admin"), password, os.environ.get("OPS_AUTH_SOURCE", "local"), verify)
    now_ms = int(time.time() * 1000)

    version = ops.get("/api/versions/current")
    solution = next((s for s in ops.get("/api/solutions").get("solution", []) if s.get("id", "").endswith(AK)), {})
    collectors = {str(c.get("id")): c for c in ops.get("/api/collectors").get("collector", [])}
    resources = all_pages(ops, "/api/resources", {"adapterKind": AK}, "resourceList")
    states = {r["identifier"]: (r.get("resourceStatusStates") or [{}])[0] for r in resources}
    kinds, no_data = {}, {}
    for r in resources:
        k = r["resourceKey"]["resourceKindKey"]
        kinds[k] = kinds.get(k, 0) + 1
        if states[r["identifier"]].get("resourceStatus") != "DATA_RECEIVING":
            no_data[k] = no_data.get(k, 0) + 1

    accounts = []
    for inst in ops.get("/api/adapters", {"adapterKindKey": AK}).get("adapterInstancesInfoDto", []):
        col = collectors.get(str(inst.get("collectorId")), {})
        st = states.get(inst["id"], {})
        accounts.append({
            "name": inst["resourceKey"]["name"], "id": inst["id"],
            "settings": {i["identifierType"]["name"]: i.get("value") for i in inst["resourceKey"].get("resourceIdentifiers", [])},
            "state": st.get("resourceState"), "status": st.get("resourceStatus"), "status_message": st.get("statusMessage"),
            "message": inst.get("messageFromAdapterInstance") or "",
            "interval_min": inst.get("monitoringInterval"), "objects": inst.get("numberOfResourcesCollected"),
            "metrics": inst.get("numberOfMetricsCollected"), "last_collected_ms": inst.get("lastCollected"),
            "collector": col.get("name"), "collector_state": col.get("state"), "collector_type": col.get("type"),
        })

    federations = []
    for r in [r for r in resources if r["resourceKey"]["resourceKindKey"] == "federation"]:
        props = {p["name"]: p.get("value") for p in ops.get(f"/api/resources/{r['identifier']}/properties").get("property", [])}
        stats = latest_stats(ops, r["identifier"])
        federations.append({"name": r["resourceKey"]["name"], "id": r["identifier"],
                            "properties": {k: props.get(k) for k in HEALTH_PROPS},
                            "stats": {k: stats.get(k) for k in HEALTH_STATS}})

    alerts, page = [], 0
    while True:
        r = ops.s.post(ops.base + "/api/alerts/query", timeout=120, params={"page": page, "pageSize": 1000},
                       json={"resource-query": {"adapterKind": [AK]}, "activeOnly": True})
        r.raise_for_status()
        doc = r.json()
        alerts += doc.get("alerts") or []
        if not doc.get("alerts") or len(alerts) >= (doc.get("pageInfo") or {}).get("totalCount", len(alerts)):
            break
        page += 1
    names = {r["identifier"]: r["resourceKey"]["name"] for r in resources}
    report = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "vcf_operations": {k: version.get(k) for k in ("releaseName", "humanlyReadableReleaseDate", "major", "minor",
                                                       "minorMinor", "buildNumber")},
        "pack": {"id": solution.get("id"), "version": solution.get("version")},
        "accounts": accounts,
        "collectors": [{k: c.get(k) for k in ("id", "name", "type", "state", "hostName")} for c in collectors.values()
                       if c.get("name") in {x["collector"] for x in accounts}],
        "objects_per_type": dict(sorted(kinds.items())),
        "objects_without_data": dict(sorted(no_data.items())),
        "federations": federations,
        "open_alerts": [{"name": al.get("alertDefinitionName"), "criticality": al.get("alertLevel"),
                         "object": names.get(al.get("resourceId"), al.get("resourceId")),
                         "since_ms": al.get("startTimeUTC")} for al in alerts],
    }
    report["findings"] = [{"problem": p, "check": c} for p, c in findings(report, now_ms)]
    with open(a.out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"VCF Operations {report['vcf_operations'].get('releaseName')}, pack {report['pack'].get('version')}, "
          f"{len(accounts)} account(s), {len(resources)} objects, {len(report['open_alerts'])} open alert(s)")
    for x in report["findings"] or [{"problem": "no problem found in the collection", "check": ""}]:
        print(f"- {x['problem']}" + (f"\n    check: {x['check']}" if x["check"] else ""))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
