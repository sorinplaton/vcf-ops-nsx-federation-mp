#!/usr/bin/env python3
"""Send the alerts of the NSX Federation pack to an outbound instance of VCF Operations (email, webhook, ServiceNow,
Slack, SNMP trap or log file): creates, or updates, one notification rule.

The outbound instance must exist (Administration, Configurations, Outbound Settings); this script does not hold
mail server or ticketing credentials. The rule takes every alert raised on an object of the pack at the chosen
criticality or above, new, updated and cancelled, so a ticket or a chat thread also learns that the alert cleared.
With --group it takes only the alerts on the members of that custom group, for example the group of one federation
made by federation_groups.py. It uses the default template of the plugin type unless --template names another.

Needs OPS_HOST and OPS_PW (an administrator) in the environment; OPS_USER, OPS_AUTH_SOURCE and OPS_CA are optional,
like cleanup_content.py. Running it again with the same rule name updates the rule.

usage: notification_rules.py --outbound NAME [--min-criticality warning|immediate|critical] [--group NAME]
                             [--template NAME] [--name RULE] [--insecure]
"""
import argparse
import os
import sys

import requests

from cleanup_content import Ops
from federation_groups import AK, KINDS, all_pages, find_group, send

LEVELS = ["WARNING", "IMMEDIATE", "CRITICAL"]


def rule_definition(name: str, plugin_id: str, template_id: str, minimum: str = "immediate",
                    group_id: str = None) -> dict:
    """A notification rule for the alerts on the objects of the pack, at `minimum` criticality or above."""
    levels = LEVELS[LEVELS.index(minimum.upper()):]
    rule = {
        "name": name,
        "pluginId": plugin_id,
        "templateId": template_id,
        "ruleType": "ALERT",
        "enabled": True,
        "alertStatuses": ["NEW", "UPDATED", "CANCELED"],
        "criticalities": levels,
        "resourceKindFilters": [{"adapterKind": AK, "resourceKind": k} for k in KINDS],
    }
    if group_id:
        # the members of a custom group are its children
        rule["resourceFilters"] = [{"resourceId": group_id, "relationship": "CHILD"}]
    return rule


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--outbound", required=True, help="name of the outbound instance (Outbound Settings)")
    ap.add_argument("--min-criticality", default="immediate", choices=["warning", "immediate", "critical"],
                    help="lowest criticality sent (default immediate)")
    ap.add_argument("--group", help="only the alerts on the members of this custom group")
    ap.add_argument("--template", help="payload template (default: the default template of the plugin type)")
    ap.add_argument("--name", help="rule name (default 'NSX Federation - <outbound>')")
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

    plugins = ops.get("/api/alertplugins").get("notificationPluginInstances", [])
    plugin = next((p for p in plugins if p.get("name") == a.outbound), None)
    if not plugin:
        sys.exit(f"no outbound instance named {a.outbound!r}; there are: {', '.join(p['name'] for p in plugins) or 'none'}")
    templates = [t for t in ops.get("/api/notifications/templates", {"pageSize": 1000}).get("notificationTemplates", [])
                 if t.get("pluginTypeId") == plugin["pluginTypeId"] and t.get("templateType") in (None, "ALERT")]
    if a.template:
        template = next((t for t in templates if t.get("name") == a.template), None)
    else:  # the built-in one: not editable, named "Default ..." (the first such, in name order)
        builtin = sorted((t for t in templates if not t.get("editable")), key=lambda t: (not t["name"].startswith("Default"), t["name"]))
        template = builtin[0] if builtin else None
    if not template:
        sys.exit(f"no {'template named ' + repr(a.template) if a.template else 'default template'} for "
                 f"{plugin['pluginTypeId']}; there are: {', '.join(t['name'] for t in templates) or 'none'}")
    group_id = None
    if a.group:
        group_id = find_group(ops, a.group)
        if not group_id:
            sys.exit(f"no custom group named {a.group!r}")
    name = a.name or f"NSX Federation - {a.outbound}"
    body = rule_definition(name, plugin["pluginId"], template["templateId"], a.min_criticality, group_id)
    old = [r for r in all_pages(ops, "/api/notifications/rules", {}, "rules") if r.get("name") == name]
    if len(old) > 1:
        sys.exit(f"{len(old)} notification rules are named {name!r}; delete the extra ones first")
    old = old[0] if old else None
    if old:
        body["id"] = old["id"]
        send(ops, "PUT", "/api/notifications/rules", body)
        print(f"updated: {name} ({old['id']})")
    else:
        print(f"created: {name} ({send(ops, 'POST', '/api/notifications/rules', body).get('id')})")
    print(f"  {plugin['pluginTypeId']} {a.outbound}, template {template['name']}, "
          f"{', '.join(body['criticalities']).lower()}{', group ' + a.group if a.group else ''}")


if __name__ == "__main__":
    main()
