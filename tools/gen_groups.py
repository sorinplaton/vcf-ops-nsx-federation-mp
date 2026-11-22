#!/usr/bin/env python3
"""Custom groups of the NSX Federation pack: one per area, membership by object type (dynamic).

Writes pack/content/customgroups/customgroups.json in the format of a VCF Operations content export.
With --zip <file> --marker <file> [--prefix <text>], a content-import zip for a test (names get the prefix).
"""
import os
import json, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AK = "NSXFederation"
GROUPS = [
    ("NSX Federation - All objects", "Every object of the NSX Federation pack. Scope for policies and notifications.",
     ["federation", "global_manager", "manager_node", "location", "sync_channel", "edge_cluster", "edge_node",
      "rtep_tunnel", "bgp_neighbor", "tier0_sr", "tier0", "tier1", "segment", "global_policy", "realization", "rule"]),
    ("NSX Federation - Managers and locations", "The federation, its Global Managers and nodes, and its locations.",
     ["federation", "global_manager", "manager_node", "location"]),
    ("NSX Federation - Inter-site connectivity", "Sync channels, edge clusters, edge nodes, RTEP tunnels, Tier-0 service routers and Tier-0 BGP neighbors.",
     ["sync_channel", "edge_cluster", "edge_node", "rtep_tunnel", "tier0_sr", "bgp_neighbor"]),
    ("NSX Federation - Stretched objects", "Stretched gateways, segments, global security policies, rules and their realization per location.",
     ["tier0", "tier1", "segment", "global_policy", "realization", "rule"]),
]


def doc(prefix=""):
    return {"customGroups": [{
        "resourceKind": "Environment", "adapterKind": "Container", "autoResolveMembership": True, "started": True,
        "name": prefix + name, "description": desc,
        "membershipDefinition": {"ruleGroups": [{"resourceKind": k, "adapterKind": AK, "rules": []} for k in kinds]},
    } for name, desc, kinds in GROUPS]}


if __name__ == "__main__":
    path = os.path.join(ROOT, "pack/content/customgroups/customgroups.json")
    json.dump(doc(), open(path, "w"), indent=2)
    print("wrote", path, len(GROUPS), "groups")
    if "--zip" in sys.argv:
        prefix = sys.argv[sys.argv.index("--prefix") + 1] if "--prefix" in sys.argv else ""
        with zipfile.ZipFile(sys.argv[sys.argv.index("--zip") + 1], "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("customgroups.json", json.dumps(doc(prefix), indent=2))
            z.writestr("configuration.json", json.dumps({"customGroups": len(GROUPS), "type": "CUSTOM"}))
            z.write(sys.argv[sys.argv.index("--marker") + 1], "6844548499441080431L.v1")
