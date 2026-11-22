#!/usr/bin/env python3
"""Import the policy shipped with the pack into VCF Operations (the same as Administration, Configurations, Policy Definition, Import).

Reads OPS_HOST and OPS_PW (and OPS_USER, OPS_AUTH_SOURCE, OPS_CA) from the environment, like cleanup_content.py.
The policy keeps a fixed ID, so importing it again updates it. Importing does not assign it: assign it to the custom
group that holds the objects of a lab or test federation.

usage: import_policy.py [policies/NSX_Federation_Non-production.xml] [--insecure]
"""
import argparse
import io
import os
import sys
import zipfile

import requests

from cleanup_content import Ops

DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "policies",
                       "NSX_Federation_Non-production.xml")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("policy", nargs="?", default=DEFAULT, help="the policy XML (default: the one of the repository)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the VCF Operations certificate (lab only)")
    a = ap.parse_args()
    host, password = os.environ.get("OPS_HOST"), os.environ.get("OPS_PW")
    if not host or not password:
        sys.exit("set OPS_HOST and OPS_PW")
    verify = False if a.insecure else (os.environ.get("OPS_CA") or True)
    if a.insecure:
        requests.packages.urllib3.disable_warnings()
    ops = Ops(host, os.environ.get("OPS_USER", "admin"), password, os.environ.get("OPS_AUTH_SOURCE", "local"), verify)
    with open(a.policy, "rb") as f:
        xml = f.read()
    z = io.BytesIO()  # VCF Operations imports the zip its export produces
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("exportedPolicies.xml", xml)
    # None drops the JSON content type of the session, so requests sets the multipart one
    r = ops.s.post(ops.base + "/api/policies/import", params={"forceImport": "true"}, headers={"Content-Type": None},
                   files={"policy": ("policy.zip", z.getvalue(), "application/zip")}, timeout=120)
    if r.status_code != 200:
        sys.exit(f"import failed: HTTP {r.status_code} {r.text[:300]}")
    doc = r.json()
    for what in ("createdPolicies", "updatedPolicies", "skippedPolicies"):
        for p in doc.get(what) or []:
            print(f"{what[:-8]}: {p.get('name')} ({p.get('id')})")


if __name__ == "__main__":
    main()
