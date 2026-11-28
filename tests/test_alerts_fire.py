"""Fault injection: every alert definition of the pack is made to fire.

Each scenario changes the recorded answers of the mock NSX the way a real fault would show in the API (an edge down,
a BGP session idle, a certificate about to expire, an NSX alarm open...), runs real collection cycles of the adapter,
and evaluates the alert definitions of pack/content/alertdefs with tests/alert_engine.py, the way VCF Operations
would. A scenario passes when its alert fires on the object it is about, after as many cycles as the alert waits.

What cannot be judged offline is checked as far as it goes: an alert on a dynamic threshold needs weeks of data in
VCF Operations, so its test only checks that the metric it watches is sent.
"""
import time

import pytest

import alert_engine
from conftest import GM_HOSTS, run

GM = GM_HOSTS[0]  # the active Global Manager
GM_B = GM_HOSTS[1]  # the standby
LM_A, LM_B = "192.0.2.2", "192.0.2.6"  # Local Managers of site-a-wld01 and site-a-wld03
SITE_B_ID = "50adcaea-7448-8f66-da51-251459b09927"  # site-a-wld03
EDGE_A = "0c93aefb-3c4d-45cd-4e06-3a368184f76a"  # an edge of site-a-wld01
EDGE_A_NAME = "edge-wld01-01a"
EDGE_CLUSTER_A = "a2225e06-d12a-4b5b-868d-f4805dd57139"
ALARMS = "/api/v1/alarms?page_size=500&status=OPEN"
REALIZED = "/global-manager/api/v1/global-infra/realized-state/status?intent_path="
BGP_A = "/policy/api/v1/global-infra/tier-0s/fed-t0/locale-services/site-a-wld01/bgp/neighbors/status?page_size=500"
DAY_MS = 86400 * 1000
SLOW_TTL_S = 3600  # adapter.SLOW_TTL: certificates and backups are cached for an hour
# conditions of the recorded lab itself: a paused upgrade at one location, no backup on the Global Managers, backups
# of the Local Managers older than a day, certificates reported but not checked (trust "report"), one NSX limit in
# its warning range at each location
BASELINE = {"loc_upgrade", "gm_backup", "loc_cert_trust", "loc_backup", "loc_capacity"}

D = alert_engine.defs()


class Clock:
    """Wall clock of the cycles: each cycle is five minutes after the one before, so rates have a time base."""

    def __init__(self, monkeypatch):
        self.offset = 0.0
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + self.offset)

    def step(self, seconds=300):
        self.offset += seconds


def cycles(nsx, clock, n, mutate=None, **settings):
    """Run n collection cycles, five minutes apart. mutate(nsx.data, i) runs before cycle i. Returns
    {alert id: set of object names} of the alerts that fire in every one of the last `wait` cycles of each alert,
    and the last cycle."""
    history = []
    for i in range(n):
        if mutate:
            mutate(nsx.data, i)
        if i:
            clock.step()
        r = run(**settings)
        assert not r["_error"], r["_error"]
        history.append((r, D.fired(r)))
    fired = {}
    for aid, a in D.alerts.items():
        last = [set(f.get(aid, ())) for _, f in history[-a["wait"]:]]
        if len(last) == a["wait"] and last and set.intersection(*last):
            fired[aid] = set.intersection(*last)
    return fired, history[-1][0]


def alarm(event_type, severity="HIGH"):
    return {"feature_name": "federation", "event_type": event_type, "severity": severity, "status": "OPEN",
            "summary": f"injected {event_type}", "node_display_name": "x"}


def add_alarm(data, host, event_type):
    doc = data[host].setdefault(ALARMS, {"results": []})
    doc.setdefault("results", []).append(alarm(event_type))
    doc["result_count"] = len(doc["results"])


def set_cert_expiry(data, host, days):
    certs = data[host]["/api/v1/trust-management/certificates?details=true&page_size=500"]["results"]
    certs[0]["details"][0]["not_after"] = int(time.time() * 1000 + days * DAY_MS)


def set_realization(data, intent_path, state, site="site-a-wld03"):
    st = data[GM][REALIZED + intent_path]
    for ep in st["consolidated_status_per_enforcement_point"]:
        if ep["site_path"].endswith("/" + site):
            ep["consolidated_status"] = {"consolidated_status": state}
    st["consolidated_status"] = {"consolidated_status": state}


def edge_status(data, host=LM_A, edge=EDGE_A):
    return data[host][f"/api/v1/transport-nodes/{edge}/status"]


def bgp_neighbors(data, host=LM_A):
    return data[host][BGP_A]["results"]


def intersite_bgp_down(data, i):
    """Both inter-site BGP sessions of EDGE_A go idle: the edge cluster status reports it per member."""
    members = data[LM_A][f"/api/v1/edge-clusters/{EDGE_CLUSTER_A}/inter-site/status"]["member_status"]
    m = next(m for m in members if m["transport_node"]["target_id"] == EDGE_A)
    m["established_bgp_sessions"] = 0
    for n in m["neighbor_status"]:
        n["connection_state"] = "IDLE"


def tunnel_counters(field, step):
    """Every cycle, one RTEP tunnel of EDGE_A counts `step` more packets of `field` (and 1000 more in total)."""
    def mutate(data, i):
        stats = data[LM_A][f"/api/v1/transport-nodes/{EDGE_A}/inter-site/statistics"]["stats_per_site"]
        t = stats[0]["stats_per_tunnel"][0]
        for direction in ("rx",):
            t[direction][field] += step * (1 if i else 0)
            t[direction]["total_packets"] += 1000 * (1 if i else 0)
    return mutate


def tx(name):
    """Objects named like `name` (substring)."""
    return lambda objs: any(name in o for o in objs)


# ---- scenarios: alert id -> (number of cycles, mutate(data, cycle), check on the objects it fires on) ----------------
def gm_upgrade(data, i):
    data[GM]["/api/v1/upgrade/summary"]["upgrade_status"] = "FAILED"


def gm_cluster(data, i):
    mcs = data[GM]["/api/v1/cluster/status"]["mgmt_cluster_status"]
    mcs["status"] = "DEGRADED"
    mcs["offline_nodes"] = [{"uuid": "dead-node", "mgmt_cluster_listen_ip_address": "192.0.2.99"}]


def gm_password(data, i):
    users = data[GM]["/api/v1/node/users"]["results"]
    me = next(u for u in users if u["username"] == "audit")
    me["password_change_frequency"], me["last_password_change"] = 90, 88


def loc_repl(data, i):
    rep = data[GM][f"/api/v1/federation/replication/gm/sites/{SITE_B_ID}/status"]
    rep["flow_statuses"][1]["replication_state"] = "ERROR"


def loc_full_sync(data, i):
    data[LM_A]["/policy/api/v1/infra/full-sync-states?page_size=500"]["results"][0]["last_completed_stage"] = "FAILED"


def ec_status(data, i):
    data[LM_A][f"/api/v1/edge-clusters/{EDGE_CLUSTER_A}/inter-site/status"]["overall_status"] = "DOWN"


def chan_down(data, i):
    rc = data[LM_A]["/api/v1/sites/status"]["remote_connections"]
    next(c for c in rc if c["site_name"] == "site-a-wld03")["stubs"][0]["connection_up"] = False


def bgp_down(data, i):
    n = bgp_neighbors(data)[0]
    n["connection_state"] = "IDLE"
    n["established_connection_count"] = 0


def bgp_no_prefix(data, i):
    """The uplink neighbor stays established but sends no route."""
    for n in bgp_neighbors(data):
        if not n["neighbor_address"].startswith("169.254."):
            n["total_in_prefix_count"] = 0


def edge_down(data, i):
    edge_status(data)["status"] = "DOWN"


def edge_cpu(data, i):
    cpu = edge_status(data)["node_status"]["system_status"]["cpu_usage"]
    cpu["avg_cpu_core_usage_dpdk"] = cpu["highest_cpu_core_usage_dpdk"] = 92.0


def edge_memory(data, i):
    edge_status(data)["node_status"]["system_status"]["edge_mem_usage"]["system_mem_usage"] = 95.0


def edge_disk(data, i):
    fs = edge_status(data)["node_status"]["system_status"]["file_systems"][0]
    fs["used"] = int(fs["total"] * 0.95)


SCENARIOS = {
    "gm_upgrade": (1, gm_upgrade, "gm-site-a"),
    "gm_cluster": (1, gm_cluster, "gm-site-a"),
    "gm_cert": (1, lambda d, i: set_cert_expiry(d, GM, 5), "gm-site-a"),
    "gm_password": (1, gm_password, "gm-site-a"),
    "loc_cert": (1, lambda d, i: set_cert_expiry(d, LM_A, 5), "site-a-wld01"),
    "loc_repl": (2, loc_repl, "site-a-wld03"),
    "loc_full_sync": (2, loc_full_sync, "site-a-wld01"),
    "loc_intersite": (2, intersite_bgp_down, "site-a-wld01"),
    "edge_bgp_down": (2, intersite_bgp_down, EDGE_A_NAME),
    "tun_bgp_down": (2, intersite_bgp_down, EDGE_A_NAME),
    "ec_status": (1, ec_status, "fed-ec-a"),
    "chan_down": (2, chan_down, "site-a-wld01"),
    "tun_drops": (3, tunnel_counters("dropped_packets", 200), EDGE_A_NAME),
    "tun_blocked": (3, tunnel_counters("blocked_packets", 200), EDGE_A_NAME),
    "t0_error": (1, lambda d, i: set_realization(d, "/global-infra/tier-0s/fed-t0", "ERROR"), "fed-t0"),
    "t1_error": (1, lambda d, i: set_realization(d, "/global-infra/tier-1s/fed-t1", "ERROR"), "fed-t1"),
    "seg_error": (1, lambda d, i: set_realization(d, "/global-infra/segments/fed-stretched", "ERROR"), "fed-stretched"),
    "pol_error": (1, lambda d, i: set_realization(d, "/global-infra/domains/default/security-policies/fed-demo", "ERROR"),
                  "fed-demo"),
    "real_missing": (2, lambda d, i: set_realization(d, "/global-infra/tier-0s/fed-t0", "ERROR"), "fed-t0"),
    "bgp_down": (2, bgp_down, ""),
    "bgp_no_prefix": (2, bgp_no_prefix, ""),
    "edge_status": (2, edge_down, EDGE_A_NAME),
    "edge_cpu": (3, edge_cpu, EDGE_A_NAME),
    "edge_memory": (3, edge_memory, EDGE_A_NAME),
    "edge_disk": (3, edge_disk, EDGE_A_NAME),
}
# the federation alarms of NSX: raised on the Global Manager, and (except Global Manager to Global Manager) on a
# Local Manager, where the pack shows them on the location
for _aid in [a for a in D.alerts if a.startswith("alarm_")]:
    _ev = _aid[len("alarm_"):]
    SCENARIOS[_aid] = (1, (lambda ev: lambda d, i: add_alarm(d, GM, ev))(_ev), "gm-site-a")
for _aid in [a for a in D.alerts if a.startswith("loc_alarm_")]:
    _ev = _aid[len("loc_alarm_"):]
    SCENARIOS[_aid] = (1, (lambda ev: lambda d, i: add_alarm(d, LM_A, ev))(_ev), "site-a-wld01")


@pytest.mark.parametrize("alert_id", sorted(SCENARIOS))
def test_the_fault_fires_its_alert(nsx, monkeypatch, alert_id):
    n, mutate, target = SCENARIOS[alert_id]
    fired, _ = cycles(nsx, Clock(monkeypatch), max(n, D.alerts[alert_id]["wait"]), mutate)
    assert alert_id in fired, f"{alert_id} did not fire; fired: {sorted(fired)}"
    assert any(target in o for o in fired[alert_id]), f"{alert_id} fired on {fired[alert_id]}, expected {target}"


def test_a_healthy_cycle_fires_only_the_conditions_of_the_recorded_lab(nsx, monkeypatch):
    fired, _ = cycles(nsx, Clock(monkeypatch), 3)
    assert set(fired) == BASELINE


@pytest.mark.parametrize("alert_id", ["gm_cert", "edge_status", "bgp_down", "alarm_rtep_bgp_down"])
def test_the_alert_cancels_when_the_fault_is_gone(nsx, monkeypatch, alert_id):
    import copy
    n, mutate, _ = SCENARIOS[alert_id]
    clean = copy.deepcopy(nsx.data)
    clock = Clock(monkeypatch)
    fired, _ = cycles(nsx, clock, max(n, D.alerts[alert_id]["wait"]), mutate)
    assert alert_id in fired
    nsx.data = clean
    clock.step(SLOW_TTL_S + 1)  # certificates and backups are read once an hour
    assert alert_id not in D.fired(run())


def test_during_a_location_outage_its_dependent_alerts_stay_quiet(nsx, monkeypatch):
    """The edge of site-a-wld01 is down AND site-a-wld01 is cut off from the Global Manager: the location alert
    fires, the edge alert does not."""
    def mutate(data, i):
        edge_down(data, i)
        for loc in data[GM]["/api/v1/sites/status"].get("remote_connections") or []:
            if loc.get("site_name") == "site-a-wld01":
                for s in loc["stubs"]:
                    s["connection_up"] = False
    fired, last = cycles(nsx, Clock(monkeypatch), 2, mutate)
    assert "loc_gm_down" in fired
    assert "edge_status" not in fired


@pytest.mark.parametrize("alert_id", sorted(a for a, x in D.alerts.items()
                                            if all(D.symptoms[r]["cond"]["type"] == "dtmetric" for s in x["sets"]
                                                   for r, neg in s if not neg)))
def test_a_dynamic_threshold_alert_has_its_metric(nsx, monkeypatch, alert_id):
    """VCF Operations learns the usual range of these metrics; offline, the metric must at least be sent."""
    _, last = cycles(nsx, Clock(monkeypatch), 2)
    kind = D.alerts[alert_id]["kind"]
    assert any(D.alert_on(alert_id, o) == "dt" for o in last[kind].values())


def test_every_alert_definition_has_a_scenario():
    dynamic = {a for a, x in D.alerts.items()
               if all(D.symptoms[r]["cond"]["type"] == "dtmetric" for s in x["sets"] for r, neg in s if not neg)}
    covered_elsewhere = BASELINE | {
        # fired by the scenarios of test_adapter.py (federation, Global Manager and location states)
        "fed_no_active", "fed_split_brain", "fed_gm_unreach", "fed_no_standby", "fed_budget", "fed_gaps", "fed_load",
        "fed_degraded", "fed_foreign", "gm_mode_none", "gm_version", "loc_newer", "gm_standby_sync", "loc_gm_down",
        "loc_collector", "loc_rtt", "loc_version", "loc_cluster", "tun_flap", "bgp_flap", "sr_down", "t0_no_active",
        "t0_failover", "loc_clock", "gm_clock", "loc_override", "loc_password", "pol_review", "pol_any"}
    missing = set(D.alerts) - set(SCENARIOS) - dynamic - covered_elsewhere
    assert not missing, f"alert definitions without a scenario: {sorted(missing)}"
