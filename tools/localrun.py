"""Run the adapter's collect() against live NSX managers without VCF Operations. Prints a summary.

Run it inside the tooling image, from the repository root:
  docker run --rm --network host -v $PWD:/work -e GM_HOSTS=gm1.example.com,gm2.example.com \
      -e NSX_USER=admin -e NSX_PASSWORD=... fedmp-tooling python tools/localrun.py
Optional: LM_USER and LM_PASSWORD (Local Managers, if different), TRUST=enforce|report (default report),
RULE_POLICIES, LINK=on,
SHOW=<object kinds, comma separated>.
"""
import collections, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pack', 'app'))
sys.argv = ['x', 'collect', '/dev/null', '/dev/null']
import adapter
from aria.ops.adapter_instance import AdapterInstance
ident = lambda k, v: {"key": k, "value": v, "is_part_of_uniqueness": True}
params = {"gm_hosts": os.environ["GM_HOSTS"], "rule_policies": os.environ.get("RULE_POLICIES", ""),
          "certificate_trust": os.environ.get("TRUST", "report"), "link_nsx_objects": os.environ.get("LINK", "off")}
ai = AdapterInstance({"adapter_key": {"adapter_kind": "NSXFederation", "object_kind": "NSXFederation_adapter_instance",
      "name": "local", "identifiers": [ident(k, v) for k, v in params.items()]},
      "credential_config": {"credential_key": "nsx_credential", "credential_fields": [
          {"key": "username", "value": os.environ.get("NSX_USER", "admin")},
          {"key": "password", "value": os.environ["NSX_PASSWORD"]},
          {"key": "lm_username", "value": os.environ.get("LM_USER", "")},
          {"key": "lm_password", "value": os.environ.get("LM_PASSWORD", "")}]}})
d = adapter.get_adapter_definition()
print("definition ok:", len(d.to_json()["object_types"]), "object types")
tr = adapter.test(ai); print("test:", tr.get_json())
j = adapter.collect(ai).get_json()
print("error:", j.get("errorMessage") or j.get("error_message"))
objs = j.get("result", [])
print("objects:", len(objs), dict(collections.Counter(o["key"]["objectKind"] for o in objs)))
only = os.environ.get("SHOW", "")
for o in objs:
    kind = o["key"]["objectKind"]
    if only and kind not in only.split(","): continue
    m = {x["key"]: x.get("numberValue") for x in o.get("metrics", [])}
    p = {x["key"]: x.get("stringValue", x.get("numberValue")) for x in o.get("properties", [])}
    print(f"\n[{kind}] {o['key']['name']}\n  metrics: {m}\n  props: {p}")
    for e in o.get("events", []): print("  EVENT", e.get("criticality"), e.get("message"))
print("\nrelationships:", len(j.get("relationships", [])))
