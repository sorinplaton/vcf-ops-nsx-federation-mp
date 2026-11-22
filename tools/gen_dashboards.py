#!/usr/bin/env python3
"""Generate the dashboards of the NSX Federation pack.

Writes pack/content/dashboards/<folder>/{dashboard.json,resources/resources.properties} (one folder per
dashboard) and, with --zip <file>, a content-import zip to test the dashboards without a pack install.
"""
import io
import typing
import json
import os
import shutil
import sys
import uuid
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AK = "NSXFederation"
OUT = os.path.join(ROOT, "pack/content/dashboards")
OWNER = "dc8f7126-4c23-4621-9011-e5390abe7398"   # owner id used inside an import zip (admin of the lab instance)
NS = uuid.UUID("6f1d2c0e-0000-4000-8000-4e5358464544")
SALT = os.environ.get("DASH_SALT", "")
uid = lambda *p: str(uuid.uuid5(NS, SALT + "/".join(p)))

# Labels of kinds, metrics and properties come from the adapter itself: run only its model block
# (the adapter module cannot be imported outside the adapter container).
_src = open(os.path.join(ROOT, "pack/app/adapter.py")).read()
_ns = {k: getattr(typing, k) for k in ("Dict", "Tuple", "List", "Optional", "Any")}  # the units block before the model is not executed
exec(_src[_src.index("RATES = {"):_src.index("def get_adapter_definition")], _ns)  # rates, model, metric groups
fk = _ns["fk"]
MODEL = _ns["MODEL"]

KINDS = list(MODEL.keys())
KID = {k: f"resourceKind:id:{i}_::_" for i, k in enumerate(KINDS)}
AID = "adapterKind:id:0_::_"
LABEL = {k: {**dict(MODEL[k][1]), **dict(MODEL[k][2])} for k in KINDS}
# short labels for scoreboard boxes (the full metric label does not fit)
SHORT = {"global_managers_active": "Active GMs", "global_managers_unreachable": "GMs unreachable", "locations_total": "Locations",
         "locations_gm_connected": "Locations connected", "locations_collected": "Locations collected",
         "stretched_objects_total": "Stretched objects", "stretched_objects_fully_realized": "Fully realized",
         "collection_duration_s": "Collection (s)", "api_calls": "API calls", "api_errors": "API errors", "data_gaps": "Calls unanswered",
         "managers_over_budget": "Over call budget", "standby_in_sync": "Standby in sync", "standby_out_of_sync_minutes": "Out of sync (min)",
         "replication_flows_unhealthy": "Flows in error", "gm_last_seen_minutes": "Seen by GM (min ago)", "minutes_since_collection": "Collected (min ago)", "locations_healthy_pct": "Locations healthy (%)", "rtep_tunnels_bgp_down": "RTEP BGP down",
         "rtep_dropped_per_min": "RTEP drops/min", "nodes_online": "Nodes online", "nodes_offline": "Nodes offline",
         "service_groups_degraded": "Services degraded", "backup_age_hours": "Backup age (h)",
         "cert_min_days_to_expiry": "Cert expiry (days)", "alarms_open_critical": "Critical alarms",
         "alarms_open_high": "High alarms", "gm_connection_up": "GM connection", "collector_reachable": "Collector reach",
         "intersite_bgp_established": "Inter-site up", "intersite_bgp_down": "Inter-site down",
         "full_sync_failed": "Full sync failed", "connection_up": "Channel up", "members": "Edge nodes",
         "rtep_tunnels": "RTEP tunnels", "bgp_established": "BGP established", "rx_packets": "Rx packets",
         "tx_packets": "Tx packets", "rx_dropped": "Rx dropped", "tx_dropped": "Tx dropped", "rx_blocked": "Rx blocked",
         "tx_blocked": "Tx blocked", "established": "Established", "prefixes_in": "Prefixes in", "prefixes_out": "Prefixes out",
         "seconds_established": "Uptime (s)", "connection_drops": "Drops", "span_locations": "Span",
         "realized_locations": "Realized", "unrealized_locations": "Not realized", "error_locations": "In error",
         "rx_bytes_per_s": "Rx bytes/s", "tx_bytes_per_s": "Tx bytes/s", "rx_packets_per_min": "Rx packets/min",
         "tx_packets_per_min": "Tx packets/min", "rx_dropped_per_min": "Rx drops/min", "tx_dropped_per_min": "Tx drops/min",
         "rx_blocked_per_min": "Rx blocked/min", "tx_blocked_per_min": "Tx blocked/min", "hit_count_per_min": "Rule hits/min",
         "rx_drop_pct": "Rx dropped (%)", "tx_drop_pct": "Tx dropped (%)", "connection_drops_per_hour": "Drops/hour",
         "gm_rtt_ms": "RTT to GM (ms)", "gm_rtt_pct_of_max": "RTT (% of max)", "sync_queue_used_pct": "Sync queue used (%)",
         "rules_total": "Rules", "rules_disabled": "Rules disabled", "hit_count": "Rule hits", "realized": "Realized",
         "switchover_ready": "Ready to take over", "healthy": "Healthy", "locations_healthy": "Locations healthy",
         "standby_full_sync_age_hours": "Last full sync (h)", "standby_sync_remaining": "Entries to send",
         "service_groups_degraded": "Services degraded", "reachable": "Reachable"}
IS_PROP = {k: {p for p, _ in MODEL[k][2]} for k in KINDS}


def tag_filter(kinds=None):
    path = [f"/source/kind/kind:{KID[k]}" for k in kinds] if kinds else [f"/source/adapterKind/adapterKind:{AID}"]
    return {"path": path, "value": {"bus": [], "adapterKind": [] if kinds else [AID], "kind": [KID[k] for k in kinds or []],
                                    "exclaim": False, "healthRange": [], "maintenanceSchedule": [], "adapterInstance": [],
                                    "collector": [], "tier": [], "state": [], "tag": [], "day": [], "status": []}}


def kind_metric(kind, key, n, bounds=None, label=None):
    """bounds = (yellow, orange, red) or None."""
    m = {"metricKey": key if key in IS_PROP[kind] else fk(kind, key), "metricName": LABEL[kind][key], "resourceKindId": KID[kind], "resourceKindName": MODEL[kind][0],
         "isStringMetric": key in IS_PROP[kind], "label": label or LABEL[kind][key], "unit": "", "metricUnitId": None, "link": "",
         "maxValue": "", "yellowBound": None, "orangeBound": None, "redBound": None, "colorMethod": 2, "id": f"extModel{n}-1"}
    if bounds:
        m.update(yellowBound=bounds[0], orangeBound=bounds[1], redBound=bounds[2], colorMethod=0)
    return m


class Dash:
    def __init__(self, name, description):
        self.name, self.description = name, description
        self.id = uid("dash", name)
        self.widgets, self.interactions, self.n = [], [], 1000

    def _add(self, wtype, title, x, y, w, h, config, states=None):
        wid = uid(self.name, title)
        config = dict(config, title=title, refreshInterval=300, refreshContent={"refreshContent": True})
        self.widgets.append({"tabId": self.id, "collapsed": False, "id": wid, "gridsterCoords": {"w": w, "x": x, "h": h, "y": y},
                             "state": "", "type": wtype, "title": title, "config": config, "height": 0, "states": states or []})
        return wid

    def link(self, provider, *receivers, kind="resourceId"):
        for r in receivers:
            self.interactions.append({"widgetIdProvider": provider, "type": kind, "widgetIdReceiver": r})

    def object_list(self, title, pos, kinds, columns, select_first=True):
        cols = [{"boxLabel": lab or LABEL[k][key], "metricName": LABEL[k][key], "metricKey": key if key in IS_PROP[k] else fk(k, key), "resourceKindId": KID[k]}
                for k, key, lab in columns]
        return self._add("ResourceList", title, *pos, {
            "resource": [], "relationshipMode": {"relationshipMode": -1}, "additionalColumns": cols, "mode": "all",
            "filterMode": "tagPicker", "tagFilter": tag_filter(kinds), "depth": 1,
            "customFilter": {"filter": [], "excludedResources": None, "includedResources": None},
            "selectFirstRow": {"selectFirstRow": select_first}, "selfProvider": {"selfProvider": True}})

    def scoreboard(self, title, pos, metrics, columns=4):
        rk = []
        for kind, key, *rest in metrics:
            self.n += 1
            rk.append(kind_metric(kind, key, self.n, rest[0] if rest else None, SHORT.get(key)))
        return self._add("Scoreboard", title, *pos, {
            "visualTheme": 5, "showMetricName": {"showMetricName": True}, "valueSize": 24, "boxHeight": None,
            "showResourceName": {"showResourceName": False}, "roundDecimals": 1, "showMetricUnit": {"showMetricUnit": False},
            "mode": {"layoutMode": "fixedView"}, "labelSize": 12, "boxColumns": columns, "periodLength": "last24Hour",
            "metric": {"mode": "resourceKind", "resourceMetrics": [], "resourceKindMetrics": rk, "subMode": "resourceKindAll"},
            "showSparkline": {"showSparkline": True}, "selfProvider": {"selfProvider": False}, "resource": [],
            "relationshipMode": {"relationshipMode": 0}, "depth": 1,
            "customFilter": {"filter": [], "excludedResources": None, "includedResources": None}})

    def property_list(self, title, pos, props):
        rk = []
        for kind, key in props:
            self.n += 1
            rk.append(kind_metric(kind, key, self.n, label=""))
        return self._add("PropertyList", title, *pos, {
            "visualTheme": 2, "resource": [], "relationshipMode": {"relationshipMode": 0}, "depth": 1,
            "showMetricFullName": {"metricFullName": False}, "selfProvider": {"selfProvider": False},
            "metric": {"mode": "resourceKind", "resourceMetrics": [], "resourceKindMetrics": rk}})

    def alert_list(self, title, pos):
        return self._add("AlertList", title, *pos, {
            "alertImpact": [], "mode": "tagFilter", "tagFilter": tag_filter(), "alertAction": [], "criticalityLevel": [],
            "state": [], "type": [], "status": [0]})

    def relationship(self, title, pos):
        return self._add("ResourceRelationshipAdvanced", title, *pos, {
            "filterMode": "tagPicker", "tagFilter": None, "resourceId": None, "depth": "1,2", "traversalSpecId": "",
            "customFilter": {"filter": [], "excludedResources": None, "includedResources": None}, "resourceName": None,
            "selfProvider": {"selfProvider": False}})

    def chart(self, title, pos):
        return self._add("MetricChart", title, *pos, {})

    def json(self):
        return {"shared": True, "hidden": False, "creationTime": 1795000000000, "autoswitchEnabled": False, "importAttempts": 0,
                "columnProportion": "1", "importComplete": True, "description": self.description, "widgets": self.widgets,
                "states": [], "editAllowed": True, "homeTab": False, "rank": 0, "disabled": False, "id": self.id, "locked": False,
                "dashboardNavigations": {}, "widgetInteractions": self.interactions, "namePath": "NSX Federation" + SALT,
                "lastUpdateUserId": OWNER, "columnCount": 1, "userId": OWNER, "name": self.name, "gridsterMaxColumns": 12,
                "lastUpdateTime": 1795000000000}


def overview():
    d = Dash("NSX Federation" + SALT + "/Federation Overview",
             "Health of the federation, its Global Managers and its locations. Select an object to see its key values, "
             "properties and related objects. Click a value to chart it.")
    alerts = d.alert_list("Open alerts on NSX Federation objects", (1, 1, 12, 6))
    objs = d.object_list("Federation, Global Managers and locations", (1, 7, 5, 7), ["federation", "global_manager", "location"], [
        ("location", "gm_connection_up", "GM connection"), ("location", "intersite_bgp_down", "Inter-site BGP down"),
        ("global_manager", "nodes_offline", "Nodes offline"), ("federation", "global_managers_active", "Active GMs")])
    up1 = (0.5, 0.5, 0.5)       # value 1 is good, 0 is red
    zero = (0.5, 0.5, 0.5)      # value 0 is good, anything above is red (ordering reversed below)
    sb = d.scoreboard("Selected object: key values", (6, 7, 7, 7), [
        ("federation", "global_managers_active"), ("federation", "global_managers_unreachable", (0.1, 0.5, 0.9)),
        ("federation", "locations_total"), ("federation", "locations_gm_connected"), ("federation", "locations_collected"),
        ("federation", "stretched_objects_total"), ("federation", "stretched_objects_fully_realized"),
        ("federation", "collection_duration_s", (60, 150, 240)), ("federation", "api_calls"),
        ("federation", "api_errors", (1, 5, 20)), ("federation", "data_gaps", (0.1, 5, 20)), ("federation", "managers_over_budget", (0.1, 0.5, 0.9)),
        ("federation", "locations_healthy_pct", (99, 75, 50)), ("federation", "rtep_tunnels_bgp_down", (0.1, 0.5, 0.9)),
        ("federation", "rtep_dropped_per_min", (60, 300, 1000)), ("federation", "cert_min_days_to_expiry", (60, 30, 7)),
        ("federation", "alarms_open_critical", (0.1, 0.5, 0.9)),
        ("global_manager", "standby_in_sync", (0.9, 0.6, 0.5)), ("global_manager", "standby_out_of_sync_minutes", (1, 15, 60)), ("global_manager", "nodes_online"), ("global_manager", "nodes_offline", (0.1, 0.5, 0.9)),
        ("global_manager", "service_groups_degraded", (0.1, 0.5, 0.9)), ("global_manager", "backup_age_hours", (24, 48, 72)),
        ("global_manager", "cert_min_days_to_expiry", (60, 30, 7)), ("global_manager", "alarms_open_critical", (0.1, 0.5, 0.9)),
        ("global_manager", "alarms_open_high", (0.9, 4, 9)),
        ("location", "gm_connection_up", (0.9, 0.6, 0.5)), ("location", "collector_reachable", (0.9, 0.6, 0.5)),
        ("location", "intersite_bgp_established"), ("location", "intersite_bgp_down", (0.1, 0.5, 0.9)),
        ("location", "full_sync_failed", (0.1, 0.5, 0.9)), ("location", "replication_flows_unhealthy", (0.1, 0.5, 0.9)),
        ("location", "gm_rtt_ms", (50, 100, 150)), ("location", "gm_rtt_pct_of_max", (50, 80, 100)),
        ("location", "sync_queue_used_pct", (10, 50, 80)),
        ("location", "gm_last_seen_minutes", (5, 15, 60)), ("location", "minutes_since_collection", (6, 15, 60)), ("location", "nodes_offline", (0.1, 0.5, 0.9)),
        ("location", "backup_age_hours", (24, 48, 72)), ("location", "cert_min_days_to_expiry", (60, 30, 7)),
        ("location", "alarms_open_critical", (0.1, 0.5, 0.9)), ("location", "alarms_open_high", (0.9, 4, 9)),
        ("location", "clock_skew_to_gm_s", (1, 5, 60)), ("location", "capacity_max_pct", (60, 70, 100)),
        ("location", "overridden_objects", (0.1, 0.5, 0.9)),
    ], columns=3)
    rel = d.relationship("Selected object: related objects", (1, 14, 7, 9))
    pl = d.property_list("Selected object: properties", (8, 14, 5, 9), [
        ("federation", "active_global_manager"), ("federation", "standby_configured"), ("federation", "collection_mode"),
        ("federation", "federation_id"), ("federation", "last_change"),
        ("global_manager", "mode"), ("global_manager", "version"), ("global_manager", "cluster_status"),
        ("global_manager", "backup_enabled"), ("global_manager", "backup_last_result"), ("global_manager", "standby_sync_status"),
        ("global_manager", "switchover_status"), ("global_manager", "cert_first_to_expire"),
        ("location", "version"), ("location", "version_matches_gm"), ("location", "local_manager_address"),
        ("location", "cluster_status"), ("location", "full_sync_stage"), ("location", "rtep_ips"),
        ("location", "backup_enabled"), ("location", "certificate_trust"), ("location", "collector_error"),
    ])
    ch = d.chart("Trend of the value clicked above", (1, 23, 12, 7))
    d.link(objs, sb, rel, pl)
    d.link(sb, ch, kind="metricId")
    del alerts, up1, zero
    return d


def connectivity():
    d = Dash("NSX Federation" + SALT + "/Inter-site Connectivity",
             "Sync channels between managers, inter-site BGP and capacity per edge node, Tier-0 service routers, RTEP "
             "tunnels and the BGP neighbors of stretched Tier-0 gateways. Select a row to see its values, click a value "
             "to chart it.")
    a = d.object_list("Sync channels, edge nodes and Tier-0 service routers", (1, 1, 6, 7),
                      ["sync_channel", "edge_cluster", "edge_node", "tier0_sr"], [
        ("sync_channel", "connection_up", "Channel up"), ("edge_node", "intersite_bgp_established", "BGP established"),
        ("edge_node", "intersite_bgp_down", "BGP down"), ("edge_cluster", "intersite_bgp_down", "BGP down (cluster)"),
        ("edge_node", "datapath_cpu_pct", "Datapath CPU (%)"), ("edge_node", "memory_used_pct", "Memory (%)"),
        ("tier0_sr", "active", "SR active"), ("tier0_sr", "routing_up", "SR routing up")])
    b = d.object_list("RTEP tunnels", (7, 1, 6, 7), ["rtep_tunnel"], [
        ("rtep_tunnel", "bgp_established", "BGP established"), ("rtep_tunnel", "rx_dropped_per_min", "Rx drops/min"),
        ("rtep_tunnel", "tx_dropped_per_min", "Tx drops/min"), ("rtep_tunnel", "rx_drop_pct", "Rx dropped (%)"),
        ("rtep_tunnel", "tx_drop_pct", "Tx dropped (%)")], select_first=False)
    c = d.object_list("Tier-0 BGP neighbors", (1, 8, 6, 8), ["bgp_neighbor"], [
        ("bgp_neighbor", "established", "Established"), ("bgp_neighbor", "prefixes_in", "Prefixes in"),
        ("bgp_neighbor", "prefixes_out", "Prefixes out"), ("bgp_neighbor", "connection_drops", "Drops"),
        ("bgp_neighbor", "connection_drops_per_hour", "Drops/hour")], select_first=False)
    sb = d.scoreboard("Selected object: key values", (7, 8, 6, 8), [
        ("sync_channel", "connection_up", (0.9, 0.6, 0.5)),
        ("edge_cluster", "members"), ("edge_cluster", "intersite_bgp_established"), ("edge_cluster", "intersite_bgp_down", (0.1, 0.5, 0.9)),
        ("edge_node", "rtep_tunnels"), ("edge_node", "intersite_bgp_established"), ("edge_node", "intersite_bgp_down", (0.1, 0.5, 0.9)),
        ("edge_node", "datapath_cpu_pct", (60, 75, 85)), ("edge_node", "datapath_cpu_peak_pct", (60, 75, 85)),
        ("edge_node", "services_cpu_pct", (60, 75, 85)), ("edge_node", "memory_used_pct", (70, 80, 90)),
        ("edge_node", "datapath_pool_peak_pct", (60, 75, 85)), ("edge_node", "disk_fullest_pct", (70, 80, 90)),
        ("tier0_sr", "active"), ("tier0_sr", "routing_up", (0.9, 0.6, 0.5)), ("tier0_sr", "bgp_neighbors_up"),
        ("tier0_sr", "bgp_neighbors_down", (0.1, 0.5, 0.9)),
        ("rtep_tunnel", "bgp_established", (0.9, 0.6, 0.5)), ("rtep_tunnel", "rx_bytes_per_s"), ("rtep_tunnel", "tx_bytes_per_s"),
        ("rtep_tunnel", "rx_packets_per_min"), ("rtep_tunnel", "tx_packets_per_min"),
        ("rtep_tunnel", "rx_dropped_per_min", (1, 10, 60)), ("rtep_tunnel", "tx_dropped_per_min", (1, 10, 60)),
        ("rtep_tunnel", "rx_drop_pct", (0.5, 1, 5)), ("rtep_tunnel", "tx_drop_pct", (0.5, 1, 5)),
        ("rtep_tunnel", "rx_blocked_per_min", (1, 10, 60)), ("rtep_tunnel", "tx_blocked_per_min", (1, 10, 60)),
        ("rtep_tunnel", "connection_drops_per_hour", (1, 12, 36)),
        ("bgp_neighbor", "established", (0.9, 0.6, 0.5)), ("bgp_neighbor", "prefixes_in"), ("bgp_neighbor", "prefixes_out"),
        ("bgp_neighbor", "seconds_established"), ("bgp_neighbor", "connection_drops"),
        ("bgp_neighbor", "connection_drops_per_hour", (1, 12, 36)),
    ], columns=3)
    ch = d.chart("Trend of the value clicked above", (1, 16, 12, 7))
    d.link(a, sb)
    d.link(b, sb)
    d.link(c, sb)
    d.link(sb, ch, kind="metricId")
    return d


def stretched():
    d = Dash("NSX Federation" + SALT + "/Stretched Objects",
             "Stretched Tier-0 and Tier-1 gateways, segments and global security policies with their span, and the "
             "realization of each object at each location.")
    cols = []
    for k in ("tier0", "tier1", "segment", "global_policy"):
        cols += [(k, "span_locations", "Span"), (k, "realized_locations", "Realized"), (k, "error_locations", "In error")]
    a = d.object_list("Stretched gateways, segments and policies", (1, 1, 7, 8), ["tier0", "tier1", "segment", "global_policy"], cols)
    b = d.object_list("Realization per location", (8, 1, 5, 8), ["realization"], [("realization", "realized", "Realized")],
                      select_first=False)
    sbm, plm = [], []
    for k in ("tier0", "tier1", "segment", "global_policy"):
        sbm += [(k, "span_locations"), (k, "realized_locations"), (k, "unrealized_locations", (0.1, 0.5, 0.9)),
                (k, "error_locations", (0.1, 0.5, 0.9))]
        plm += [(k, "status"), (k, "path"), (k, "last_modified_user"), (k, "revision")]
    sbm += [("tier0", "sr_total"), ("tier0", "sr_active", (0.9, 0.6, 0.5)), ("tier0", "sr_down", (0.1, 0.5, 0.9)),
            ("tier0", "locations_without_active_sr", (0.1, 0.5, 0.9)), ("global_policy", "rules_total"), ("global_policy", "rules_disabled"), ("global_policy", "hit_count"), ("global_policy", "hit_count_per_min"),
            ("realization", "realized", (0.9, 0.6, 0.5))]
    plm += [("tier0", "ha_mode"), ("tier0", "primary_location"), ("tier0", "active_service_routers"), ("tier0", "no_active_sr_at"), ("tier1", "tier0"), ("tier1", "primary_location"),
            ("segment", "subnets"), ("segment", "gateway"), ("segment", "segment_type"),
            ("global_policy", "category"), ("global_policy", "scope"),
            ("realization", "status"), ("realization", "location"), ("realization", "object_type"), ("realization", "object_path")]
    sb = d.scoreboard("Selected object: key values", (1, 9, 7, 6), sbm, columns=3)
    pl = d.property_list("Selected object: properties", (8, 9, 5, 12), plm)
    rel = d.relationship("Selected object: related objects", (1, 15, 7, 6))
    d.link(a, sb, pl, rel)
    d.link(b, sb, pl)
    return d


def locations():
    d = Dash("NSX Federation" + SALT + "/Location Matrix",
             "One row per location with the checks that decide if it is healthy. Select a location to see its "
             "values and properties, click a value to chart it.")
    a = d.object_list("Locations", (1, 1, 12, 7), ["location"], [
        ("location", "healthy", "Healthy"), ("location", "gm_connection_up", "GM connection"),
        ("location", "collector_reachable", "Collector reach"), ("location", "nodes_offline", "Nodes offline"),
        ("location", "intersite_bgp_down", "Inter-site BGP down"), ("location", "full_sync_failed", "Full sync failed"),
        ("location", "replication_flows_unhealthy", "Flows in error"), ("location", "gm_rtt_pct_of_max", "RTT (% of max)"),
        ("location", "sync_queue_used_pct", "Sync queue (%)"), ("location", "backup_age_hours", "Backup age (h)"),
        ("location", "cert_min_days_to_expiry", "Cert expiry (days)"), ("location", "alarms_open_critical", "Critical alarms"),
        ("location", "version_matches_gm", "Same version as GM")])
    fl = d.object_list("Federation", (1, 8, 4, 5), ["federation"], [("federation", "locations_healthy_pct", "Healthy (%)")])
    f = d.scoreboard("Federation: key values", (5, 8, 8, 5), [
        ("federation", "locations_total"), ("federation", "locations_healthy"), ("federation", "locations_healthy_pct", (99, 75, 50)),
        ("federation", "locations_gm_connected"), ("federation", "rtep_tunnels_bgp_down", (0.1, 0.5, 0.9)),
        ("federation", "switchover_ready", (0.9, 0.6, 0.5))], columns=3)
    d.link(fl, f)
    sb = d.scoreboard("Selected location: key values", (1, 13, 7, 7), [
        ("location", "healthy", (0.9, 0.6, 0.5)), ("location", "gm_connection_up", (0.9, 0.6, 0.5)),
        ("location", "collector_reachable", (0.9, 0.6, 0.5)), ("location", "nodes_offline", (0.1, 0.5, 0.9)),
        ("location", "intersite_bgp_established"), ("location", "intersite_bgp_down", (0.1, 0.5, 0.9)),
        ("location", "full_sync_failed", (0.1, 0.5, 0.9)), ("location", "replication_flows_unhealthy", (0.1, 0.5, 0.9)),
        ("location", "gm_rtt_ms", (50, 100, 150)), ("location", "gm_rtt_pct_of_max", (50, 80, 100)),
        ("location", "sync_queue_used_pct", (10, 50, 80)), ("location", "gm_last_seen_minutes", (5, 15, 60)),
        ("location", "backup_age_hours", (24, 48, 72)), ("location", "cert_min_days_to_expiry", (60, 30, 7)),
        ("location", "alarms_open_critical", (0.1, 0.5, 0.9))], columns=3)
    pl = d.property_list("Selected location: properties", (8, 13, 5, 7), [
        ("location", "version"), ("location", "version_matches_gm"), ("location", "cluster_status"),
        ("location", "replication_state"), ("location", "full_sync_stage"), ("location", "upgrade_status"),
        ("location", "backup_last_result"), ("location", "collector_error")])
    ch = d.chart("Trend of the value clicked above", (1, 20, 12, 7))
    d.link(a, sb, pl)
    d.link(sb, ch, kind="metricId")
    return d


def switchover():
    d = Dash("NSX Federation" + SALT + "/Switchover Readiness",
             "Can a standby Global Manager take over now? Each standby lists what stands in the way: sync with the "
             "active, cluster health, version, certificates, an upgrade in progress. Select a Global Manager to see why.")
    a = d.object_list("Global Managers", (1, 1, 12, 5), ["global_manager"], [
        ("global_manager", "mode", "Mode"), ("global_manager", "switchover_ready", "Ready to take over"),
        ("global_manager", "switchover_readiness", "Readiness"), ("global_manager", "reachable", "Reachable"),
        ("global_manager", "standby_in_sync", "In sync"), ("global_manager", "standby_out_of_sync_minutes", "Out of sync (min)"),
        ("global_manager", "nodes_offline", "Nodes offline"), ("global_manager", "version", "Version"),
        ("global_manager", "backup_age_hours", "Backup age (h)")])
    fl = d.object_list("Federation", (1, 6, 4, 5), ["federation"], [("federation", "switchover_readiness", "Readiness")])
    f = d.scoreboard("Federation: key values", (5, 6, 8, 5), [
        ("federation", "switchover_ready", (0.9, 0.6, 0.5)), ("federation", "global_managers_reachable"),
        ("federation", "global_managers_active"), ("federation", "global_managers_unreachable", (0.1, 0.5, 0.9))], columns=2)
    d.link(fl, f)
    sb = d.scoreboard("Selected Global Manager: key values", (1, 11, 7, 7), [
        ("global_manager", "switchover_ready", (0.9, 0.6, 0.5)), ("global_manager", "reachable", (0.9, 0.6, 0.5)),
        ("global_manager", "standby_in_sync", (0.9, 0.6, 0.5)), ("global_manager", "standby_out_of_sync_minutes", (1, 15, 60)),
        ("global_manager", "standby_sync_remaining"), ("global_manager", "standby_full_sync_age_hours"),
        ("global_manager", "nodes_online"), ("global_manager", "nodes_offline", (0.1, 0.5, 0.9)),
        ("global_manager", "service_groups_degraded", (0.1, 0.5, 0.9)), ("global_manager", "backup_age_hours", (24, 48, 72)),
        ("global_manager", "cert_min_days_to_expiry", (60, 30, 7)), ("global_manager", "alarms_open_critical", (0.1, 0.5, 0.9))],
        columns=3)
    pl = d.property_list("Selected Global Manager: properties", (8, 11, 5, 7), [
        ("global_manager", "switchover_readiness"), ("global_manager", "mode"), ("global_manager", "version"),
        ("global_manager", "version_matches_active_gm"), ("global_manager", "cluster_status"),
        ("global_manager", "standby_sync_status"), ("global_manager", "switchover_status"),
        ("global_manager", "upgrade_status"), ("global_manager", "backup_last_result"), ("global_manager", "cert_first_to_expire")])
    ch = d.chart("Trend of the value clicked above", (1, 18, 12, 7))
    d.link(a, sb, pl)
    d.link(sb, ch, kind="metricId")
    return d


def bundle(dashes):
    """dashboard.json holding the given dashboards plus the lookup table of kinds."""
    return {"entries": {"resourceKind": [{"resourceKindKey": k, "internalId": KID[k], "adapterKindKey": AK} for k in KINDS],
                        "adapterKind": [{"internalId": AID, "adapterKindKey": AK}], "resource": []},
            "dashboards": [d.json() for d in dashes], "uuid": uid("bundle", *[d.name for d in dashes])}


def properties(dashes):
    esc = lambda s: s.replace("\\", "\\\\").replace(" ", "\\ ").replace(":", "\\:").replace("=", "\\=")
    lines = ["#Dashboard Localization"]
    for d in dashes:
        lines.append(f"{esc(d.name)}={d.name}")
        for w in d.widgets:
            lines.append(f"{esc(d.name)}.{esc(w['title'])}={w['title']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    dashes = [overview(), locations(), switchover(), connectivity(), stretched()]
    for d in dashes:
        folder = os.path.join(OUT, d.name.split("/")[-1].replace(" ", "_"))
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(os.path.join(folder, "resources"))
        json.dump(bundle([d]), open(os.path.join(folder, os.path.basename(folder) + ".json"), "w"), indent=1)
        open(os.path.join(folder, "resources", "resources.properties"), "w").write(properties([d]))
        print("wrote", folder, len(d.widgets), "widgets")
    if "--zip" in sys.argv:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("dashboard/dashboard.json", json.dumps(bundle(dashes)))
            z.writestr("dashboard/resources/resources.properties", properties(dashes))
        with zipfile.ZipFile(sys.argv[sys.argv.index("--zip") + 1], "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"dashboards/{OWNER}", inner.getvalue())
            z.writestr("configuration.json", json.dumps({"dashboardsByOwner": [{"owner": OWNER, "count": len(dashes)}],
                                                         "type": "CUSTOM", "dashboards": len(dashes)}))
            z.writestr("usermappings.json", open(sys.argv[sys.argv.index("--usermap") + 1]).read())
            z.writestr("6844548499441080431L.v1", open(sys.argv[sys.argv.index("--marker") + 1]).read())
