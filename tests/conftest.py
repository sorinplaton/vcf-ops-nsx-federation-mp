"""A fake NSX for the tests: it answers the adapter's GET calls from a recorded, anonymised collection of a
small federation (two Global Managers, two locations). No network is used."""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pack", "app"))
sys.argv = ["adapter", "collect", os.devnull, os.devnull]

import nsxclient  # noqa: E402

FIXTURE = json.load(open(os.path.join(ROOT, "tests", "fixtures", "federation.json")))
GM_HOSTS = [h for h, calls in FIXTURE.items() if "/api/v1/sites/self" in calls]
GM_HOSTS.sort(key=lambda h: FIXTURE[h]["/api/v1/sites/self"]["site"].get("active_gm") != "ACTIVE")  # active first
REALIZED = "/global-manager/api/v1/global-infra/realized-state/status?intent_path="


class FakeNsx:
    """data: host -> {path?params -> answer}. fail: path fragments that answer HTTP 500."""

    def __init__(self):
        self.data = copy.deepcopy(FIXTURE)
        self.fail = []
        self.calls = 0
        self.clock_ms = {}  # host -> clock offset in milliseconds

    def get(self, client, path, params=None):
        client.check_trust()
        with client._lock:  # the same bookkeeping as NsxClient.get
            client.requests += 1
            if client.calls >= client.call_budget:
                client.budget_hit = True
                raise nsxclient.BudgetExceeded(client.host)
            client.calls += 1
        self.calls += 1
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        if any(f in key for f in self.fail):
            with client._lock:
                client.errors += 1
            raise nsxclient.NsxError(500, path, "injected")
        answers = self.data.get(client.host, {})
        if key not in answers and key.startswith(REALIZED):  # an object added by a test: realized like its kind
            kind = key[len(REALIZED):].split("/")[2]
            key = next(k for k in answers if k.startswith(REALIZED + "/global-infra/" + kind + "/"))
        if key not in answers:
            with client._lock:
                client.errors += 1
            raise nsxclient.NsxError(404, path, "not in the fixture")
        answer = copy.deepcopy(answers[key])
        if key == "/api/v1/node/status":  # the clock of the manager: now, plus the offset a test gives it
            import time
            answer["system_time"] = int(time.time() * 1000 + self.clock_ms.get(client.host, 0))
        return answer


@pytest.fixture
def nsx(monkeypatch, tmp_path):
    fake = FakeNsx()
    monkeypatch.setattr(nsxclient, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(nsxclient, "server_cert_sha256", lambda host, port=443, timeout=10.0: "f" * 64)
    monkeypatch.setattr(nsxclient.NsxClient, "get", lambda self, path, params=None: fake.get(self, path, params))
    return fake


def run(gm_hosts=None, trust="report", **identifiers):
    """One collection cycle. Returns {object kind: {object name: {"m": metrics, "p": properties}}}."""
    import adapter
    from aria.ops.adapter_instance import AdapterInstance
    ids = dict({"gm_hosts": ",".join(gm_hosts or GM_HOSTS), "certificate_trust": trust}, **identifiers)
    ai = AdapterInstance({
        "adapter_key": {"adapter_kind": "NSXFederation", "object_kind": "NSXFederation_adapter_instance", "name": "test",
                        "identifiers": [{"key": k, "value": v, "is_part_of_uniqueness": True} for k, v in ids.items()]},
        "credential_config": {"credential_key": "nsx_credential", "credential_fields": [
            {"key": "username", "value": "audit"}, {"key": "password", "value": "x"}]}})
    doc = adapter.collect(ai).get_json()
    out = {}
    for o in doc.get("result", []):
        out.setdefault(o["key"]["objectKind"], {})[o["key"]["name"]] = {
            "m": {x["key"]: x.get("numberValue") for x in o.get("metrics", [])},
            "p": {x["key"]: x.get("stringValue", x.get("numberValue")) for x in o.get("properties", [])},
            "id": next((i["value"] for i in o["key"].get("identifiers", []) if i["key"] == "id"), None),
            "e": [e.get("message") for e in o.get("events", [])]}
    out["_error"] = doc.get("errorMessage")
    return out
