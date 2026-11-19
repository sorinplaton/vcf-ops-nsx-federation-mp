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
    for kind in ("global_manager", "location", "sync_channel", "edge_cluster", "edge_node", "rtep_tunnel", "bgp_neighbor", "tier0",
                 "tier1", "segment", "global_policy", "realization"):
        assert all(o["p"]["location_outage"] == down["site_name"] for o in r[kind].values()), kind
    assert all(o["p"]["gm_outage"] == "none" for o in r["sync_channel"].values())


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
        if kind == "sync_channel":
            keys[kind].add("gm_outage")
    doc = etree.parse(os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml"))
    for s in doc.iter("SymptomDefinition"):
        for c in s.iter("Condition"):
            if c.get("key"):
                assert c.get("key") in keys[s.get("resourceKind")], (s.get("id"), c.get("key"))


# ---- VCF Operations side (the adapter reads it through the collector's connection)

class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.ok, self.content = data, status, status < 300, b"x"

    def json(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOps:
    """Answers the suite-api calls of the adapter. resources: kind -> [(id, name, identifier, properties, age days)]."""

    def __init__(self, nsx_clusters=(), resources=None):
        self.nsx_clusters, self.res, self.deleted = list(nsx_clusters), resources or {}, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        if url.startswith("/api/adapters?adapterKindKey=NSXTAdapter"):
            return FakeResponse({"adapterInstancesInfoDto": [{"resourceKey": {"resourceIdentifiers": [
                {"identifierType": {"name": "NSX_CLUSTER_ID"}, "value": c}]}} for c in self.nsx_clusters]})
        if url.startswith("/api/resources?"):
            kind = url.split("resourceKind=")[1].split("&")[0]
            return FakeResponse({"resourceList": [
                {"identifier": i, "creationTime": 0, "resourceKey": {"name": n, "resourceIdentifiers": [
                    {"identifierType": {"name": "id"}, "value": ident}]}} for i, n, ident, _p, _a in self.res.get(kind, [])]})
        rid = url.split("/")[3]
        row = next(x for rows in self.res.values() for x in rows if x[0] == rid)
        if url.endswith("/properties"):
            return FakeResponse({"property": [{"name": k, "value": v} for k, v in row[3].items()]})
        if url.endswith("/stats/latest"):
            ts = int((time.time() - row[4] * 86400) * 1000)
            return FakeResponse({"values": [{"stat-list": {"stat": [{"statKey": {"key": "connection_up"}, "timestamps": [ts]}]}}]})
        return FakeResponse({}, 404)

    def delete(self, url):
        self.deleted.append(url.split("/")[3])
        return FakeResponse({})


@pytest.fixture
def ops(monkeypatch):
    import adapter
    fake = FakeOps()
    real = adapter.OpsApi.__init__

    def init(self, ai):
        real(self, ai)
        self.client, self.error = fake, ""
    monkeypatch.setattr(adapter.OpsApi, "__init__", init)
    return fake


def lm_cluster_ids(nsx):
    return {h: calls["/api/v1/cluster/status"]["cluster_id"] for h, calls in nsx.data.items() if h not in GM_HOSTS}


def test_alarms_of_a_local_manager_the_builtin_integration_monitors_are_left_to_it(nsx, ops):
    ids = lm_cluster_ids(nsx)
    covered_host, covered_cluster = next(iter(ids.items()))
    ops.nsx_clusters = [covered_cluster]
    for host in ids:  # one open federation alarm on every Local Manager
        nsx.data[host]["/api/v1/alarms?page_size=500&status=OPEN"]["results"].append(
            {"id": "a1", "feature_name": "federation", "event_type": "rtep_bgp_down", "severity": "HIGH", "status": "OPEN",
             "summary": "test"})
    import adapter
    from conftest import GM_HOSTS as hosts
    doc = adapter.collect(_instance(hosts)).get_json()
    by = {o["key"]["name"]: o for o in doc["result"] if o["key"]["objectKind"] == "location"}
    states = {n: ({p["key"]: p.get("stringValue") for p in o["properties"]}["alarm_alerts_by"], len(o.get("events", [])))
              for n, o in by.items()}
    assert sorted(states.values()) == [("the built-in NSX integration", 0), ("this pack", 1)]
    r = run()
    assert fed(r)["p"]["vcf_operations_api"] == "ok"


def _instance(hosts):
    from aria.ops.adapter_instance import AdapterInstance
    return AdapterInstance({
        "adapter_key": {"adapter_kind": "NSXFederation", "object_kind": "NSXFederation_adapter_instance", "name": "t",
                        "identifiers": [{"key": "gm_hosts", "value": ",".join(hosts), "is_part_of_uniqueness": True},
                                        {"key": "certificate_trust", "value": "report", "is_part_of_uniqueness": False}]},
        "credential_config": {"credential_key": "nsx_credential", "credential_fields": [
            {"key": "username", "value": "audit"}, {"key": "password", "value": "x"}]}})


def test_without_vcf_operations_the_pack_raises_the_alarms_itself(nsx):
    r = run()
    assert all(l["p"]["alarm_alerts_by"] == "this pack" for l in r["location"].values())
    assert fed(r)["p"]["vcf_operations_api"].startswith("not available")


def test_versions_upgrade_and_sync_details(nsx):
    r = run()
    assert all(g["p"]["version_matches_active_gm"] == "True" for g in r["global_manager"].values())
    assert all(l["p"]["release_newer_than_gm"] == "False" and "upgrade_status" in l["p"] for l in r["location"].values())
    assert all(l["m"]["sync|replication_flows_unhealthy"] == 0 and l["m"]["sync|full_sync_duration_s"] > 0
               for l in r["location"].values())
    assert fed(r)["m"]["global_managers|split_brain_reported"] == 0
    # a standby on another version, a split-brain flag, a location ahead of the Global Manager
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["site"]["site_version"] = "9.1.0.0.1"
    sites = nsx.data[GM_HOSTS[0]]["/api/v1/sites"]
    sites["sites"][0]["split_brain"] = True
    lm = next(s for s in sites["sites"] if s.get("node_type") == "LM")
    lm["site_version"] = "9.2.0.0.1"
    r = run()
    assert sorted(g["p"]["version_matches_active_gm"] for g in r["global_manager"].values()) == ["False", "True"]
    assert fed(r)["m"]["global_managers|split_brain_reported"] == 1
    assert sorted(l["p"]["release_newer_than_gm"] for l in r["location"].values()) == ["False", "True"]


def test_standby_out_of_sync_duration(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]["active_standby_sync_statuses"][0]
    status["status"], status["full_sync_status"]["status"] = "ERROR", "FAILED"
    run()
    r = run()
    active = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "ACTIVE")
    assert active["m"]["sync|standby_in_sync"] == 0 and active["m"]["sync|standby_out_of_sync_minutes"] >= 0


def test_global_manager_outage_marks_the_sync_channels(nsx):
    run()
    nsx.fail = [GM_HOSTS[1] and "/nothing"]  # keep the fixture intact; drop the standby below
    del nsx.data[GM_HOSTS[1]]
    r = run()
    assert all(c["p"]["gm_outage"].startswith("Global Manager not reachable") for c in r["sync_channel"].values())


def test_realization_is_tiered_above_200_objects(nsx):
    segs = nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/global-infra/segments?page_size=500"]
    template = segs["results"][0]
    for i in range(600):
        segs["results"].append(dict(template, id=f"scale-{i}", display_name=f"scale-{i}",
                                    path=f"/global-infra/segments/scale-{i}", unique_id=f"scale-{i}"))
    r = run()
    assert fed(r)["m"]["collection|realization_checked"] == 610  # first cycle: everything
    r = run()
    checked = fed(r)["m"]["collection|realization_checked"]
    assert 90 <= checked <= 115 and len(r["segment"]) == 605  # then a sixth per cycle, state kept for the rest
    assert all(s["m"]["realization|realized_locations"] >= 1 for s in r["segment"].values())
    segs["results"][-1]["_last_modified_time"] = 1  # an object changed in NSX is read again at once
    assert fed(run())["m"]["collection|realization_checked"] >= 1


def test_location_list_from_vcf_operations_when_the_cache_is_empty(nsx, ops):
    r = run()
    f_name, f = next(iter(r["federation"].items()))
    fed_id = f["p"]["federation_id"]
    ops.res = {"federation": [("f1", f_name, fed_id, {"global_manager_addresses": f["p"]["global_manager_addresses"]}, 0)],
               "location": [(f"l{i}", n, f"{fed_id}:site:{l['p']['site_id']}", l["p"], 0)
                            for i, (n, l) in enumerate(r["location"].items())]}
    for f_ in os.listdir(nsxclient.CACHE_DIR):  # the container restarted
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    nsx.fail = ["/api/v1/sites/self"]  # and no Global Manager answers
    r = run()
    assert fed(r)["p"]["collection_mode"] == "degraded: location list from VCF Operations"
    assert len(r["location"]) == 2 and len(r["rtep_tunnel"]) == 8 and r["_error"] is None


def test_stale_objects_are_deleted_only_when_asked(nsx, ops):
    r = run()
    fed_id = fed(r)["p"]["federation_id"]
    live_name = next(iter(r["sync_channel"]))
    ops.res = {"sync_channel": [("old", "gone -> x", f"{fed_id}:chan:gone>x", {}, 10),
                                ("recent", "gone -> y", f"{fed_id}:chan:gone>y", {}, 1),
                                ("other", "foreign", "ffff:chan:a>b", {}, 10)]}
    run()
    assert ops.deleted == []  # default: never
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    r = run(delete_stale_after_days="7")
    assert ops.deleted == ["old"] and fed(r)["m"]["collection|stale_objects_deleted"] == 1 and live_name in r["sync_channel"]
