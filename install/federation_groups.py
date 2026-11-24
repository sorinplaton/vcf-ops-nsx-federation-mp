#!/usr/bin/env python3
"""Create one custom group per NSX federation in VCF Operations, and optionally assign a policy to it.

The pack ships groups by area (all objects, managers and locations, ...) that hold the objects of every federation.
This script adds a group per federation, "NSX Federation - <federation>", that holds only the objects of that
federation: every object of the pack carries the property federation_id, and the group selects on it. Use it to
assign a policy, or to scope a notification rule or a dashboard, to one federation.

It reads the federations from VCF Operations, so the pack (1.13.0 or later) must have collected at least once.
Running it again updates the groups; it never deletes one. Needs OPS_HOST and OPS_PW (an administrator) in the
environment; OPS_USER (default admin), OPS_AUTH_SOURCE (default local) and OPS_CA are optional, like cleanup_content.py.

usage: federation_groups.py [--federation NAME] [--policy NAME] [--insecure]
"""
import argparse
import os
import sys

import requests

from cleanup_content import Ops

AK = "NSXFederation"
KINDS = ["federation", "global_manager", "manager_node", "location", "sync_channel", "edge_cluster", "edge_node",
         "rtep_tunnel", "bgp_neighbor", "tier0_sr", "tier0", "tier1", "segment", "global_policy", "realization", "rule"]
PREFIX = "NSX Federation - "


def group_name(federation: str) -> str:
    """'NSX Federation - lab', also for a federation named 'NSX Federation lab' (the default name of the pack)."""
    short = federation[len("NSX Federation"):].strip(" -") if federation.startswith("NSX Federation") else federation
    return PREFIX + (short or federation)


def group_definition(federation: str, federation_id: str) -> dict:
    """The custom group of one federation: every object type of the pack whose federation_id is this federation's."""
    return {
        "resourceKey": {"name": group_name(federation), "adapterKindKey": "Container", "resourceKindKey": "Environment"},
        "description": f"Every object of the NSX federation {federation} (federation ID {federation_id}).",
        "autoResolveMembership": True,
        "membershipDefinition": {"rules": [{
            "resourceKindKey": {"resourceKind": kind, "adapterKind": AK},
            "propertyConditionRules": [{"key": "federation_id", "stringValue": federation_id, "compareOperator": "EQ"}],
        } for kind in KINDS]},
    }


def federations(ops):
    """[(name, VCF Operations ID, federation ID or None)] of every federation object of the pack."""
    out = []
    for r in all_pages(ops, "/api/resources", {"adapterKind": AK, "resourceKind": "federation"}, "resourceList"):
        props = ops.get(f"/api/resources/{r['identifier']}/properties").get("property", [])
        fid = next((p.get("value") for p in props if p.get("name") == "federation_id"), None)
        out.append((r["resourceKey"]["name"], r["identifier"], fid))
    return out


def all_pages(ops, path, params, key):
    """Every item of a paged Suite API list."""
    out, page = [], 0
    while True:
        doc = ops.get(path, dict(params, page=page, pageSize=1000))
        items = doc.get(key) or []
        out += items
        total = (doc.get("pageInfo") or {}).get("totalCount", len(out))
        if not items or len(out) >= total:
            return out
        page += 1


def find_group(ops, name):
    """ID of the custom group (Container / Environment) with this name, None if there is none; exits on duplicates."""
    hits = [g["id"] for g in all_pages(ops, "/api/resources/groups", {}, "groups")
            if g["resourceKey"]["name"] == name and g["resourceKey"].get("adapterKindKey") == "Container"
            and g["resourceKey"].get("resourceKindKey") == "Environment"]
    if len(hits) > 1:
        sys.exit(f"{len(hits)} custom groups are named {name!r}; rename or delete the extra ones first")
    return hits[0] if hits else None


def send(ops, method, path, body):
    r = ops.s.request(method, ops.base + path, json=body, timeout=120)
    if r.status_code not in (200, 201, 204):
        sys.exit(f"{method} {path} failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json() if r.text else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--federation", help="only this federation (its name in VCF Operations)")
    ap.add_argument("--policy", help="assign this policy (its name in VCF Operations) to the group(s)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the VCF Operations certificate (lab only)")
    a = ap.parse_args()
    host, password = os.environ.get("OPS_HOST"), os.environ.get("OPS_PW")
    if not host or not password:
        sys.exit("set OPS_HOST and OPS_PW")
    verify = False if a.insecure else (os.environ.get("OPS_CA") or True)
    if a.insecure:
        requests.packages.urllib3.disable_warnings()
    ops = Ops(host, os.environ.get("OPS_USER", "admin"), password, os.environ.get("OPS_AUTH_SOURCE", "local"), verify)

    feds = [f for f in federations(ops) if not a.federation or f[0] == a.federation]
    if not feds:
        sys.exit(f"no federation object{' named ' + a.federation if a.federation else ''} in VCF Operations")
    policy_id = None
    if a.policy:
        policies = ops.get("/api/policies").get("policySummaries", [])
        policy_id = next((p["id"] for p in policies if p.get("name") == a.policy), None)
        if not policy_id:
            sys.exit(f"no policy named {a.policy!r}")
    names = [group_name(n) for n, _, fid in feds if fid]
    clash = sorted({n for n in names if names.count(n) > 1})
    if clash:
        sys.exit(f"several federations map to the group name(s) {', '.join(clash)}: give them distinct names "
                 "(account setting 'Federation name'), or run with --federation")
    for name, _, fid in feds:
        if not fid:
            print(f"skipped {name}: no federation_id property yet (pack before 1.13.0, or no collection since the upgrade)")
            continue
        body = group_definition(name, fid)
        gname = body["resourceKey"]["name"]
        gid = find_group(ops, gname)
        if gid:
            body["id"] = gid
            # the PUT replaces the whole definition: keep the policy assigned to the group
            policy = ops.get(f"/api/resources/groups/{gid}", {"includePolicy": "true"}).get("policy")
            if policy:
                body["policy"] = policy
            send(ops, "PUT", "/api/resources/groups", body)
            what = "updated"
        else:
            gid, what = send(ops, "POST", "/api/resources/groups", body)["id"], "created"
        print(f"{what}: {gname} ({gid})")
        if policy_id:
            res = send(ops, "PUT", f"/api/policies/{policy_id}/assign", {"groupIds": [gid]})
            if gid in (res.get("failedGroupIds") or []):
                sys.exit(f"  policy {a.policy} NOT assigned to {gname}")
            print(f"  policy {a.policy} assigned")


if __name__ == "__main__":
    main()
