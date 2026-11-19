#!/usr/bin/env python3
"""Generate the alert content of the NSX Federation pack.

Writes pack/content/alertdefs/nsxfederation_alerts.xml (symptoms, alerts, recommendations) and,
with --zip <file>, a content-import zip for testing the same definitions without a pack install.
"""
import os
import json
import typing
import sys
import zipfile
from xml.sax.saxutils import escape, quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# metric keys are stored as "<group>|<metric>" since 1.4.0: take the grouping from the adapter
_src = open(os.path.join(ROOT, "pack/app/adapter.py")).read()
_ns = {k: getattr(typing, k) for k in ("Dict", "Tuple", "List", "Optional", "Any")}
exec(_src[_src.index("RATES = {"):_src.index("def get_adapter_definition")], _ns)
fk = _ns["fk"]

AK = "NSXFederation"
NET, APP = 19, 15                      # alert type: network, application
AVAIL, PERF, COMPL, CONF = 18, 19, 21, 22  # alert subtype: availability, performance, compliance, configuration

# ---- symptoms: key -> (resource kind, name, severity, condition type, metric/property key, operator, value)
S = {
    # federation
    "fed_no_active":   ("federation", "No Global Manager is in the active role", "critical", "metric", "global_managers_active", "<", 1),
    "fed_split_brain": ("federation", "More than one Global Manager claims the active role", "critical", "metric", "global_managers_active", ">", 1),
    "fed_gm_unreach":  ("federation", "A configured Global Manager is not reachable by the collector", "immediate", "metric", "global_managers_unreachable", ">", 0),
    "fed_no_standby":  ("federation", "No standby Global Manager is configured", "warning", "property", "standby_configured", "=", "False"),
    "fed_budget":      ("federation", "A manager used its whole API call budget", "warning", "metric", "managers_over_budget", ">", 0),
    "fed_gaps":        ("federation", "NSX API calls of the collection got no answer", "warning", "metric", "data_gaps", ">", 0),
    "fed_throttled":   ("federation", "NSX throttles the API calls of the collection", "warning", "metric", "api_calls_throttled", ">", 0),
    "fed_slow":        ("federation", "The collection takes more than 4 minutes", "warning", "metric", "collection_duration_s", ">", 240),
    "fed_degraded":    ("federation", "The collection runs in degraded mode", "immediate", "property", "collection_mode", "contains", "degraded"),
    "fed_foreign":     ("federation", "A listed Global Manager belongs to another federation", "warning", "metric", "global_managers_foreign", ">", 0),
    "fed_split_nsx":   ("federation", "NSX reports a split-brain between the Global Managers", "critical", "metric", "split_brain_reported", ">", 0),
    # global manager
    "gm_mode_none":    ("global_manager", "Global Manager has no role (mode NONE)", "immediate", "property", "mode", "=", "NONE"),
    "gm_version":      ("global_manager", "Global Manager version differs from the active Global Manager", "warning", "property", "version_matches_active_gm", "=", "False"),
    "gm_upg_running":  ("global_manager", "An NSX upgrade is in progress on the Global Manager", "info", "property", "upgrade_status", "=", "IN_PROGRESS"),
    "gm_upg_paused":   ("global_manager", "An NSX upgrade is paused on the Global Manager", "info", "property", "upgrade_status", "=", "PAUSED"),
    "gm_upg_failed":   ("global_manager", "An NSX upgrade failed on the Global Manager", "immediate", "property", "upgrade_status", "=", "FAILED"),
    "loc_repl":        ("location", "A replication flow with the Global Manager is in an error state", "immediate", "metric", "replication_flows_unhealthy", ">", 0),
    "loc_newer":       ("location", "Location runs a newer NSX release than the Global Manager", "immediate", "property", "release_newer_than_gm", "=", "True"),
    "loc_upg_running": ("location", "An NSX upgrade is in progress on the Local Manager", "info", "property", "upgrade_status", "=", "IN_PROGRESS"),
    "loc_upg_paused":  ("location", "An NSX upgrade is paused on the Local Manager", "info", "property", "upgrade_status", "=", "PAUSED"),
    "loc_upg_failed":  ("location", "An NSX upgrade failed on the Local Manager", "immediate", "property", "upgrade_status", "=", "FAILED"),
    "chan_gm_outage":  ("sync_channel", "A Global Manager is not reachable or none is active", "info", "property", "gm_outage", "!=", "none"),
    "gm_cluster":      ("global_manager", "Global Manager cluster status is not STABLE", "critical", "property", "cluster_status", "!=", "STABLE"),
    "gm_node_offline": ("global_manager", "A Global Manager node is offline", "immediate", "metric", "nodes_offline", ">", 0),
    "gm_backup_off":   ("global_manager", "Backup is not configured on the Global Manager", "immediate", "property", "backup_enabled", "=", "False"),
    "gm_backup_old":   ("global_manager", "Last successful Global Manager backup is older than 24 hours", "immediate", "metric", "backup_age_hours", ">", 24),
    "gm_backup_fail":  ("global_manager", "Last Global Manager backup failed", "immediate", "property", "backup_last_result", "=", "FAILED"),
    "gm_backup_never": ("global_manager", "No backup of the Global Manager has run", "immediate", "property", "backup_last_result", "=", "NONE"),
    "gm_standby_sync": ("global_manager", "A standby Global Manager is not in sync", "immediate", "metric", "standby_in_sync", "<", 1),
    "gm_cert_30":      ("global_manager", "A Global Manager certificate expires in less than 30 days", "warning", "metric", "cert_min_days_to_expiry", "<", 30),
    "gm_cert_7":       ("global_manager", "A Global Manager certificate expires in less than 7 days", "critical", "metric", "cert_min_days_to_expiry", "<", 7),
    # location
    "loc_gm_down":     ("location", "Location is not connected to the active Global Manager", "critical", "metric", "gm_connection_up", "<", 1),
    "loc_collector":   ("location", "The collector cannot reach the Local Manager", "warning", "metric", "collector_reachable", "<", 1),
    "loc_cert_trust":  ("location", "The Local Manager certificate is not the trusted one", "immediate", "property", "certificate_trust", "contains", "NOT TRUSTED"),
    "loc_full_sync":   ("location", "A full sync did not complete", "immediate", "metric", "full_sync_failed", ">", 0),
    "loc_bgp_down":    ("location", "Inter-site BGP sessions are not established", "immediate", "metric", "intersite_bgp_down", ">", 0),
    "loc_bgp_none":    ("location", "No inter-site BGP session is established", "critical", "metric", "intersite_bgp_established", "<", 1),
    "loc_bgp_zero":    ("location", "Location has no inter-site BGP session configured", "info", "metric", "intersite_bgp_total", "<", 1),
    "loc_version":     ("location", "Location version differs from the Global Manager", "info", "property", "version_matches_gm", "=", "False"),
    "loc_backup_off":  ("location", "Backup is not configured on the Local Manager", "warning", "property", "backup_enabled", "=", "False"),
    "loc_backup_old":  ("location", "Last successful Local Manager backup is older than 24 hours", "warning", "metric", "backup_age_hours", ">", 24),
    "loc_backup_fail": ("location", "Last Local Manager backup failed", "warning", "property", "backup_last_result", "=", "FAILED"),
    "loc_backup_never": ("location", "No backup of the Local Manager has run", "warning", "property", "backup_last_result", "=", "NONE"),
    "loc_cert_30":     ("location", "A Local Manager certificate expires in less than 30 days", "warning", "metric", "cert_min_days_to_expiry", "<", 30),
    "loc_cert_7":      ("location", "A Local Manager certificate expires in less than 7 days", "critical", "metric", "cert_min_days_to_expiry", "<", 7),
    "loc_cluster":     ("location", "Local Manager cluster status is not STABLE", "immediate", "property", "cluster_status", "!=", "STABLE"),
    "loc_node_offline": ("location", "A Local Manager node is offline", "immediate", "metric", "nodes_offline", ">", 0),
    # sync channel, edge node, BGP neighbor, realization
    "chan_down":       ("sync_channel", "Sync channel is down", "immediate", "metric", "connection_up", "<", 1),
    "edge_bgp_none":   ("edge_node", "No inter-site BGP session of the edge node is established", "immediate", "metric", "intersite_bgp_established", "<", 1),
    "edge_bgp_zero":   ("edge_node", "Edge node has no inter-site BGP session configured", "info", "metric", "intersite_bgp_total", "<", 1),
    "ec_status":       ("edge_cluster", "Inter-site status of the edge cluster is not UP", "immediate", "property", "intersite_status", "!=", "UP"),
    "tun_bgp_down":    ("rtep_tunnel", "Inter-site BGP session of the RTEP tunnel is not established", "immediate", "metric", "bgp_established", "<", 1),
    "tun_rx_drops":    ("rtep_tunnel", "RTEP tunnel drops more than 60 received packets per minute", "warning", "metric", "rx_dropped_per_min", ">", 60),
    "tun_tx_drops":    ("rtep_tunnel", "RTEP tunnel drops more than 60 sent packets per minute", "warning", "metric", "tx_dropped_per_min", ">", 60),
    "t0_error":        ("tier0", "Stretched Tier-0 gateway is in error at a location", "immediate", "metric", "error_locations", ">", 0),
    "t1_error":        ("tier1", "Stretched Tier-1 gateway is in error at a location", "immediate", "metric", "error_locations", ">", 0),
    "seg_error":       ("segment", "Stretched segment is in error at a location", "immediate", "metric", "error_locations", ">", 0),
    "pol_error":       ("global_policy", "Global security policy is in error at a location", "immediate", "metric", "error_locations", ">", 0),
    "bgp_down":        ("bgp_neighbor", "BGP neighbor is not established", "immediate", "metric", "established", "<", 1),
    "bgp_internal":    ("bgp_neighbor", "BGP neighbor is a session between edge nodes", "info", "property", "session_type", "!=", "uplink"),
    "bgp_no_prefix":   ("bgp_neighbor", "No prefixes are received from the BGP neighbor", "warning", "metric", "prefixes_in", "<", 1),
    "real_missing":    ("realization", "Stretched object is not realized at this location", "warning", "metric", "realized", "<", 1),
    # dynamic threshold: VCF Operations learns the usual range of the metric
    "tun_rx_dt":       ("rtep_tunnel", "RTEP tunnel receive rate is above its usual range", "warning", "dt", "rx_bytes_per_s", "above", None),
    "tun_tx_dt":       ("rtep_tunnel", "RTEP tunnel send rate is above its usual range", "warning", "dt", "tx_bytes_per_s", "above", None),
}

# ---- NSX alarms of the Global Manager, which the adapter forwards as events "NSX alarm <event_type>: <summary>"
# (the format of the built-in NSX integration). event type -> (severity, alert name, description, recommendation key)
GM_ALARMS = {
    "gm_to_gm_split_brain": ("critical", "NSX alarm: Global Manager split-brain",
                             "NSX reports that more than one Global Manager node is active.", "split_brain"),
    "gm_to_gm_synchronization_error": ("immediate", "NSX alarm: active to standby Global Manager sync error",
                                       "The active Global Manager cannot synchronize with the standby for more than 5 minutes.", "gm_sync"),
    "gm_to_gm_synchronization_warning": ("warning", "NSX alarm: active to standby Global Manager sync warning",
                                         "The active Global Manager cannot synchronize with the standby.", "gm_sync"),
    "gm_to_gm_latency_warning": ("warning", "NSX alarm: latency between Global Managers is high",
                                 "Latency between the Global Managers has been above the NSX threshold for more than 2 minutes.", "latency"),
    "gm_to_lm_synchronization_error": ("immediate", "NSX alarm: Global Manager to Local Manager sync error",
                                       "Data synchronization between the Global Manager and a Local Manager failed for an extended period.", "lm_sync"),
    "gm_to_lm_synchronization_warning": ("warning", "NSX alarm: Global Manager to Local Manager sync warning",
                                         "Data synchronization between the Global Manager and a Local Manager failed.", "lm_sync"),
    "gm_to_lm_latency_warning": ("warning", "NSX alarm: latency between Global Manager and Local Manager is high",
                                 "Latency between the Global Manager and a location has been above the NSX threshold for more than 2 minutes.", "latency"),
    "lm_to_lm_synchronization_error": ("immediate", "NSX alarm: sync between locations failed for 15 minutes",
                                       "Synchronization between two locations failed for more than 15 minutes.", "lm_lm_sync"),
    "lm_to_lm_synchronization_warning": ("warning", "NSX alarm: sync between locations failed for 3 minutes",
                                         "Synchronization between two locations failed for more than 3 minutes.", "lm_lm_sync"),
    "queue_occupancy_threshold_exceeded": ("warning", "NSX alarm: federation sync queue above threshold",
                                           "A queue used to synchronize data between locations reached its size threshold.", "queue"),
    "lm_restore_while_config_import_in_progress": ("immediate", "NSX alarm: Local Manager restored during config import",
                                                   "A location was restored from backup while its configuration import to the Global Manager was in progress.", "onboarding"),
    "rtep_bgp_down": ("immediate", "NSX alarm: RTEP BGP neighbor down",
                      "NSX reports an inter-site (RTEP) BGP session as down.", "intersite"),
    "rtep_connectivity_lost": ("immediate", "NSX alarm: RTEP connectivity lost",
                               "An edge node lost RTEP connectivity with a remote location.", "intersite"),
}
# the Global Manager object gets every type; a location (alarms of its Local Manager) all but the GM-to-GM ones
ALARM_KINDS = {"alarm_": ("global_manager", "Global Manager"), "loc_alarm_": ("location", "Local Manager")}
alarm_kinds = lambda ev: ["alarm_"] + ([] if ev.startswith("gm_to_gm") else ["loc_alarm_"])
for ev, (sev, name, _d, _r) in GM_ALARMS.items():
    for p in alarm_kinds(ev):
        S[p + ev] = (ALARM_KINDS[p][0], f"{ALARM_KINDS[p][1]} raised NSX alarm {ev}", sev, "event", None, "regex",
                     f"NSX alarm {ev}:(?s).*")

# ---- alert storms: while a location is cut off from the Global Manager, the alerts of the objects that depend
# on it stay quiet behind the alert of the location. The adapter writes the names of such locations to the
# property "location_outage" of these objects ("none" otherwise); each alert below also requires that the
# outage symptom is NOT triggered.
OUTAGE_KINDS = {"global_manager": "Global Manager", "location": "Location", "sync_channel": "Sync channel", "edge_cluster": "Edge cluster",
                "edge_node": "Edge node", "rtep_tunnel": "RTEP tunnel", "bgp_neighbor": "Tier-0 BGP neighbor",
                "tier0": "Tier-0 gateway", "tier1": "Tier-1 gateway", "segment": "Segment",
                "global_policy": "Global security policy", "realization": "Stretched object at a location"}
for _k in OUTAGE_KINDS:
    S["outage_" + _k] = (_k, "A location of the federation is cut off from the Global Manager", "info", "property",
                         "location_outage", "!=", "none")
QUIET = {"loc_intersite", "chan_down", "edge_bgp_down", "ec_status", "tun_bgp_down", "tun_drops", "t0_error", "t1_error",
         "seg_error", "pol_error", "bgp_down", "real_missing"}

# ---- wait and cancel cycles: conditions that can flap need two cycles (10 minutes) to raise and to clear
WAIT = {k: (2, 2) for k in ("chan_down", "loc_bgp_down", "loc_bgp_none", "edge_bgp_none", "tun_bgp_down",
                            "tun_rx_drops", "tun_tx_drops", "bgp_down", "bgp_no_prefix")}
WAIT.update({"tun_rx_dt": (3, 3), "tun_tx_dt": (3, 3), "gm_standby_sync": (3, 2), "loc_repl": (2, 2),
             "fed_gaps": (2, 2), "fed_throttled": (3, 2), "fed_slow": (2, 2),
             # a full sync or a change in progress is not a fault: these two wait for a second cycle
             "loc_full_sync": (2, 1), "real_missing": (2, 1)})
# an outage symptom outlives the symptoms it keeps quiet, so that their alerts do not fire in the cycles in which
# the outage is over and they are not cancelled yet
WAIT.update({"outage_" + k: (1, 3) for k in OUTAGE_KINDS})
WAIT["chan_gm_outage"] = (1, 3)
assert all(WAIT[o][1] > max((c for _w, c in WAIT.values() if _w > 1 or c > 1), default=1) - 1 for o in WAIT if "outage" in o)

# ---- alerts that affect the Risk badge (a condition that will hurt later) instead of Health (broken now)
RISK = {"gm_version", "gm_upgrade", "loc_newer", "loc_upgrade", "fed_no_standby", "fed_budget", "fed_gaps", "fed_load", "gm_backup", "gm_cert", "loc_cert_trust", "loc_version", "loc_backup", "loc_cert"}

# ---- recommendations
R = {
    "active_gm": "Open the Global Manager UI (System, Location Manager) and check which Global Manager is active. If the active "
                 "Global Manager is lost, follow the NSX Federation disaster recovery procedure and make the standby active. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html",
    "split_brain": "Two Global Managers claim the active role. Do not change global configuration. Decide which Global Manager "
                   "holds the valid configuration and force the other one to standby, as described in the NSX Federation recovery documentation. See https://knowledge.broadcom.com/external/article/324244",
    "gm_reach": "Check that the Global Manager appliance is powered on and that the cloud proxy can reach it on TCP 443. If its "
                "certificate was replaced, update the trusted certificate of the adapter instance.",
    "standby": "Deploy a standby Global Manager in another site and add it under System, Location Manager, Add Standby Global "
               "Manager. Without a standby the global configuration cannot be changed if the active Global Manager is lost. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html",
    "gaps": "Some NSX API calls of the collection failed, so part of the data is unknown and the alerts that depend on it are "
            "not evaluated. Read the property 'NSX API calls without an answer' on the federation object: it lists the manager, "
            "the call and the HTTP status. HTTP 403 points to missing rights of the collection account, HTTP 5xx or a timeout to "
            "a manager under load or a service that is down.",
    "load": "The collection is close to the limits of the managers or of the collection interval. Lengthen the collection "
            "interval of the account, reduce the policies whose rules are collected, or lower 'Managers collected in parallel'. "
            "Check the load of the manager named in the NSX alarms.",
    "degraded": "No Global Manager answered, none is active, or the active one did not return its locations (the property "
                "'Collection mode' says which), so the collection uses the list of locations it cached earlier and collects the "
                "Local Managers only. Check the Global Manager addresses of the account and the alert on the Global Managers.",
    "foreign": "One account monitors one federation. Remove the address named in the property 'Global Managers not collected' "
               "from this account and create a second account for the other federation.",
    "gm_none": "A Global Manager that comes back after a failover has no role until it is added again as the standby. "
               "In the UI of the active Global Manager, open System, Location Manager and make it the standby. "
               "See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html",
    "gm_version": "Upgrade the Global Manager that is behind. NSX refuses a switchover while the active and the standby "
                  "Global Manager run different versions. See https://knowledge.broadcom.com/external/article/449461",
    "upgrade": "Finish the upgrade in the order NSX Federation requires: the standby Global Manager, then the active "
               "Global Manager, then the Local Managers. A Local Manager must not run a newer release than the Global "
               "Manager. Take a backup of every manager cluster first. A paused upgrade is resumed or cancelled in "
               "System, Upgrade. See https://knowledge.broadcom.com/external/article/435153",
    "budget": "The collection of this cycle is partial. Raise 'API call budget per manager and cycle' in the advanced settings "
              "of the adapter instance, or lengthen the collection interval.",
    "cluster": "Open System, Appliances on the manager and check the state of each node and service group. Restore or redeploy "
               "the failed node.",
    "backup": "Configure a backup target and a schedule under System, Backup and Restore on the manager, then run a backup and "
              "confirm that it succeeds. A manager can only be restored from its own backup.",
    "cert": "Replace the certificate before it expires (System, Certificates). An expired manager certificate breaks the "
            "connection between the Global Manager and the Local Managers. See https://knowledge.broadcom.com/external/article/429189",
    "loc_gm": "Check the network path between the active Global Manager and the Local Manager of this location (TCP 443 and "
              "1236 in both directions) and the state of the Local Manager cluster. The location keeps forwarding traffic with its last configuration.",
    "collector": "Check that the cloud proxy can reach the Local Manager on TCP 443 and that the account of the adapter instance "
                 "is valid on this Local Manager. This is the monitoring path only.",
    "cert_trust": "The Local Manager presents a certificate that is not the one the Global Manager holds for it. Confirm the "
                  "certificate change is expected, then update the location in the Global Manager so the thumbprint matches.",
    "full_sync": "Open the location in System, Location Manager and read the sync status details. Start a full sync again from "
                 "the Global Manager once the cause (connectivity, version, disk space) is fixed.",
    "intersite": "Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing "
                 "between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions.",
    "version": "The location and the Global Manager run different NSX releases (the first three numbers of the version). "
               "That is expected only during an upgrade. Complete the upgrade so that all managers run the same release. "
               "A different patch level of the same release does not raise this alert.",
    "channel": "Check the connectivity between the two managers named on the channel (TCP 443 and 1236) and the state of both "
               "manager clusters.",
    "bgp": "Check the BGP neighbor on the Tier-0 gateway: interface state of the edge uplink, the peer router, AS numbers, "
           "passwords and timers.",
    "bgp_prefix": "The session is established but the peer advertises nothing. Check the routing policy and redistribution on "
                  "the peer router.",
    "tunnel_drops": "Check the RTEP network between the two edge nodes of this tunnel: MTU on the whole path (1700 recommended), "
                    "packet loss on the physical links and the load of the edge nodes.",
    "object_error": "Open the object in the Global Manager and read the error for each location of its span. Fix the cause "
                    "(a missing edge cluster, an address conflict, a location that is not reachable), then save the object again.",
    "gm_sync": "Check the connectivity between the active and the standby Global Manager (ping, then TCP 443 and 1236) and "
               "the state of both Global Manager clusters. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html",
    "lm_sync": "Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and "
               "that the async-replicator service runs on both sides (get service async_replicator). Its log is "
               "/var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html",
    "lm_lm_sync": "Run get site-replicator remote-sites on the Local Managers to see the connection state between the "
                  "locations, then check TCP 1236 between them. A location that is connected but not synchronized may still "
                  "be electing its leader; check again after a few seconds.",
    "latency": "Measure the round-trip time between the two managers named in the alarm. Federation supports up to 150 ms "
               "between locations; look for WAN congestion or a path change. See https://knowledge.broadcom.com/external/article/345822",
    "queue": "The sync queue grows when the remote location cannot keep up or cannot be reached. Check the connectivity to "
             "the remote location and /var/log/async-replicator/ar.log. See https://knowledge.broadcom.com/external/article/414606",
    "onboarding": "The location is in an inconsistent state. Delete the onboarding status of the location on the Global "
                  "Manager, as the NSX alarm describes, then start the configuration import again.",
    "traffic": "Inter-site traffic on this tunnel is above the range VCF Operations learned for it. Check whether a migration, "
               "a backup or a replication job explains it before looking for a loop or a misrouted flow.",
    "realization": "Open the object in the Global Manager and read the realization status for this location. Check the "
                   "location alarms and the connection between the Global Manager and the location, then save the object again if needed.",
}

# ---- alerts: (key, resource kind, name, description, type, subtype, symptom sets, recommendation)
# symptom sets: a list of sets joined by OR; each set is a list of symptom keys joined by AND
A = [
    ("fed_no_active", "federation", "NSX Federation has no active Global Manager",
     "No reachable Global Manager reports the active role. Global configuration cannot be changed.", NET, AVAIL, [["fed_no_active"]], "active_gm"),
    ("fed_split_brain", "federation", "NSX Federation split-brain: two active Global Managers",
     "More than one Global Manager claims the active role, or NSX reports a split-brain.", NET, AVAIL,
     [["fed_split_brain"], ["fed_split_nsx"]], "split_brain"),
    ("fed_gm_unreach", "federation", "Global Manager is not reachable by the collector",
     "At least one Global Manager address of the adapter instance does not answer or presents an untrusted certificate.", NET, AVAIL, [["fed_gm_unreach"]], "gm_reach"),
    ("fed_no_standby", "federation", "NSX Federation has no standby Global Manager",
     "The federation runs with a single Global Manager.", NET, COMPL, [["fed_no_standby"]], "standby"),
    ("fed_budget", "federation", "NSX Federation collection is partial",
     "At least one manager used its whole API call budget in the last collection cycle.", APP, PERF, [["fed_budget"]], "budget"),
    ("fed_gaps", "federation", "NSX Federation collection has data gaps",
     "NSX API calls of the collection got no answer in the last cycles. The data behind them is unknown.", APP, AVAIL, [["fed_gaps"]], "gaps"),
    ("fed_load", "federation", "NSX Federation collection is slow or throttled",
     "The collection takes most of its interval, or NSX throttles its API calls.", APP, PERF, [["fed_throttled"], ["fed_slow"]], "load"),
    ("fed_degraded", "federation", "NSX Federation collection runs without a Global Manager",
     "No Global Manager answered, none is active, or the active one did not return its locations. The Local Managers are collected "
     "from the last known list of locations.", APP, AVAIL, [["fed_degraded"]], "degraded"),
    ("fed_foreign", "federation", "NSX Federation account lists a Global Manager of another federation",
     "A Global Manager address of the account answers for another federation. It is not collected.", APP, CONF, [["fed_foreign"]], "foreign"),
    ("gm_mode_none", "global_manager", "Global Manager has no role in the federation",
     "The Global Manager reports the mode NONE: it is neither active nor standby.", NET, AVAIL, [["gm_mode_none"]], "gm_none"),
    ("gm_version", "global_manager", "Global Managers run different versions",
     "This Global Manager does not run the version of the active Global Manager. A switchover is refused in this state.",
     NET, COMPL, [["gm_version"]], "gm_version"),
    ("gm_upgrade", "global_manager", "NSX upgrade in progress, paused or failed on the Global Manager",
     "The upgrade coordinator of the Global Manager reports an upgrade that is not finished.", NET, CONF,
     [["gm_upg_running"], ["gm_upg_paused"], ["gm_upg_failed"]], "upgrade"),
    ("loc_repl", "location", "Replication between the location and the Global Manager is in error",
     "NSX reports a replication flow between the Global Manager and this location in an error state.", NET, AVAIL,
     [["loc_repl", "!outage_location"]], "lm_sync"),
    ("loc_newer", "location", "Location runs a newer NSX release than the Global Manager",
     "The Local Manager is ahead of the Global Manager. NSX Federation supports the opposite order only.", NET, COMPL,
     [["loc_newer"]], "upgrade"),
    ("loc_upgrade", "location", "NSX upgrade in progress, paused or failed on the Local Manager",
     "The upgrade coordinator of the Local Manager reports an upgrade that is not finished.", NET, CONF,
     [["loc_upg_running"], ["loc_upg_paused"], ["loc_upg_failed"]], "upgrade"),
    ("gm_cluster", "global_manager", "Global Manager cluster is degraded",
     "The Global Manager cluster is not stable or a node is offline.", NET, AVAIL, [["gm_cluster"], ["gm_node_offline"]], "cluster"),
    ("gm_standby_sync", "global_manager", "Standby Global Manager is not in sync",
     "The full sync to a standby Global Manager is not complete or reports a failure. A switchover would start from stale configuration.",
     NET, AVAIL, [["gm_standby_sync"]], "gm_sync"),
    ("gm_backup", "global_manager", "Global Manager backup is missing, old or failed",
     "Backup is not configured on the Global Manager, no backup has run, the last successful backup is older than 24 hours, or the last backup failed.",
     NET, COMPL, [["gm_backup_off"], ["gm_backup_old"], ["gm_backup_fail"], ["gm_backup_never", "!gm_backup_off"]], "backup"),
    ("gm_cert", "global_manager", "Global Manager certificate is about to expire",
     "A certificate of the Global Manager expires in less than 30 days (warning) or 7 days (critical).", NET, CONF, [["gm_cert_30"], ["gm_cert_7"]], "cert"),
    ("loc_gm_down", "location", "Location lost its connection to the active Global Manager",
     "The active Global Manager reports the connection to this location as down.", NET, AVAIL, [["loc_gm_down"]], "loc_gm"),
    ("loc_collector", "location", "Local Manager is not reachable by the collector",
     "The cloud proxy cannot collect from the Local Manager of this location. The data of the location is incomplete.", APP, AVAIL, [["loc_collector"]], "collector"),
    ("loc_cert_trust", "location", "Local Manager certificate is not trusted",
     "The Local Manager presents a certificate that differs from the one the Global Manager holds for it.", NET, CONF, [["loc_cert_trust"]], "cert_trust"),
    ("loc_full_sync", "location", "Full sync to the location did not complete",
     "At least one full sync between the Global Manager and this location stopped before completion.", NET, AVAIL, [["loc_full_sync"]], "full_sync"),
    ("loc_intersite", "location", "Inter-site BGP sessions of the location are down",
     "Inter-site (RTEP) BGP sessions of this location are not established. Critical when none is established.",
     NET, AVAIL, [["loc_bgp_down"], ["loc_bgp_none", "!loc_bgp_zero"]], "intersite"),
    ("loc_version", "location", "Location and Global Manager run different NSX releases",
     "The Local Manager and the Global Manager run different NSX releases. A different patch level is not reported.", NET, COMPL, [["loc_version"]], "version"),
    ("loc_backup", "location", "Local Manager backup is missing, old or failed",
     "Backup is not configured on the Local Manager, no backup has run, the last successful backup is older than 24 hours, or the last backup failed.",
     NET, COMPL, [["loc_backup_off"], ["loc_backup_old"], ["loc_backup_fail"], ["loc_backup_never", "!loc_backup_off"]], "backup"),
    ("loc_cert", "location", "Local Manager certificate is about to expire",
     "A certificate of the Local Manager expires in less than 30 days (warning) or 7 days (critical).", NET, CONF, [["loc_cert_30"], ["loc_cert_7"]], "cert"),
    ("loc_cluster", "location", "Local Manager cluster is degraded",
     "The Local Manager cluster of this location is not stable or a node is offline.", NET, AVAIL, [["loc_cluster"], ["loc_node_offline"]], "cluster"),
    ("chan_down", "sync_channel", "Federation sync channel is down",
     "The connection between two managers of the federation is down.", NET, AVAIL, [["chan_down"]], "channel"),
    ("edge_bgp_down", "edge_node", "Edge node has lost all inter-site BGP sessions",
     "No inter-site (RTEP) BGP session of this edge node is established. Single sessions raise an alert on their RTEP tunnel.",
     NET, AVAIL, [["edge_bgp_none", "!edge_bgp_zero"]], "intersite"),
    ("ec_status", "edge_cluster", "Inter-site status of the edge cluster is not UP",
     "NSX reports an inter-site status other than UP for this edge cluster.", NET, AVAIL, [["ec_status"]], "intersite"),
    ("tun_bgp_down", "rtep_tunnel", "RTEP tunnel inter-site BGP session is down",
     "The inter-site BGP session carried by this RTEP tunnel is not established.", NET, AVAIL, [["tun_bgp_down"]], "intersite"),
    ("tun_drops", "rtep_tunnel", "RTEP tunnel is dropping packets",
     "The tunnel drops more than 60 packets per minute in one direction.", NET, PERF, [["tun_rx_drops"], ["tun_tx_drops"]], "tunnel_drops"),
    ("t0_error", "tier0", "Stretched Tier-0 gateway is in error at a location",
     "At least one location of the span reports an error for this gateway.", NET, AVAIL, [["t0_error"]], "object_error"),
    ("t1_error", "tier1", "Stretched Tier-1 gateway is in error at a location",
     "At least one location of the span reports an error for this gateway.", NET, AVAIL, [["t1_error"]], "object_error"),
    ("seg_error", "segment", "Stretched segment is in error at a location",
     "At least one location of the span reports an error for this segment.", NET, AVAIL, [["seg_error"]], "object_error"),
    ("pol_error", "global_policy", "Global security policy is in error at a location",
     "At least one location of the span reports an error for this policy.", NET, AVAIL, [["pol_error"]], "object_error"),
    ("bgp_down", "bgp_neighbor", "Tier-0 BGP neighbor of a stretched gateway is down",
     "A BGP session of a stretched Tier-0 gateway is not established.", NET, AVAIL, [["bgp_down"]], "bgp"),
    ("bgp_no_prefix", "bgp_neighbor", "Tier-0 uplink BGP neighbor sends no prefixes",
     "The uplink BGP session is established but no prefix is received.", NET, AVAIL, [["bgp_no_prefix", "!bgp_down", "!bgp_internal"]], "bgp_prefix"),
    ("real_missing", "realization", "Stretched object is not realized at a location",
     "A stretched gateway, segment or security policy is not realized at one location of its span.", NET, AVAIL, [["real_missing"]], "realization"),
    ("tun_traffic", "rtep_tunnel", "RTEP tunnel traffic is above its usual range",
     "The receive or send rate of the tunnel is above the dynamic threshold VCF Operations learned for it.", NET, PERF,
     [["tun_rx_dt"], ["tun_tx_dt"]], "traffic"),
] + [(p + ev, ALARM_KINDS[p][0], name, desc, NET, AVAIL, [[p + ev]], rec)
     for ev, (_s, name, desc, rec) in GM_ALARMS.items() for p in alarm_kinds(ev)]

sid = lambda k: f"SymptomDefinition-{AK}-{k}"
aid = lambda k: f"AlertDefinition-{AK}-{k}"
# VCF Operations accepts only recommendation ids that start with Recommendation-df- (pack) or Recommendation-ud- (user)
rid = lambda k: f"Recommendation-df-{AK}-{k}"


def symptoms_xml():
    out = ["    <SymptomDefinitions>"]
    for k, (kind, name, sev, ctype, key, op, val) in S.items():
        vt = "numeric" if ctype == "metric" else "string"
        w, c = WAIT.get(k, (1, 1))
        out.append(f'        <SymptomDefinition adapterKind="{AK}" disableInBasePolicy="false" id="{sid(k)}" name={quoteattr(name)} '
                   f'resourceKind="{kind}" waitCycle="{w}" cancelCycle="{c}">')
        out.append(f'            <State severity="{sev}">')
        if ctype == "event":   # NOTIFICATION event (type 11, subtype 5) whose message matches a regular expression
            out.append(f'                <Condition eventMsg={quoteattr(val)} eventSubType="5" eventType="11" operator="{op}" type="msg_event"/>')
        elif ctype == "dt":
            out.append(f'                <Condition key="{fk(kind, key)}" operator="{op}" type="dtmetric"/>')
        else:
            key = fk(kind, key) if ctype == "metric" else key
            out.append(f'                <Condition key="{key}" operator={quoteattr(op)} thresholdType="static" type="{ctype}" '
                       f'value={quoteattr(str(val))} valueType="{vt}"/>')
        out.append("            </State>")
        out.append("        </SymptomDefinition>")
    out.append("    </SymptomDefinitions>")
    return out


def _quiet(k, kind, sets):
    alarm = k.startswith(("alarm_", "loc_alarm_")) and "gm_to_gm" not in k
    if k in QUIET or alarm:
        sets = [s + ["!outage_" + kind] for s in sets]
    if k == "chan_down":  # a Global Manager that is down takes its channels with it
        sets = [s + ["!chan_gm_outage"] for s in sets]
    return sets


A = [(k, kind, name, desc, typ, sub, _quiet(k, kind, sets), rec) for k, kind, name, desc, typ, sub, sets, rec in A]


def alerts_xml(with_recs=True):
    out = ["    <AlertDefinitions>"]
    for k, kind, name, desc, typ, sub, sets, rec in A:
        out.append(f'        <AlertDefinition adapterKind="{AK}" description={quoteattr(desc)} disableInBasePolicy="false" '
                   f'id="{aid(k)}" name={quoteattr(name)} resourceKind="{kind}" subType="{sub}" type="{typ}" waitCycle="1" cancelCycle="1">')
        out.append('            <State severity="automatic">')

        def one(s, pad):
            if len(s) == 1:
                return [f'{pad}<SymptomSet applyOn="self" operator="and" ref="{sid(s[0])}"/>']
            return [f'{pad}<SymptomSet applyOn="self" operator="and">'] + \
                   [f'{pad}    <Symptom ref="{sid(x.lstrip("!"))}"' + (' negateCondition="true"' if x.startswith("!") else "") + "/>"
                    for x in s] + [f"{pad}</SymptomSet>"]
        if len(sets) == 1:
            out += one(sets[0], " " * 16)
        else:
            out.append('                <SymptomSets operator="or">')
            for s in sets:
                out += one(s, " " * 20)
            out.append("                </SymptomSets>")
        out.append(f'                <Impact key="{"risk" if k in RISK else "health"}" type="badge"/>')
        if with_recs:
            out.append(f'                <Recommendations><Recommendation priority="1" ref="{rid(rec)}"/></Recommendations>')
        out.append("            </State>")
        out.append("        </AlertDefinition>")
    out.append("    </AlertDefinitions>")
    return out


def recs_xml():
    out = ["    <Recommendations>"]
    for k, text in R.items():
        out.append(f'        <Recommendation key="{rid(k)}"><Description>{escape(text)}</Description></Recommendation>')
    out.append("    </Recommendations>")
    return out


def doc(parts):
    return '<?xml version="1.0" encoding="UTF-8"?>\n<alertContent>\n' + "\n".join(sum(parts, [])) + "\n</alertContent>\n"


if __name__ == "__main__":
    for k, *_r, sets, rec in A:
        assert rec in R and all(x.lstrip("!") in S for s in sets for x in s), k
    path = os.path.join(ROOT, "pack/content/alertdefs/nsxfederation_alerts.xml")
    open(path, "w").write(doc([alerts_xml(), symptoms_xml(), recs_xml()]))
    print("wrote", path, len(A), "alerts", len(S), "symptoms", len(R), "recommendations")
    if "--zip" in sys.argv:
        z = zipfile.ZipFile(sys.argv[sys.argv.index("--zip") + 1], "w", zipfile.ZIP_DEFLATED)
        z.writestr("alertdefs.xml", doc([alerts_xml()]))
        z.writestr("symptomdefs.xml", doc([symptoms_xml()]))
        z.writestr("recommendationdefs.xml", doc([recs_xml()]))
        z.write(sys.argv[sys.argv.index("--marker") + 1], "6844548499441080431L.v1")
        z.writestr("configuration.json", json.dumps({"symptomDefs": len(S), "alertDefs": len(A), "recommendations": len(R), "type": "CUSTOM"}))
        z.close()
