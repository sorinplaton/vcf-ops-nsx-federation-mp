#!/usr/bin/env python3
"""Generate the policy shipped with the pack: policies/NSX_Federation_Non-production.xml, to import in VCF Operations.

(A policy inside the PAK needs a describe fragment, and the SDK drops the required vendorNameKey of OOTBPolicies when it
merges one, so the build fails. The policy is an import file instead; install/import_policy.py imports it.)

"NSX Federation - Non-production" keeps the alerts for outages, broken sync, failover and realization, and turns off
the alerts a lab or test federation often raises on purpose: backups, no standby Global Manager, firewall rule review,
overrides, NSX configuration limits and edge capacity. Assign it to a custom group that holds the objects of such a
federation (Operations, Configurations, Policies). The other federations keep the default policy, with every alert on.
"""
import os
import sys
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gen_alerts  # noqa: E402  (the alert list: key -> object type)

OUT = os.path.join(ROOT, "policies", "NSX_Federation_Non-production.xml")
KEY = "6e5d3f1a-0c4b-5a9e-8f21-4e5358460112"  # fixed, so a second import updates the same policy
AK = gen_alerts.AK
NAME = "NSX Federation - Non-production"
OFF = ["gm_backup", "loc_backup", "fed_no_standby", "pol_review", "pol_any", "loc_override", "loc_capacity",
       "edge_cpu", "edge_cpu_trend", "edge_memory", "edge_disk"]


def policy_xml() -> str:
    """The policy in the form VCF Operations exports and imports (Operations, Configurations, Policies, Import)."""
    kinds = {a[0]: a[1] for a in gen_alerts.A}
    missing = [k for k in OFF if k not in kinds]
    assert not missing, f"unknown alerts: {missing}"
    by_kind = {}
    for k in OFF:
        by_kind.setdefault(kinds[k], []).append(k)
    alerts = "".join(
        f'                <Alerts adapterKind="{AK}" resourceKind="{kind}">\n'
        + "".join(f'                    <Alert enabled="false" id="{gen_alerts.aid(k)}"/>\n' for k in keys)
        + "                </Alerts>\n"
        for kind, keys in sorted(by_kind.items()))
    return ('<?xml version="1.0" encoding="UTF-8"?><PolicyContent>\n'
            '    <Policies>\n'
            f'        <Policy key="{KEY}" name={quoteattr(NAME)}>\n'
            '            <PackageSettings>\n'
            f'{alerts}'
            '            </PackageSettings>\n'
            '        </Policy>\n'
            '    </Policies>\n'
            '</PolicyContent>\n')


if __name__ == "__main__":
    text = policy_xml()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(text)
    print(f"wrote {OUT}: {NAME}, {len(OFF)} alerts off")
