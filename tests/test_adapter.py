import json
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


def test_location_outage_marks_only_the_objects_that_depend_on_it(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    names = list(run()["location"])
    down = next(rc for rc in status["remote_connections"] if rc["site_name"] in names)
    for stub in down["stubs"]:
        stub["connection_up"] = False
    a = down["site_name"]
    b = next(n for n in names if n != a)
    r = run()
    f = fed(r)
    assert f["m"]["locations|locations_down"] == 1 and f["p"]["location_outage"] == a
    assert r["location"][a]["m"]["connectivity|gm_connection_up"] == 0
    assert r["location"][a]["p"]["location_outage"] == a and r["location"][b]["p"]["location_outage"] == "none"
    assert all(g["p"]["location_outage"] == a for g in r["global_manager"].values())  # the hub knows
    for kind in ("edge_cluster", "edge_node", "bgp_neighbor"):  # objects of the other location are not silenced
        mine = [o for n, o in r[kind].items() if n.endswith(f"({a})")]
        other = [o for n, o in r[kind].items() if n.endswith(f"({b})")]
        assert mine and other, kind
        assert all(o["p"]["location_outage"] == a for o in mine), kind
        assert all(o["p"]["location_outage"] == "none" for o in other), kind
    assert all(o["p"]["location_outage"] == a for o in r["rtep_tunnel"].values())  # every tunnel has an end at a
    for n, o in r["sync_channel"].items():
        assert o["p"]["location_outage"] == (a if a in n else "none"), n
    for n, o in r["realization"].items():
        assert o["p"]["location_outage"] == (a if n.endswith("@ " + a) else "none"), n
    spans = {n: o["m"]["realization|span_locations"] for n, o in r["segment"].items()}
    assert any(o["p"]["location_outage"] == "none" for o in r["segment"].values()) or min(spans.values()) == 2
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
    assert active["m"]["sync|standby_in_sync"] == 0
    since = json.load(open(os.path.join(nsxclient.CACHE_DIR, os.listdir(nsxclient.CACHE_DIR)[0])))["standby_out_of_sync_since"]["v"]
    assert list(since) == [status["standby_site"]]  # the start of the outage is kept from the first cycle


def test_global_manager_outage_marks_only_its_sync_channels(nsx):
    r = run()
    locations = list(r["location"])
    standby = next(n for n, g in r["global_manager"].items() if g["p"]["mode"] == "STANDBY")
    del nsx.data[GM_HOSTS[1]]  # the standby does not answer
    r = run()
    for n, c in r["sync_channel"].items():
        ends = [e.strip() for e in n.split("->")]
        between_locations = all(e in locations for e in ends)
        expected = "none" if between_locations or standby not in ends else "Global Manager not reachable"
        assert c["p"]["gm_outage"] == expected, n


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


# ---- regressions found by review

def test_a_local_manager_that_refuses_the_account_is_not_reached(nsx):
    lm = next(h for h in nsx.data if h not in GM_HOSTS)
    real = nsx.get

    def get(client, path, params=None):
        if client.host == lm:
            client.check_trust()
            client.calls += 1
            client.requests += 1
            client.errors += 1
            raise nsxclient.NsxError(401, path, "expired")
        return real(client, path, params)
    nsx.get = get
    r = run()
    reached = sorted(l["m"]["connectivity|collector_reachable"] for l in r["location"].values())
    assert reached == [0, 1] and fed(r)["m"]["locations|locations_collected"] == 1
    assert fed(r)["m"]["locations|locations_healthy_pct"] == 50


def test_a_failed_inventory_load_is_not_cached(nsx):
    r = run()
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    nsx.fail = ["/enforcement-points/default/edge-clusters"]
    assert fed(run())["m"]["collection|data_gaps"] >= 2  # reported, on both Local Managers
    nsx.fail = []
    r = run()  # the next cycle reads the inventory again and finds everything
    assert len(r["edge_cluster"]) == 2 and len(r["rtep_tunnel"]) == 8 and fed(r)["m"]["collection|data_gaps"] == 0


def test_a_failed_inventory_load_falls_back_to_the_last_good_one(nsx, monkeypatch):
    import adapter
    run()
    monkeypatch.setattr(adapter, "INVENTORY_TTL", 0)  # the cached inventory is due again
    nsx.fail = ["/enforcement-points/default/edge-clusters"]
    r = run()
    assert len(r["edge_cluster"]) == 2 and len(r["rtep_tunnel"]) == 8


def test_the_budget_keeps_calls_for_the_global_manager(nsx):
    segs = nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/global-infra/segments?page_size=500"]
    template = segs["results"][0]
    for i in range(300):
        segs["results"].append(dict(template, id=f"s-{i}", display_name=f"s-{i}", path=f"/global-infra/segments/s-{i}",
                                    unique_id=f"s-{i}"))
    r = run(call_budget="80")
    active = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "ACTIVE")
    assert "cluster|nodes_online" in active["m"] and "sync|standby_in_sync" in active["m"]
    assert fed(r)["m"]["collection|managers_over_budget"] >= 1 and len(r["sync_channel"]) == 9


def test_failed_statistics_send_no_counters(nsx):
    run()
    nsx.fail = ["/security-policies/", "/inter-site/statistics"]
    r = run()
    rates = [k for o in list(r["realization"].values()) + list(r["rtep_tunnel"].values()) for k in o["m"] if "per_" in k]
    assert rates == [] and len(r["rtep_tunnel"]) == 8  # the tunnels are still known from the BGP state
    assert all("remote_location" not in o["p"] and "totals|rx_bytes" not in o["m"] for o in r["rtep_tunnel"].values())
    assert all("intersite|rtep_tunnels" not in n["m"] for n in r["edge_node"].values())
    nsx.fail = []
    r = run()
    spikes = [v for o in r["realization"].values() for k, v in o["m"].items() if "per_min" in k and v > 0]
    assert spikes == []  # the counters did not restart from zero


@pytest.mark.parametrize("state", ["UNAVAILABLE", "DISCONNECTED", "STOPPED", "PAUSED"])
def test_only_known_states_count_as_in_sync(nsx, state):
    nsx.data[GM_HOSTS[0]][SITES_STATUS]["active_standby_sync_statuses"][0]["status"] = state
    r = run()
    assert next(g for g in r["global_manager"].values() if g["p"]["mode"] == "ACTIVE")["m"]["sync|standby_in_sync"] == 0


def test_a_location_without_a_connection_entry_is_unknown_not_down(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    names = list(run()["location"])
    status["remote_connections"] = [rc for rc in status["remote_connections"] if rc["site_name"] not in names[:1]]
    r = run()
    assert "connectivity|gm_connection_up" not in r["location"][names[0]]["m"]
    assert fed(r)["m"]["locations|locations_down"] == 0 and fed(r)["p"]["location_outage"] == "none"


def test_an_account_never_moves_to_another_federation(nsx):
    own = fed(run())["p"]["federation_id"]
    del nsx.data[GM_HOSTS[0]]  # its active Global Manager is down
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["id"] = "00000000-0000-0000-0000-00000000beef"  # the other is foreign
    r = run()
    assert fed(r)["p"]["federation_id"] == own and fed(r)["m"]["global_managers|global_managers_foreign"] == 1
    assert fed(r)["p"]["collection_mode"].startswith("degraded")


def test_stale_objects_are_kept_when_the_cycle_is_not_clean(nsx, ops):
    r = run()
    fed_id = fed(r)["p"]["federation_id"]
    ops.res = {"sync_channel": [("old", "gone -> x", f"{fed_id}:chan:gone>x", {}, 10)]}
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    nsx.fail = ["/api/v1/alarms"]  # one failed call is enough to skip the deletion
    run(delete_stale_after_days="7")
    assert ops.deleted == []
    nsx.fail = []
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    run(delete_stale_after_days="0.001")  # less than a day is read as one day; the object is 10 days old
    assert ops.deleted == ["old"]
    ops.deleted.clear()
    ops.res = {"sync_channel": [("young", "gone -> y", f"{fed_id}:chan:gone>y", {}, 0.5)]}
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))
    run(delete_stale_after_days="0.001")
    assert ops.deleted == []


def test_a_manager_that_keeps_timing_out_is_skipped(monkeypatch):
    import requests
    monkeypatch.setattr(nsxclient, "server_cert_sha256", lambda host, port=443, timeout=10.0: "a" * 64)
    monkeypatch.setattr(nsxclient.time, "sleep", lambda s: None)
    client = nsxclient.NsxClient("gm.example.test", "u", "p", enforce_trust=False)
    attempts = []

    def get(url, **kw):
        attempts.append(url)
        raise requests.Timeout()
    client.check_trust()
    monkeypatch.setattr(client._session, "get", get)
    for i in range(10):
        assert client.try_get(f"/api/v1/x{i}", default="unknown") == "unknown"
    assert len(attempts) == 3 and len(client.failed) == 10  # two calls with a retry, then nothing is sent


def test_retry_after_as_a_date_does_not_break_the_call(monkeypatch):
    monkeypatch.setattr(nsxclient, "server_cert_sha256", lambda host, port=443, timeout=10.0: "a" * 64)
    monkeypatch.setattr(nsxclient.time, "sleep", lambda s: None)
    client = nsxclient.NsxClient("gm.example.test", "u", "p", enforce_trust=False)
    client.check_trust()

    class Resp:
        status_code, headers, content, text = 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, b"", "busy"
    monkeypatch.setattr(client._session, "get", lambda url, **kw: Resp())
    assert client.try_get("/api/v1/x", default="unknown") == "unknown" and client.throttled == 3


# ---- second review: faults reproduced by an independent reviewer

STATS = "/policy/api/v1/global-infra/domains/default/security-policies/fed-demo/statistics"


def local_managers(nsx):
    return [h for h in nsx.data if h not in GM_HOSTS]


def wipe_cache():
    for f_ in os.listdir(nsxclient.CACHE_DIR):
        os.remove(os.path.join(nsxclient.CACHE_DIR, f_))


@pytest.fixture
def clock(monkeypatch):
    """Cycles five minutes apart: without it no rate is ever computed in a test."""
    import adapter
    now = {"now": time.time()}
    monkeypatch.setattr(adapter.time, "time", lambda: now["now"])
    return now


def test_a_failed_stretched_object_list_does_not_cost_the_locations(nsx):
    nsx.fail = ["/global-infra/segments"]
    r = run()
    assert len(r["location"]) == 2 and len(r["edge_cluster"]) == 2 and fed(r)["p"]["collection_mode"] == "normal"
    assert fed(r)["m"]["collection|data_gaps"] >= 1 and len(r.get("segment", {})) == 0


def test_many_policies_do_not_cost_the_global_manager_its_health(nsx):
    gm = nsx.data[GM_HOSTS[0]]
    pols = gm["/global-manager/api/v1/global-infra/domains/default/security-policies?page_size=500"]
    template = next(p for p in pols["results"] if p["id"] == "fed-demo")
    base = "/global-manager/api/v1/global-infra/domains/default/security-policies/"
    for i in range(100):
        pols["results"].append(dict(template, id=f"p{i}", display_name=f"p{i}",
                                    path=f"/global-infra/domains/default/security-policies/p{i}"))
        gm[base + f"p{i}"] = gm[base + "fed-demo"]
    r = run(call_budget="80")
    active = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "ACTIVE")
    assert "cluster|nodes_online" in active["m"] and len(r["location"]) == 2 and len(r["sync_channel"]) == 9
    # the active Global Manager and the two Local Managers (one statistics call per policy), each counted once
    assert fed(r)["m"]["collection|managers_over_budget"] == 3
    assert fed(r)["p"]["collection_mode"] == "normal"


def test_policy_hits_do_not_drop_and_burst_when_one_location_misses_a_cycle(nsx, clock):
    for h in local_managers(nsx):
        nsx.data[h][STATS]["results"][0]["statistics"]["results"][0]["hit_count"] = 1000
    for _ in range(2):
        r = run(rule_policies="*")
        clock["now"] += 300
    assert r["global_policy"]["fed-demo"]["m"]["traffic|hit_count_per_min"] == 0  # a rate is computed here
    real, bad = nsx.get, local_managers(nsx)[0]

    def get(client, path, params=None):
        if client.host == bad and path == STATS:
            client.check_trust()
            client.calls += 1
            client.requests += 1
            raise nsxclient.NsxError(500, path, "x")
        return real(client, path, params)
    nsx.get = get
    r = run(rule_policies="*")
    clock["now"] += 300
    assert "traffic|hit_count" not in r["global_policy"]["fed-demo"]["m"]  # unknown, not half the total
    nsx.get = real
    r = run(rule_policies="*")
    assert r["global_policy"]["fed-demo"]["m"].get("traffic|hit_count_per_min", 0) == 0
    assert all(o["m"].get("traffic|hit_count_per_min", 0) == 0 for o in r["rule"].values())


def test_failed_statistics_give_no_rate_in_the_next_cycle(nsx, clock):
    for h in local_managers(nsx):
        nsx.data[h][STATS]["results"][0]["statistics"]["results"][0]["hit_count"] = 1000
    for _ in range(2):
        r = run()
        clock["now"] += 300
    steady = [o["m"]["traffic|hit_count_per_min"] for n, o in r["realization"].items() if n.startswith("fed-demo @")]
    assert steady == [0, 0]  # the rate exists, so the next assertions can fail
    nsx.fail = [STATS]
    run()
    clock["now"] += 300
    nsx.fail = []
    r = run()
    assert all(o["m"].get("traffic|hit_count_per_min", 0) == 0 for n, o in r["realization"].items())


def test_a_cold_cache_does_not_let_a_foreign_global_manager_take_the_account(nsx, ops):
    r = run()
    name, f = next(iter(r["federation"].items()))
    own = f["p"]["federation_id"]
    ops.res = {"federation": [("f1", name, own, {"global_manager_addresses": f["p"]["global_manager_addresses"]}, 0)],
               "location": [(f"l{i}", n, f"{own}:site:{l['p']['site_id']}", l["p"], 0)
                            for i, (n, l) in enumerate(r["location"].items())]}
    wipe_cache()
    del nsx.data[GM_HOSTS[0]]
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["id"] = "00000000-0000-0000-0000-00000000beef"
    r = run()
    assert fed(r)["p"]["federation_id"] == own and fed(r)["m"]["global_managers|global_managers_foreign"] == 1


def test_channels_to_a_global_manager_that_answers_are_not_marked_on_a_cold_cache(nsx):
    del nsx.data[GM_HOSTS[1]]  # first cycle ever, and the standby does not answer
    r = run()
    locations, active = list(r["location"]), next(iter(r["global_manager"]))
    marked = {n for n, c in r["sync_channel"].items() if c["p"]["gm_outage"] != "none"}
    assert marked and all(not set(e.strip() for e in n.split("->")) <= set(locations + [active]) for n in marked)
    assert all(c["p"]["gm_outage"] == "none" for n, c in r["sync_channel"].items()
               if set(e.strip() for e in n.split("->")) <= set(locations + [active]))


def test_no_false_missing_standby_when_the_list_call_fails(nsx):
    nsx.fail = ["/global-infra/global-managers"]
    assert "standby_configured" not in fed(run(gm_hosts=[GM_HOSTS[0]]))["p"]


def test_a_failed_realization_call_is_a_data_gap_and_blocks_deletion(nsx, ops):
    fed_id = fed(run())["p"]["federation_id"]
    ops.res = {"sync_channel": [("old", "gone -> x", f"{fed_id}:chan:gone>x", {}, 10)]}
    wipe_cache()
    nsx.fail = ["realized-state/status?intent_path=/global-infra/tier-0s"]
    r = run(delete_stale_after_days="7")
    assert fed(r)["m"]["collection|data_gaps"] >= 1 and ops.deleted == []


def test_a_removed_standby_does_not_stay_out_of_sync(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    status["active_standby_sync_statuses"][0]["status"] = "ERROR"
    run()
    status["active_standby_sync_statuses"] = []
    active = next(g for g in run()["global_manager"].values() if g["p"]["mode"] == "ACTIVE")
    assert active["m"]["sync|standby_in_sync"] == 1 and active["m"]["sync|standby_out_of_sync_minutes"] == 0


def test_outage_symptoms_outlive_the_symptoms_they_keep_quiet():
    etree = pytest.importorskip("lxml.etree")
    doc = etree.parse(os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml"))
    cancel = {s.get("id"): int(s.get("cancelCycle") or 1) for s in doc.iter("SymptomDefinition")}
    checked = 0
    for alert in doc.iter("AlertDefinition"):
        for group in alert.iter("SymptomSet"):
            negated = [s.get("ref") for s in group.iter("Symptom") if s.get("negateCondition") == "true" and "outage" in s.get("ref")]
            others = [s.get("ref") for s in group.iter("Symptom") if s.get("negateCondition") != "true"]
            for n in negated:
                checked += 1
                assert all(cancel[n] > cancel[o] for o in others), (alert.get("id"), n)
    assert checked > 20


# ---- the real client on a fake transport: retries, throttling and the breaker run for real

class WireResponse:
    def __init__(self, status, data):
        self.status_code, self._data, self.headers, self.content, self.text = status, data, {}, b"x", "x"

    def json(self):
        return self._data


@pytest.fixture
def wire(monkeypatch, tmp_path):
    import copy
    import requests
    from conftest import FakeNsx
    fake = FakeNsx()
    fake.status, fake.sent = {}, []
    monkeypatch.setattr(nsxclient, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(nsxclient, "server_cert_sha256", lambda host, port=443, timeout=10.0: "f" * 64)
    monkeypatch.setattr(nsxclient.time, "sleep", lambda s: None)

    def get(self, url, params=None, timeout=None, **kw):
        host, _, path = url[len("https://"):].partition("/")
        fake.sent.append(host)
        if host in fake.status:
            return WireResponse(fake.status[host], {})
        key = "/" + path + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        answers = fake.data.get(host, {})
        return WireResponse(200, copy.deepcopy(answers[key])) if key in answers else WireResponse(404, {})
    monkeypatch.setattr(requests.Session, "get", get)
    return fake


def test_a_local_manager_that_answers_503_is_not_reached(wire):
    r = run()
    assert sorted(l["m"]["connectivity|collector_reachable"] for l in r["location"].values()) == [1, 1]
    assert len(r["rtep_tunnel"]) == 8 and fed(r)["m"]["collection|data_gaps"] == 0
    bad = local_managers(wire)[0]
    wire.status[bad] = 503
    wire.sent.clear()
    r = run()
    assert sorted(l["m"]["connectivity|collector_reachable"] for l in r["location"].values()) == [0, 1]
    assert fed(r)["m"]["collection|api_calls_throttled"] >= 3
    assert wire.sent.count(bad) <= 12  # three calls with their retries, then the manager is skipped
