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
        assert "backup_enabled" not in gm["p"]
        if gm["p"]["mode"] == "ACTIVE":  # a standby always reads 0 entries: it sends nothing itself
            assert "sync|standby_sync_entries" not in gm["m"]
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
    assert fed(r)["p"]["collection_mode"] == "degraded (no Global Manager answers): location list from VCF Operations"
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


# ---- final validation: faults found in 1.7.2

def test_a_policy_realized_at_one_location_gets_its_counters(nsx, clock):
    status = "/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/domains/default/security-policies/fed-demo"
    gm = nsx.data[GM_HOSTS[0]]
    eps = gm[status]["consolidated_status_per_enforcement_point"]
    gm[status]["consolidated_status_per_enforcement_point"] = eps[:1]  # realized at one location only
    locations = run()["location"]
    inside = next(n for n, l in locations.items() if l["p"]["site_path"] == eps[0]["site_path"])
    host_of = {l["p"]["local_manager_address"]: n for n, l in locations.items()}
    for h in local_managers(nsx):
        if host_of.get(h) != inside:
            del nsx.data[h][STATS]  # the Local Manager outside the span answers 404 for the statistics
        else:
            nsx.data[h][STATS]["results"][0]["statistics"]["results"][0]["hit_count"] = 1000
    wipe_cache()
    for _ in range(2):
        r = run()
        clock["now"] += 300
    pol = r["global_policy"]["fed-demo"]
    assert pol["m"]["traffic|hit_count"] == 1000 and pol["m"]["traffic|hit_count_per_min"] == 0
    assert fed(r)["m"]["collection|data_gaps"] == 0  # the location outside the span is not asked


def test_a_rebuilt_federation_is_followed_by_a_new_account(nsx, ops):
    r = run()
    name, f = next(iter(r["federation"].items()))
    old = f["p"]["federation_id"]
    ops.res = {"federation": [("f1", name, old, {"global_manager_addresses": f["p"]["global_manager_addresses"]}, 0)]}
    wipe_cache()
    new = "00000000-0000-0000-0000-00000000cafe"
    for h in GM_HOSTS:  # both listed Global Managers answer for the rebuilt federation
        nsx.data[h]["/api/v1/sites/self"]["id"] = new
    r = run()
    assert fed(r)["p"]["federation_id"] == new and fed(r)["m"]["global_managers|global_managers_foreign"] == 0
    assert len(r["location"]) == 2


def test_one_refused_call_does_not_make_a_location_unreachable(nsx):
    lm = local_managers(nsx)[0]
    real = nsx.get

    def get(client, path, params=None):
        if client.host == lm and path == "/api/v1/cluster/status":
            client.check_trust()
            client.calls += 1
            client.requests += 1
            raise nsxclient.NsxError(403, path, "role")
        return real(client, path, params)
    nsx.get = get
    r = run()
    assert sorted(l["m"]["connectivity|collector_reachable"] for l in r["location"].values()) == [1, 1]


def test_no_false_missing_standby_when_the_sites_call_fails(nsx):
    nsx.fail = ["/api/v1/sites?"] if False else []
    real = nsx.get

    def get(client, path, params=None):
        if path == "/api/v1/sites" and client.host == GM_HOSTS[0]:
            client.check_trust()
            client.calls += 1
            client.requests += 1
            raise nsxclient.NsxError(500, path, "x")
        return real(client, path, params)
    nsx.get = get
    assert "standby_configured" not in fed(run(gm_hosts=[GM_HOSTS[0]]))["p"]


def test_a_standby_does_not_keep_the_values_of_the_active(nsx):
    r = run()
    standby = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "STANDBY")
    assert standby["p"]["switchover_status"] == "not applicable (standby)"
    assert standby["m"]["sync|standby_sync_entries"] == 0


def test_a_sync_channel_without_stubs_is_unknown(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    status["remote_connections"][0]["stubs"] = []
    name = status["remote_connections"][0]["site_name"]
    gm = next(n for n, g in run()["global_manager"].items() if g["p"]["mode"] == "ACTIVE")
    r = run()
    assert "connection_up" not in r["sync_channel"][f"{gm} -> {name}"]["m"]


# ---- last review of 1.7.3

POLICY_STATUS = "/global-manager/api/v1/global-infra/realized-state/status?intent_path=/global-infra/domains/default/security-policies/fed-demo"


def test_a_policy_with_no_known_span_keeps_its_counters(nsx, clock):
    nsx.data[GM_HOSTS[0]][POLICY_STATUS]["consolidated_status_per_enforcement_point"] = []
    for h in local_managers(nsx):
        nsx.data[h][STATS]["results"][0]["statistics"]["results"][0]["hit_count"] = 1000
    r = run()
    assert r["global_policy"]["fed-demo"]["m"]["traffic|hit_count"] == 2000


def test_a_span_with_no_known_location_sends_no_false_zero(nsx):
    eps = nsx.data[GM_HOSTS[0]][POLICY_STATUS]["consolidated_status_per_enforcement_point"]
    for ep in eps:
        ep["site_path"] = "/global-infra/sites/offboarded"
    r = run()
    assert "traffic|hit_count" not in r["global_policy"]["fed-demo"]["m"]


def test_a_standby_gets_its_own_sync_values(nsx):
    r = run()
    standby = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "STANDBY")
    assert standby["m"]["sync|standby_out_of_sync_minutes"] == 0
    assert standby["m"]["sync|standby_full_sync_age_hours"] >= 0


def test_metric_labels_carry_no_unit():
    import re
    import adapter
    for kind, (_l, metrics, _p) in adapter.MODEL.items():
        for key, label in metrics:
            if key in adapter.UNITS:  # VCF Operations adds the unit to the label
                assert not re.search(r"\((s|ms|min|h|%|hours ago|min ago)\)", label), (kind, key, label)


# ---- review of 1.7.4

def test_a_policy_absent_from_a_local_manager_is_not_a_data_gap(nsx):
    nsx.data[GM_HOSTS[0]][POLICY_STATUS]["consolidated_status_per_enforcement_point"] = []  # span unknown
    for h in local_managers(nsx):
        del nsx.data[h][STATS]  # the Local Managers answer 404 for its statistics
    r = run()
    assert fed(r)["m"]["collection|data_gaps"] == 0 and fed(r)["m"]["collection|api_errors"] == 0
    assert "traffic|hit_count" not in r["global_policy"]["fed-demo"]["m"]


def test_a_standby_shows_how_long_it_has_been_out_of_sync(nsx, clock):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]["active_standby_sync_statuses"][0]
    status["status"], status["full_sync_status"]["status"] = "ERROR", "FAILED"
    run()
    clock["now"] += 900
    r = run()
    for g in r["global_manager"].values():
        assert g["m"]["sync|standby_in_sync"] == 0 and g["m"]["sync|standby_out_of_sync_minutes"] == 15


def test_a_global_manager_with_no_role_does_not_stay_out_of_sync(nsx):
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]
    status["active_standby_sync_statuses"][0]["status"] = "ERROR"
    run()
    status["active_standby_sync_statuses"] = []  # the active no longer reports it
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["site"]["active_gm"] = "NONE"
    r = run()
    lost = next(g for g in r["global_manager"].values() if g["p"]["mode"] == "NONE")
    assert lost["m"]["sync|standby_in_sync"] == 1 and lost["p"]["switchover_status"] == "not reported (mode NONE)"


def test_the_collection_mode_says_why_it_is_degraded(nsx):
    run()
    nsx.data[GM_HOSTS[0]]["/api/v1/sites/self"]["site"]["active_gm"] = "STANDBY"  # both answer, none is active
    assert fed(run())["p"]["collection_mode"] == "degraded (no active Global Manager): location list from cache"


def test_backup_result_is_sent_for_locations(nsx):
    r = run()
    assert all(l["p"]["backup_last_result"] in ("SUCCESS", "FAILED", "NONE") for l in r["location"].values())


def test_a_backup_that_never_ran_has_an_alert():
    etree = pytest.importorskip("lxml.etree")
    doc = etree.parse(os.path.join(ROOT, "pack", "content", "alertdefs", "nsxfederation_alerts.xml"))
    for kind in ("gm", "loc"):
        alert = next(a for a in doc.iter("AlertDefinition") if a.get("id").endswith(f"-{kind}_backup"))
        refs = {(s.get("ref").split("-")[-1], s.get("negateCondition")) for s in alert.iter("Symptom")}
        assert (f"{kind}_backup_never", None) in refs and (f"{kind}_backup_off", "true") in refs


# ---- last review of 1.7.5

def test_a_404_where_the_policy_is_realized_is_a_gap_not_a_drop(nsx, clock):
    for h in local_managers(nsx):
        nsx.data[h][STATS]["results"][0]["statistics"]["results"][0]["hit_count"] = 1000
    for _ in range(2):
        r = run()
        clock["now"] += 300
    bad = local_managers(nsx)[0]
    saved = nsx.data[bad].pop(STATS)  # one cycle of 404 at a location where the policy is realized
    r = run()
    clock["now"] += 300
    assert "traffic|hit_count" not in r["global_policy"]["fed-demo"]["m"] and fed(r)["m"]["collection|data_gaps"] >= 1
    nsx.data[bad][STATS] = saved
    r = run()
    pol = r["global_policy"]["fed-demo"]["m"]
    assert pol["traffic|hit_count"] == 2000 and pol.get("traffic|hit_count_per_min", 0) == 0  # no burst when it is back


def test_a_global_manager_that_stops_answering_reads_not_reachable(nsx):
    names = {g["p"]["address"]: n for n, g in run()["global_manager"].items()}
    del nsx.data[GM_HOSTS[1]]
    r = run()
    assert r["global_manager"][names[GM_HOSTS[1]]]["m"]["connectivity|reachable"] == 0
    assert r["global_manager"][names[GM_HOSTS[0]]]["m"]["connectivity|reachable"] == 1


# ---- last review of 1.7.6

def test_an_unreachable_standby_still_gets_its_sync_state(nsx):
    run()
    status = nsx.data[GM_HOSTS[0]][SITES_STATUS]["active_standby_sync_statuses"][0]
    status["status"], status["full_sync_status"]["status"] = "ERROR", "FAILED"
    del nsx.data[GM_HOSTS[1]]  # the collector cannot reach the standby
    r = run()
    standby = r["global_manager"][status["standby_site"]]
    assert standby["m"]["connectivity|reachable"] == 0 and standby["m"]["sync|standby_in_sync"] == 0


def test_after_a_restart_a_global_manager_that_is_down_reads_not_reachable(nsx, ops):
    r = run()
    fed_id = fed(r)["p"]["federation_id"]
    names = {g["p"]["address"]: n for n, g in r["global_manager"].items()}
    ops.res = {"global_manager": [(f"g{i}", n, f"{fed_id}:gm:{x}", {"address": a}, 0) for i, (a, n, x) in enumerate(
        (a, n, nsx.data[a]["/api/v1/sites/self"]["site"]["id"]) for a, n in names.items())]}
    wipe_cache()
    del nsx.data[GM_HOSTS[1]]
    r = run()
    assert r["global_manager"][names[GM_HOSTS[1]]]["m"]["connectivity|reachable"] == 0


def test_an_address_that_now_answers_for_another_federation_reads_not_reachable(nsx):
    names = {g["p"]["address"]: n for n, g in run()["global_manager"].items()}
    nsx.data[GM_HOSTS[1]]["/api/v1/sites/self"]["id"] = "00000000-0000-0000-0000-00000000beef"
    r = run()
    assert r["global_manager"][names[GM_HOSTS[1]]]["m"]["connectivity|reachable"] == 0


def test_a_second_address_of_a_manager_that_answers_is_not_marked_down(nsx):
    run(gm_hosts=GM_HOSTS + ["alias.example.test"])  # alias of nothing: never seen, nothing sent
    r = run(gm_hosts=GM_HOSTS + ["alias.example.test"])
    assert all(g["m"]["connectivity|reachable"] == 1 for g in r["global_manager"].values())


def tunnel_stats(nsx):
    """Every per-tunnel statistic of the fixture, to change the counters between cycles."""
    return [t for h in nsx.data for k, v in nsx.data[h].items() if k.endswith("/inter-site/statistics")
            for site in v.get("stats_per_site") or [] for t in site.get("stats_per_tunnel") or []]


def bump(nsx, rx_packets=0, rx_dropped=0, tx_packets=0, tx_dropped=0, rx_blocked=0):
    for t in tunnel_stats(nsx):
        rx, tx = t.setdefault("rx", {}), t.setdefault("tx", {})
        rx["total_packets"] = int(rx.get("total_packets") or 0) + rx_packets
        rx["dropped_packets"] = int(rx.get("dropped_packets") or 0) + rx_dropped
        rx["blocked_packets"] = int(rx.get("blocked_packets") or 0) + rx_blocked
        tx["total_packets"] = int(tx.get("total_packets") or 0) + tx_packets
        tx["dropped_packets"] = int(tx.get("dropped_packets") or 0) + tx_dropped


def test_tunnel_drops_are_also_a_percentage(nsx, clock):
    assert tunnel_stats(nsx)
    run()
    clock["now"] += 300
    bump(nsx, rx_packets=1000, rx_dropped=50, tx_packets=1000)  # NSX counts the 50 dropped in the 1000
    r = run()
    for t in r["rtep_tunnel"].values():
        assert t["m"]["rates|rx_drop_pct"] == 5.0 and t["m"]["rates|tx_drop_pct"] == 0
        assert t["m"]["rates|rx_dropped_per_min"] == 10.0


def test_an_idle_tunnel_gets_no_drop_percentage(nsx, clock):
    run()
    clock["now"] += 300
    bump(nsx, rx_packets=20, rx_dropped=1)  # one lost packet out of 21 is not 5% of anything
    r = run()
    assert all("rates|rx_drop_pct" not in t["m"] for t in r["rtep_tunnel"].values())


def test_a_counter_reset_gives_no_drop_percentage(nsx, clock):
    run()
    clock["now"] += 300
    for t in tunnel_stats(nsx):  # NSX restarted the counters: smaller than in the previous cycle
        t["rx"]["total_packets"], t["rx"]["dropped_packets"] = 10, 5
    r = run()
    assert all("rates|rx_drop_pct" not in t["m"] for t in r["rtep_tunnel"].values())


def test_drop_pct_and_rtt_pct():
    import adapter
    prev, cur = {"d": 10, "p": 1000}, {"d": 20, "p": 2000}
    assert adapter.drop_pct(prev, cur, "d", "p") == 1.0
    assert adapter.drop_pct(prev, {"d": 11, "p": 1010}, "d", "p") is None  # 10 packets: too few
    assert adapter.drop_pct(prev, {"d": 200, "p": 1100}, "d", "p") is None  # more drops than packets: read apart
    assert adapter.drop_pct({}, cur, "d", "p") is None
    assert adapter.rtt_pct(200, 250) == 80.0 and adapter.rtt_pct(None, 250) is None
    assert adapter.rtt_pct(10, None) is None and adapter.rtt_pct(10, 0) is None and adapter.rtt_pct(10, "x") is None


def bgp_status(nsx):
    return [n for h in nsx.data for k, v in nsx.data[h].items() if "/bgp/neighbors/status" in k for n in v.get("results") or []]


def test_a_bgp_session_drop_gets_a_rate(nsx, clock):
    assert bgp_status(nsx)
    for n in bgp_status(nsx):
        n["connection_drop_count"] = 3
    run()
    clock["now"] += 300
    r = run()
    assert all(o["m"]["connection_drops_per_hour"] == 0 for o in r["bgp_neighbor"].values())
    for n in bgp_status(nsx):
        n["connection_drop_count"] = 4
    clock["now"] += 300
    r = run()
    assert all(o["m"]["connection_drops_per_hour"] == 12.0 for o in r["bgp_neighbor"].values())


def flow(src, dst, ms, cur=0, mx=1000):
    return {"flow_type": "GM_TO_LM", "queue_infos": [{"namespace": "GM_2_LM", "type": "TRANSMITTER", "current_size": cur, "max_size": mx}],
            "cross_site_flow_info": {"status": "GOOD", "from_site_path": src, "to_site_path": dst, "latency_millis": ms}}


def intersite_summaries(nsx):
    return [n for h in nsx.data for k, v in nsx.data[h].items() if k.endswith("/inter-site/bgp/summary") for n in v["neighbor_status"]]


def test_an_intersite_session_drop_gets_a_rate_on_the_tunnel(nsx, clock):
    assert intersite_summaries(nsx)
    run()
    clock["now"] += 300
    r = run()
    assert all(t["m"]["connection_drops_per_hour"] == 0 for t in r["rtep_tunnel"].values())
    for n in intersite_summaries(nsx):
        n["connection_drop_count"] = 1
    clock["now"] += 300
    r = run()
    assert all(t["m"]["connection_drops_per_hour"] == 12.0 for t in r["rtep_tunnel"].values())
    assert fed(r)["m"]["collection|data_gaps"] == 0


def test_a_node_without_the_bgp_summary_is_not_a_data_gap(nsx):
    for h in nsx.data:
        for k in [k for k in nsx.data[h] if k.endswith("/inter-site/bgp/summary")]:
            del nsx.data[h][k]  # answers 404 in the fake: no inter-site BGP on that node
    r = run()
    assert fed(r)["m"]["collection|data_gaps"] == 0
    assert all("connection_drops" not in t["m"] for t in r["rtep_tunnel"].values())


def test_a_failed_bgp_summary_is_a_data_gap_and_sends_no_counter(nsx):
    nsx.fail.append("/inter-site/bgp/summary")
    r = run()
    assert fed(r)["m"]["collection|data_gaps"] == 4  # one per edge node
    assert all("connection_drops" not in t["m"] for t in r["rtep_tunnel"].values())


def test_rtt_and_sync_queue_come_from_the_flow_details(nsx):
    gm, a, b = "/global-infra/global-managers/gm-site-a", "/global-infra/sites/site-a-wld01", "/global-infra/sites/site-a-wld03"
    nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/observability/flow-details"] = {"errors": [], "flow_info": [
        flow(gm, a, 3), flow(a, gm, 5, cur=250), flow(gm, b, 200), flow(b, gm, 190),
        {"flow_type": "GM_TO_GM", "queue_infos": [{"current_size": 0}],  # between Global Managers: left out
         "cross_site_flow_info": {"from_site_path": gm, "to_site_path": "/global-infra/global-managers/gm-site-b"}}]}
    r = run()
    wld01, wld03 = r["location"]["site-a-wld01"]["m"], r["location"]["site-a-wld03"]["m"]
    assert wld01["connectivity|gm_rtt_ms"] == 5 and wld01["sync|sync_queue_used_pct"] == 25.0
    assert wld03["connectivity|gm_rtt_ms"] == 200 and wld03["connectivity|gm_rtt_pct_of_max"] == 80.0  # maximum_rtt 250 ms
    assert fed(r)["m"]["collection|data_gaps"] == 0


def test_without_flow_details_the_rtt_comes_from_the_replication_status(nsx):
    for h in GM_HOSTS:  # no flow-details in the fixture: an NSX without that API answers 404, which is no data gap
        for k, v in nsx.data[h].items():
            if k.startswith("/api/v1/federation/replication/gm/sites/"):
                for f in v.get("flow_statuses") or []:
                    f["rtt_statistic"] = {"average_rtt": 50.0}
    r = run()
    for loc in r["location"].values():
        assert loc["m"]["connectivity|gm_rtt_ms"] == 50.0
        assert loc["m"]["connectivity|gm_rtt_pct_of_max"] == 20.0
        assert "sync|sync_queue_used_pct" not in loc["m"]
    assert fed(r)["m"]["collection|data_gaps"] == 0


def test_new_network_alerts_follow_the_rules():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_alerts", os.path.join(ROOT, "tools", "gen_alerts.py"))
    ga = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ga)
    alerts = {a[0]: a for a in ga.A}
    assert ga.S["tun_rx_loss1"][6] == 1 and ga.S["tun_rx_loss5"][6] == 5
    # a percentage never fires alone: each drop condition also needs more than 10 drops per minute
    assert all(any(s.startswith("tun_") and s.endswith("_drops") for s in group) for group in alerts["tun_drops"][6])
    # the flap needs the session up in this cycle: the 2-cycle "down" symptom would let the first cycle of an outage through
    assert all("bgp_up_now" in group for group in alerts["bgp_flap"][6]) and ga.WAIT.get("bgp_up_now", (1, 1)) == (1, 1)
    assert all("tun_up_now" in group for group in alerts["tun_flap"][6]) and ga.WAIT.get("tun_up_now", (1, 1)) == (1, 1)
    # every federation alarm type of NSX 9.1.1 (GET /api/v1/events, feature "federation") has a named alert
    assert set(ga.GM_ALARMS) >= {"error_in_delta_sync", "connectivity_to_ldap_server_lost"} and len(ga.GM_ALARMS) == 15
    for k in ("tun_blocked", "tun_flap", "bgp_flap", "loc_rtt"):  # quiet during a location outage like the other network alerts
        assert all(any(s.startswith("!outage_") for s in group) for group in alerts[k][6])


def test_flow_latency_is_kept_when_the_replication_status_call_fails(nsx):
    gm, a = "/global-infra/global-managers/gm-site-a", "/global-infra/sites/site-a-wld01"
    nsx.data[GM_HOSTS[0]]["/global-manager/api/v1/observability/flow-details"] = {"flow_info": [flow(gm, a, 42, cur=100)]}
    nsx.fail.append("/api/v1/federation/replication/gm/sites/")
    m = run()["location"]["site-a-wld01"]["m"]
    assert m["connectivity|gm_rtt_ms"] == 42 and m["sync|sync_queue_used_pct"] == 10.0
    assert "sync|replication_flows" not in m  # that one does come from the failed call


def test_a_reset_of_the_session_drop_counter_gives_a_rate_of_zero(nsx, clock):
    run()
    for n in intersite_summaries(nsx):
        n["connection_drop_count"] += 1
    clock["now"] += 300
    assert all(t["m"]["connection_drops_per_hour"] == 12.0 for t in run()["rtep_tunnel"].values())
    for n in intersite_summaries(nsx):
        n["connection_drop_count"] = 0  # the edge restarted
    clock["now"] += 300
    assert all(t["m"]["connection_drops_per_hour"] == 0 for t in run()["rtep_tunnel"].values())


def test_flows_between_locations_and_negative_latency_are_not_a_round_trip_time_to_the_global_manager():
    import adapter
    site = flow("/global-infra/sites/loc-a", "/global-infra/sites/loc-b", 400)
    site["flow_type"] = "LM_TO_LM"
    unknown = flow("/global-infra/global-managers/gm-a", "/global-infra/sites/loc-a", -1)
    good = flow("/global-infra/sites/loc-b", "/global-infra/global-managers/gm-a", 7)
    good["flow_type"] = "LM_TO_GM"
    out = adapter.flow_summary({"flow_info": [site, unknown, good]})
    assert out["/global-infra/sites/loc-a"]["rtt_ms"] is None
    assert out["/global-infra/sites/loc-b"]["rtt_ms"] == 7.0
