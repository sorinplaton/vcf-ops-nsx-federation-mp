import os
import time

import pytest

import nsxclient
from conftest import GM_HOSTS, ROOT, run

SITES_STATUS = "/api/v1/sites/status"


def fed(r):
    return next(iter(r["federation"].values()))


def test_normal_cycle(nsx):
    r = run()
    assert {k: len(v) for k, v in r.items() if not k.startswith("_")} == {
        "federation": 1, "global_manager": 2, "manager_node": 4, "location": 2, "sync_channel": 9, "edge_cluster": 2,
        "edge_node": 4, "rtep_tunnel": 8, "bgp_neighbor": 16, "tier0": 1, "tier1": 3, "segment": 5, "global_policy": 1,
        "realization": 14}
    f = fed(r)
    assert f["m"]["global_managers|global_managers_active"] == 1
    assert f["m"]["collection|data_gaps"] == 0 and f["p"]["failed_calls"] == "none"
    assert f["m"]["locations|locations_healthy_pct"] == 100 and f["p"]["standby_configured"] == "True"
    for gm in r["global_manager"].values():  # the active and the standby both carry the sync state
        assert gm["m"]["sync|standby_in_sync"] == 1
    for loc in r["location"].values():  # same release, another patch level: no mismatch
        assert loc["p"]["version_matches_gm"] == "True" and loc["p"]["location_outage"] == "none"


def test_failed_calls_are_unknown_not_empty(nsx):
    nsx.fail = [SITES_STATUS, "/backups/overview", "/alarms", "/cluster/status"]
    r = run()
    f = fed(r)
    assert f["m"]["collection|data_gaps"] > 0 and "HTTP 500" in f["p"]["failed_calls"]
    for loc in r["location"].values():
        assert "connectivity|gm_connection_up" not in loc["m"]  # not "0 = disconnected"
        assert "backup_enabled" not in loc["p"] and "alarms|alarms_open_critical" not in loc["m"]
        assert "cluster|nodes_offline" not in loc["m"]
    for gm in r["global_manager"].values():
        assert "backup_enabled" not in gm["p"] and "sync|standby_sync_entries" not in gm["m"]
    assert "locations|locations_gm_connected" not in f["m"]
    assert f["m"]["locations|locations_healthy_pct"] == 100  # unknown does not make a location unhealthy


def test_a_failed_slow_call_is_not_cached(nsx):
    nsx.fail = ["/backups/overview"]
    run()
    nsx.fail = []
    r = run()  # second cycle, same cache directory
    assert all("backup_enabled" in gm["p"] for gm in r["global_manager"].values())


def test_location_outage_marks_dependent_objects(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    down = next(rc for rc in status["remote_connections"] if any(rc["site_name"] == n for n in run()["location"]))
    for stub in down["stubs"]:
        stub["connection_up"] = False
    r = run()
    f = fed(r)
    assert f["m"]["locations|locations_down"] == 1 and f["p"]["location_outage"] == down["site_name"]
    assert r["location"][down["site_name"]]["m"]["connectivity|gm_connection_up"] == 0
    for kind in ("location", "sync_channel", "edge_cluster", "edge_node", "rtep_tunnel", "bgp_neighbor", "tier0",
                 "tier1", "segment", "global_policy", "realization"):
        assert all(o["p"]["location_outage"] == down["site_name"] for o in r[kind].values()), kind
    assert all("location_outage" not in o["p"] for o in r["global_manager"].values())


def test_global_manager_of_another_federation_is_left_out(nsx):
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["id"] = "00000000-0000-0000-0000-00000000beef"
    r = run()
    f = fed(r)
    assert f["m"]["global_managers|global_managers_foreign"] == 1 and len(r["global_manager"]) == 1
    assert "belongs to federation" in f["p"]["global_manager_errors"]
    assert f["m"]["global_managers|global_managers_unreachable"] == 0


def test_standby_is_read_from_the_federation_not_from_the_account(nsx):
    r = run(gm_hosts=[GM_HOSTS[0]])  # one address, but the federation has a standby
    assert fed(r)["p"]["standby_configured"] == "True"
    gms = nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/global-infra/global-managers?page_size=500"]
    gms["results"] = [g for g in gms["results"] if g.get("mode") == "ACTIVE"]
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["site"]["active_gm"] = "NONE"
    r = run()  # two addresses, but no standby in the federation
    assert fed(r)["p"]["standby_configured"] == "False"


def test_no_global_manager_uses_the_cached_location_list(nsx):
    run()
    nsx.fail = ["/api/v1/sites/self"]
    r = run()
    f = fed(r)
    assert f["p"]["collection_mode"].startswith("degraded") and len(r["location"]) == 2
    assert f["m"]["global_managers|global_managers_unreachable"] == 2 and len(r["rtep_tunnel"]) == 8


def test_federation_name(nsx):
    assert "Paris-Lyon" in run(federation_name="Paris-Lyon")["federation"]


def test_scale_one_call_per_stretched_object(nsx):
    """500 more segments cost 500 more calls on the active Global Manager and nothing on the Local Managers."""
    run()
    base = nsx.calls
    segs = nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/global-infra/segments?page_size=500"]
    template = segs["results"][0]
    for i in range(500):
        segs["results"].append(dict(template, id=f"scale-{i}", display_name=f"scale-{i}",
                                    path=f"/global-infra/segments/scale-{i}", unique_id=f"scale-{i}"))
    for f in os.listdir(nsxclient.CACHE_DIR):  # the inventory is cached for an hour: start from an empty cache
        os.remove(os.path.join(nsxclient.CACHE_DIR, f))
    nsx.calls = 0
    started = time.time()
    r = run()
    assert len(r["segment"]) == 505 and nsx.calls == base + 500
    assert fed(r)["m"]["collection|managers_over_budget"] == 0
    assert time.time() - started < 30


def test_call_budget_gives_a_partial_cycle_not_a_failure(nsx):
    r = run(call_budget="10")
    assert fed(r)["m"]["collection|managers_over_budget"] >= 1 and r["_error"] is None


def test_enforce_needs_a_trusted_certificate(monkeypatch):
    monkeypatch.setattr(nsxclient, "server_cert_sha256", lambda host, port=443, timeout=10.0: "a" * 64)
    with pytest.raises(nsxclient.CertificateNotTrusted, match="no trusted certificate"):
        nsxclient.NsxClient("gm.example.test", "u", "p", enforce_trust=True).check_trust()
    with pytest.raises(nsxclient.CertificateNotTrusted, match="not a trusted certificate"):
        nsxclient.NsxClient("gm.example.test", "u", "p", trusted_fingerprints=["b" * 64], enforce_trust=True).check_trust()
    ok = nsxclient.NsxClient("gm.example.test", "u", "p", trusted_fingerprints=["AA:" * 31 + "AA"], enforce_trust=True)
    ok.check_trust()
    assert isinstance(ok._session.get_adapter("https://gm.example.test/"), nsxclient._PinnedAdapter)
    nsxclient.NsxClient("gm.example.test", "u", "p", enforce_trust=False).check_trust()  # report mode: collected


def test_same_release():
    import adapter
    assert adapter.same_release("9.1.1.0.25691509", "9.1.1.0100.25762940")
    assert not adapter.same_release("9.1.1.0", "9.1.2.0") and not adapter.same_release("9.0.2.0", "9.1.1.0")


def test_alert_content_is_valid():
    etree = pytest.importorskip("lxml.etree")
    schema = etree.XMLSchema(etree.parse(os.path.join(ROOT, "tools", "schemas", "alertDefinitionSchema.xsd")))
    doc = etree.parse(os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml"))
    assert schema.validate(doc), schema.error_log
    symptoms = {s.get("id") for s in doc.iter("SymptomDefinition")}
    recs = {x.get("key") for x in doc.iter("Recommendation") if x.get("key")}
    assert len(symptoms) == len(list(doc.iter("SymptomDefinition")))
    for alert in doc.iter("AlertDefinition"):
        refs = [s.get("ref") for s in alert.iter("SymptomSet") if s.get("ref")] + [s.get("ref") for s in alert.iter("Symptom")]
        assert refs and set(refs) <= symptoms, alert.get("id")
        assert all(x.get("ref") in recs for x in alert.iter("Recommendation") if x.get("ref")), alert.get("id")
        assert all(x.get("ref").startswith("Recommendation-df-") for x in alert.iter("Recommendation") if x.get("ref"))


def test_content_matches_the_object_model():
    """Every metric and property an alert watches is defined by the adapter."""
    etree = pytest.importorskip("lxml.etree")
    import adapter
    keys = {}
    for kind, (_label, metrics, props) in adapter.MODEL.items():
        keys[kind] = {adapter.fk(kind, m) for m, _ in metrics} | {p for p, _ in props}
        if kind in adapter.OUTAGE_KINDS:
            keys[kind].add("location_outage")
    doc = etree.parse(os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml"))
    for s in doc.iter("SymptomDefinition"):
        for c in s.iter("Condition"):
            if c.get("key"):
                assert c.get("key") in keys[s.get("resourceKind")], (s.get("id"), c.get("key"))
