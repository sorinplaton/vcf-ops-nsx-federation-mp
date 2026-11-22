#  NSX Federation management pack for VCF Operations (Integration SDK, Python).
#  Read-only. One adapter instance = one federation.
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set, Tuple

import aria.ops.adapter_logging as logging
from aria.ops.adapter_instance import AdapterInstance
from aria.ops.definition.adapter_definition import AdapterDefinition
from aria.ops.definition.units import Units
from aria.ops.event import Criticality
from aria.ops.object import Identifier, Object
from aria.ops.result import CollectResult, EndpointResult, TestResult
from aria.ops.timer import Timer
from constants import ADAPTER_KIND, ADAPTER_NAME
from nsxclient import BudgetExceeded, Cache, CertificateNotTrusted, NsxClient, pem_sha256, NsxError, split_pem

logger = logging.getLogger(__name__)

GI = "/global-manager/api/v1/global-infra"  # policy tree on a Global Manager
LGI = "/policy/api/v1/global-infra"  # the same tree as received by a Local Manager
# what the Location Manager page of the Global Manager reads: per flow, the latency NSX measured and the sync queues
FLOWS = "/global-manager/api/v1/observability/flow-details"
INVENTORY_TTL = 30 * 60  # seconds: inventory tier
GM_RESERVED_CALLS = 20  # calls of the budget kept for the health of the Global Manager itself
REALIZATION_EVERY_CYCLE = 200  # up to this many stretched objects, the realization of each is read in every cycle
REALIZATION_SWEEP_CYCLES = 6  # above it, every object is read again at least once in this many cycles
# fewest packets between two cycles for a drop percentage (dropped ones included): below it one lost packet would
# read as several percent.
# The drop alert also needs more than 10 drops a minute, which always clears this floor on a 5-minute interval.
LOSS_MIN_PACKETS = 50
SLOW_TTL = 60 * 60  # seconds: certificates and backups

# ---- units, by metric key (the same key has the same unit on every object type) ----------
_PKT, _PER_MIN = Units.MISC.PACKETS, Units.RATE.PER_MINUTE
UNITS = {
    "backup_age_hours": Units.TIME.HOURS, "cert_min_days_to_expiry": Units.TIME.DAYS,
    "collection_duration_s": Units.TIME.SECONDS, "seconds_established": Units.TIME.SECONDS,
    "api_calls": Units.MISC.CALLS, "api_calls_throttled": Units.MISC.CALLS, "api_errors": Units.MISC.ERRORS, "data_gaps": Units.MISC.CALLS,
    "standby_out_of_sync_minutes": Units.TIME.MINUTES, "standby_full_sync_age_hours": Units.TIME.HOURS,
    "full_sync_duration_s": Units.TIME.SECONDS, "full_sync_age_hours": Units.TIME.HOURS, "minutes_since_collection": Units.TIME.MINUTES, "gm_last_seen_minutes": Units.TIME.MINUTES,
    "gm_rtt_ms": Units.TIME.MILLISECONDS, "api_calls_retried": Units.MISC.CALLS,
    "rx_bytes": Units.DATA_SIZE.BYTE, "tx_bytes": Units.DATA_SIZE.BYTE, "byte_count": Units.DATA_SIZE.BYTE,
    "rx_bytes_per_s": Units.DATA_RATE.BYTE_PER_SECOND, "tx_bytes_per_s": Units.DATA_RATE.BYTE_PER_SECOND,
    "rx_packets": _PKT, "tx_packets": _PKT, "rx_dropped": _PKT, "tx_dropped": _PKT, "rx_blocked": _PKT, "tx_blocked": _PKT,
    "packet_count": _PKT,
    "rx_packets_per_min": _PER_MIN, "tx_packets_per_min": _PER_MIN, "rx_dropped_per_min": _PER_MIN,
    "tx_dropped_per_min": _PER_MIN, "rx_blocked_per_min": _PER_MIN, "tx_blocked_per_min": _PER_MIN,
    "hit_count_per_min": _PER_MIN, "rtep_dropped_per_min": _PER_MIN,
    "locations_healthy_pct": Units.RATIO.PERCENT,
    "rx_drop_pct": Units.RATIO.PERCENT, "tx_drop_pct": Units.RATIO.PERCENT, "gm_rtt_pct_of_max": Units.RATIO.PERCENT,
    "connection_drops_per_hour": Units.RATE.PER_HOUR, "sync_queue_used_pct": Units.RATIO.PERCENT,
}
# edge capacity: every value is a share in percent
for _k in ("datapath_cpu_pct", "datapath_cpu_peak_pct", "services_cpu_pct", "memory_used_pct", "datapath_memory_pct",
           "datapath_pool_peak_pct", "disk_fullest_pct"):
    UNITS[_k] = Units.RATIO.PERCENT
UNITS.update({"capacity_max_pct": Units.RATIO.PERCENT, "clock_skew_to_gm_s": Units.TIME.SECONDS,
              "clock_skew_to_active_s": Units.TIME.SECONDS, "clock_max_skew_s": Units.TIME.SECONDS,
              "availability_pct": Units.RATIO.PERCENT, "password_days_left": Units.TIME.DAYS,
              "password_min_days_left": Units.TIME.DAYS})
CLOCK_MAX_RTT_S = 3.0
RULE_FIELDS = ("id", "display_name", "path", "action", "disabled", "source_groups", "destination_groups", "rule_id",
               "sequence_number", "services", "service_entries", "scope", "profiles", "direction", "ip_protocol",
               "sources_excluded", "destinations_excluded")
PASSWORD_NEVER_EXPIRES = 9999  # days sent for an account whose password does not expire
UNUSED_RULE_DAYS = 30  # default of the setting: a rule with no new hit for this many days is reported as unused  # a clock reading whose call took longer than this is too imprecise to use
# counters that also get a rate: key -> (rate key, seconds the rate is expressed in)
RATES = {
    "rx_packets": ("rx_packets_per_min", 60), "tx_packets": ("tx_packets_per_min", 60),
    "rx_dropped": ("rx_dropped_per_min", 60), "tx_dropped": ("tx_dropped_per_min", 60),
    "rx_blocked": ("rx_blocked_per_min", 60), "tx_blocked": ("tx_blocked_per_min", 60),
    "rx_bytes": ("rx_bytes_per_s", 1), "tx_bytes": ("tx_bytes_per_s", 1),
    "hit_count": ("hit_count_per_min", 60),
    "connection_drops": ("connection_drops_per_hour", 3600),
}
# share of the packets dropped between two cycles: metric key -> (dropped counter, packet counter)
LOSS = {"rx_drop_pct": ("rx_dropped", "rx_packets"), "tx_drop_pct": ("tx_dropped", "tx_packets")}

# ---- object model ---------------------------------------------------------------------
# type key -> (label, [(metric key, label)], [(property key, label)])
MODEL: Dict[str, Tuple[str, List[Tuple[str, str]], List[Tuple[str, str]]]] = {
    "federation": ("NSX Federation", [
        ("global_managers_configured", "Global Managers configured"),
        ("global_managers_reachable", "Global Managers reachable"),
        ("global_managers_unreachable", "Global Managers not reachable"),
        ("global_managers_active", "Global Managers claiming active"),
        ("locations_total", "Locations"),
        ("locations_gm_connected", "Locations connected to the Global Manager"),
        ("locations_collected", "Locations reached by the collector"),
        ("stretched_objects_total", "Stretched objects"),
        ("stretched_objects_fully_realized", "Stretched objects realized at every location"),
        ("collection_duration_s", "Collection duration"),
        ("api_calls", "NSX API calls in this cycle"),
        ("api_calls_throttled", "NSX API calls throttled"),
        ("api_errors", "NSX API errors"),
        ("data_gaps", "NSX API calls without an answer (data unknown)"),
        ("api_calls_retried", "NSX API calls retried after a timeout"),
        ("global_managers_foreign", "Listed Global Managers of another federation"),
        ("locations_down", "Locations not connected to the Global Manager"),
        ("split_brain_reported", "Split-brain reported by NSX (1 = yes)"),
        ("realization_checked", "Stretched objects whose realization was read in this cycle"),
        ("stale_objects_deleted", "Objects deleted because they no longer exist in NSX"),
        ("managers_over_budget", "Managers that used their call budget"),
        ("locations_healthy", "Locations healthy"),
        ("locations_healthy_pct", "Share of locations healthy"),
        ("rtep_tunnels_total", "RTEP tunnels"),
        ("rtep_tunnels_bgp_down", "RTEP tunnels with inter-site BGP down"),
        ("rtep_dropped_per_min", "RTEP packets dropped per minute, all tunnels"),
        ("cert_min_days_to_expiry", "Days until the first manager certificate expires"),
        ("alarms_open_critical", "Open critical alarms, all managers"),
        ("switchover_ready", "A standby Global Manager is ready to take over (1 = yes)"),
        ("changes_detected", "Changes detected in this cycle"),
        ("clock_max_skew_s", "Largest clock difference with the active Global Manager"),
        ("capacity_max_pct", "NSX configuration limit most used, all locations"),
        ("overridden_objects", "Global objects overridden at a location, all locations"),
        ("availability_pct", "Availability: one active Global Manager answers (100 = yes)"),
        ("password_min_days_left", "Days until the first password of the collection accounts expires"),
        ("rules_to_review", "Global firewall rules to review (unused, shadowed or allowing any traffic)"),
    ], [
        ("federation_id", "Federation ID"),
        ("active_global_manager", "Active Global Manager"),
        ("standby_configured", "Standby Global Manager configured"),
        ("collection_mode", "Collection mode"),
        ("failed_calls", "NSX API calls without an answer"),
        ("global_manager_errors", "Global Managers not collected"),
        ("location_outage", "Locations in outage"),
        ("config_version", "Federation configuration version"),
        ("global_manager_addresses", "Global Manager addresses of the account"),
        ("vcf_operations_api", "VCF Operations API from the adapter"),
        ("stale_objects", "Objects deleted because they no longer exist in NSX"),
        ("switchover_readiness", "Switchover readiness"),
        ("last_change", "Last change"), ("recent_changes", "Recent changes"),
    ]),
    "global_manager": ("Global Manager", [
        ("reachable", "Reachable by the collector"),
        ("nodes_online", "Nodes online"),
        ("nodes_offline", "Nodes offline"),
        ("service_groups_degraded", "Service groups not stable"),
        ("alarms_open_critical", "Open alarms: critical"),
        ("alarms_open_high", "Open alarms: high"),
        ("alarms_open_other", "Open alarms: medium and low"),
        ("backup_age_hours", "Hours since last successful backup"),
        ("cert_min_days_to_expiry", "Days until the first certificate expires"),
        ("standby_sync_entries", "Standby sync entries reported"),
        ("standby_in_sync", "Standby sync healthy (1 = in sync)"),
        ("standby_sync_remaining", "Entries left to send to the standby"),
        ("standby_out_of_sync_minutes", "Standby out of sync for"),
        ("standby_full_sync_age_hours", "Time since the last full sync to the standby"),
        ("switchover_ready", "Ready to take over as the active Global Manager (1 = yes)"),
        ("clock_skew_to_active_s", "Clock difference with the active Global Manager"),
        ("availability_pct", "Availability: reachable with no node offline (100 = yes)"),
        ("password_days_left", "Days until the password of the collection account expires"),
    ], [
        ("mode", "Mode"), ("cluster_status", "Cluster status"), ("version", "Version"),
        ("address", "Address used by the collector"),
        ("backup_enabled", "Backup enabled"), ("backup_last_result", "Last backup result"),
        ("cert_first_to_expire", "First certificate to expire"),
        ("switchover_status", "Switchover status"),
        ("standby_sync_status", "Standby sync status"),
        ("standby_data_consistent", "Standby data consistent (as NSX reports it)"),
        ("version_matches_active_gm", "Same version as the active Global Manager"),
        ("upgrade_status", "Upgrade status"), ("upgrade_target_version", "Upgrade target version"),
        ("switchover_readiness", "Switchover readiness"),
        ("certificate_trust", "Certificate trust"),
        ("clock_offset_to_active", "Clock offset to the active Global Manager"),
        ("ntp_servers", "NTP servers"), ("ntp_service", "NTP service"),
        ("password_expiry", "Password expiry of the collection account"),
    ]),
    "manager_node": ("Manager Node", [("online", "Online")],
                     [("address", "Address"), ("uuid", "Node UUID")]),
    "location": ("Location", [
        ("healthy", "Location healthy (1 = yes)"),
        ("gm_connection_up", "Connected to the active Global Manager"),
        ("collector_reachable", "Reached by the collector"),
        ("nodes_online", "Local Manager nodes online"),
        ("nodes_offline", "Local Manager nodes offline"),
        ("alarms_open_critical", "Open alarms: critical"),
        ("alarms_open_high", "Open alarms: high"),
        ("alarms_open_other", "Open alarms: medium and low"),
        ("backup_age_hours", "Hours since last successful backup"),
        ("cert_min_days_to_expiry", "Days until the first certificate expires"),
        ("full_sync_failed", "Full syncs not completed"),
        ("intersite_bgp_total", "Inter-site BGP sessions"),
        ("intersite_bgp_established", "Inter-site BGP sessions established"),
        ("intersite_bgp_down", "Inter-site BGP sessions not established"),
        ("api_calls", "NSX API calls in this cycle"),
        ("full_sync_duration_s", "Duration of the last full sync"),
        ("full_sync_age_hours", "Time since the last full sync"),
        ("minutes_since_collection", "Time since the last successful collection"),
        ("replication_flows", "Replication flows with the Global Manager"),
        ("replication_flows_unhealthy", "Replication flows in an error state"),
        ("gm_rtt_ms", "Round-trip time to the Global Manager"),
        ("gm_rtt_pct_of_max", "Round-trip time to the Global Manager, % of the maximum RTT setting"),
        ("sync_queue_used_pct", "Sync queue used, fullest queue with the Global Manager"),
        ("gm_last_seen_minutes", "Time since the Global Manager last saw the location"),
        ("clock_skew_to_gm_s", "Clock difference with the active Global Manager"),
        ("capacity_max_pct", "NSX configuration limit most used"),
        ("capacity_items_warning", "NSX configuration limits above the warning threshold"),
        ("capacity_items_critical", "NSX configuration limits above the critical threshold"),
        ("overridden_objects", "Global objects overridden at this location"),
        ("availability_pct", "Availability: location healthy (100 = yes)"),
        ("password_days_left", "Days until the password of the collection account expires"),
    ], [
        ("site_id", "Site ID"), ("version", "Version"), ("version_matches_gm", "Same version as the Global Manager"),
        ("local_manager_address", "Local Manager address"), ("rtep_ips", "RTEP addresses"),
        ("maximum_rtt_ms", "Maximum RTT setting (ms)"), ("cluster_status", "Local Manager cluster status"),
        ("full_sync_stage", "Last full sync stage"), ("backup_enabled", "Backup enabled"),
        ("backup_last_result", "Last backup result"),
        ("cert_first_to_expire", "First certificate to expire"), ("certificate_trust", "Certificate trust"),
        ("collector_error", "Collector error"),
        ("release_newer_than_gm", "NSX release newer than the Global Manager"),
        ("upgrade_status", "Upgrade status"), ("upgrade_target_version", "Upgrade target version"),
        ("alarm_alerts_by", "Federation alarm alerts raised by"),
        ("replication_state", "Replication flows"), ("onboarding_status", "Onboarding status"),
        ("site_path", "Site path"), ("lm_thumbprint", "Local Manager certificate thumbprint (from the Global Manager)"),
        ("clock_offset_to_gm", "Clock offset to the active Global Manager"),
        ("ntp_servers", "NTP servers"), ("ntp_service", "NTP service"),
        ("capacity_fullest", "NSX configuration limit most used"),
        ("capacity_over_threshold", "NSX configuration limits above a threshold"),
        ("overridden_list", "Global objects overridden at this location"),
        ("capacity_alerts_by", "Configuration limit alerts raised by"),
        ("password_expiry", "Password expiry of the collection account"),
    ]),
    "sync_channel": ("Sync Channel", [("connection_up", "Connection up")],
                     [("source", "Source"), ("target", "Target"), ("target_address", "Target address")]),
    "edge_cluster": ("Federation Edge Cluster", [
        ("intersite_bgp_total", "Inter-site BGP sessions"),
        ("intersite_bgp_established", "Inter-site BGP sessions established"),
        ("intersite_bgp_down", "Inter-site BGP sessions not established"),
        ("members", "Edge nodes"),
    ], [("intersite_status", "Inter-site status"), ("rtep_ips", "RTEP addresses"),
        ("intersite_forwarding", "Inter-site forwarding enabled"), ("location", "Location")]),
    "edge_node": ("Federation Edge Node", [
        ("intersite_bgp_total", "Inter-site BGP sessions"),
        ("intersite_bgp_established", "Inter-site BGP sessions established"),
        ("intersite_bgp_down", "Inter-site BGP sessions not established"),
        ("rtep_tunnels", "RTEP tunnels"),
        ("datapath_cpu_pct", "Datapath CPU usage, average of the datapath cores"),
        ("datapath_cpu_peak_pct", "Datapath CPU usage, busiest datapath core"),
        ("services_cpu_pct", "Services CPU usage, average of the other cores"),
        ("memory_used_pct", "System memory used"),
        ("datapath_memory_pct", "Datapath memory used"),
        ("datapath_pool_peak_pct", "Datapath memory pool used, fullest pool"),
        ("disk_fullest_pct", "Disk used, fullest file system"),
    ], [("location", "Location"), ("edge_status", "Edge status"), ("maintenance_mode", "Maintenance mode"),
        ("software_version", "Software version"), ("datapath_pool_fullest", "Fullest datapath memory pool"),
        ("capacity_alerts_by", "Edge status and capacity alerts raised by")]),
    "tier0_sr": ("Tier-0 Service Router", [
        ("active", "HA state active (1 = yes)"), ("routing_up", "Routing up (1 = yes)"),
        ("bgp_neighbors_up", "BGP neighbors up"), ("bgp_neighbors_down", "BGP neighbors down"),
    ], [("ha_status", "HA state"), ("routing_status", "Routing status"), ("tier0", "Tier-0 gateway"),
        ("ha_mode", "HA mode of the Tier-0"), ("edge", "Edge node"), ("location", "Location"),
        ("edge_maintenance", "Edge maintenance mode"), ("last_ha_change", "Last HA state change")]),
    "rtep_tunnel": ("RTEP Tunnel", [
        ("rx_bytes", "Received bytes"), ("tx_bytes", "Sent bytes"),
        ("rx_packets", "Received packets"), ("tx_packets", "Sent packets"),
        ("rx_dropped", "Received packets dropped"), ("tx_dropped", "Sent packets dropped"),
        ("rx_blocked", "Received packets blocked"), ("tx_blocked", "Sent packets blocked"),
        ("rx_bytes_per_s", "Received bytes per second"), ("tx_bytes_per_s", "Sent bytes per second"),
        ("rx_packets_per_min", "Received packets per minute"), ("tx_packets_per_min", "Sent packets per minute"),
        ("rx_dropped_per_min", "Received packets dropped per minute"), ("tx_dropped_per_min", "Sent packets dropped per minute"),
        ("rx_blocked_per_min", "Received packets blocked per minute"), ("tx_blocked_per_min", "Sent packets blocked per minute"),
        ("rx_drop_pct", "Share of received packets dropped"), ("tx_drop_pct", "Share of sent packets dropped"),
        ("bgp_established", "Inter-site BGP established"),
        ("connection_drops", "Inter-site BGP connection drops"), ("connection_drops_per_hour", "Inter-site BGP connection drops per hour"),
    ], [("local_rtep", "Local RTEP"), ("remote_rtep", "Remote RTEP"), ("remote_location", "Remote location"),
        ("bgp_state", "Inter-site BGP state")]),
    "tier0": ("Stretched Tier-0 Gateway", [
        ("sr_total", "Service routers"), ("sr_active", "Service routers active"),
        ("sr_down", "Service routers with routing down"),
        ("locations_without_active_sr", "Locations without an active service router"),
        ("sr_ha_changes", "Service router HA state changes in this cycle"),
        ("availability_pct", "Availability: an active service router where the HA mode needs one (100 = yes)"),
    ], [("ha_mode", "HA mode"), ("primary_location", "Primary location"),
        ("active_service_routers", "Active service routers"),
        ("no_active_sr_at", "Locations without an active service router")]),
    "tier1": ("Stretched Tier-1 Gateway", [], [("tier0", "Connected Tier-0"), ("primary_location", "Primary location")]),
    "segment": ("Stretched Segment", [], [("subnets", "Subnets"), ("gateway", "Connected gateway"), ("segment_type", "Type")]),
    "global_policy": ("Global Security Policy", [
        ("rules_total", "Rules"), ("rules_disabled", "Rules disabled"),
        ("hit_count", "Rule hits (all locations)"), ("hit_count_per_min", "Rule hits per minute (all locations)"),
        ("rules_unused", "Rules with no hit for the unused-rule period"),
        ("rules_shadowed", "Rules that an earlier rule of the policy always matches first"),
        ("rules_any_allow", "Rules that allow any source, destination and service"),
    ], [("category", "Category"), ("domain", "Domain"), ("scope", "Applied to"),
        ("unused_rules", "Rules with no hit for the unused-rule period"),
        ("shadowed_rules", "Shadowed rules (and the rule that matches first)"),
        ("any_allow_rules", "Rules that allow any traffic")]),
    "realization": ("Stretched Object at Location", [
        ("realized", "Realized"),
        ("hit_count", "Rule hits"), ("hit_count_per_min", "Rule hits per minute"),
        ("packet_count", "Packets"), ("byte_count", "Bytes"), ("session_count", "Sessions"),
    ], [("status", "Realization status"), ("location", "Location"), ("object_path", "Object path"), ("object_type", "Object type")]),
    "bgp_neighbor": ("Tier-0 BGP Neighbor", [
        ("established", "Established"), ("prefixes_in", "Prefixes received"), ("prefixes_out", "Prefixes advertised"),
        ("messages_in", "Messages received"), ("messages_out", "Messages sent"),
        ("seconds_established", "Seconds established"), ("connection_drops", "Connection drops"),
        ("connection_drops_per_hour", "Connection drops per hour"),
    ], [("state", "Connection state"), ("remote_as", "Remote AS"), ("source_address", "Source address"),
        ("neighbor_address", "Neighbor address"), ("location", "Location"), ("edge", "Edge"),
        ("session_type", "Session type (uplink or inter-SR)")]),
    "rule": ("Global Firewall Rule", [
        ("hit_count", "Hits (all locations)"), ("hit_count_per_min", "Hits per minute (all locations)"),
        ("packet_count", "Packets"), ("byte_count", "Bytes"), ("session_count", "Sessions"),
    ], [("action", "Action"), ("disabled", "Disabled"), ("sources", "Sources"), ("destinations", "Destinations")]),
}
# kinds that are stretched objects: they share span/realization metrics and change properties
STRETCHED = ("tier0", "tier1", "segment", "global_policy")
for _kind in STRETCHED:
    MODEL[_kind][1].extend([
        ("span_locations", "Locations in span"), ("realized_locations", "Locations realized"),
        ("unrealized_locations", "Locations not realized"),
        ("error_locations", "Locations in error"),
    ])
    MODEL[_kind][2].extend([
        ("status", "Consolidated status"), ("path", "Policy path"), ("revision", "Revision"),
        ("last_modified_user", "Last modified by"), ("last_modified_time", "Last modified"),
    ])


# ---- metric groups: the folders of the Metrics tab. The key VCF Operations stores is "<group>|<metric>".
_G = {
    "alarms": "Alarms", "certificates": "Certificates", "backup": "Backup", "cluster": "Cluster",
    "connectivity": "Connectivity", "sync": "Sync", "intersite": "Inter-site", "collection": "Collection",
    "global_managers": "Global Managers", "locations": "Locations", "stretched": "Stretched objects",
    "totals": "Counters since start", "rates": "Rates", "realization": "Realization", "rules": "Rules",
    "traffic": "Traffic", "prefixes": "Prefixes and messages", "capacity": "Capacity",
    "service_routers": "Service routers", "changes": "Changes", "time": "Time", "limits": "Configuration limits",
    "configuration": "Configuration", "availability": "Availability", "account": "Collection account",
}
_GROUP_BY_KEY = {
    "alarms_open_critical": "alarms", "alarms_open_high": "alarms", "alarms_open_other": "alarms",
    "cert_min_days_to_expiry": "certificates", "backup_age_hours": "backup",
    "nodes_online": "cluster", "nodes_offline": "cluster", "service_groups_degraded": "cluster",
    "reachable": "connectivity", "gm_connection_up": "connectivity", "collector_reachable": "connectivity",
    "standby_sync_entries": "sync", "full_sync_failed": "sync", "standby_in_sync": "sync", "standby_sync_remaining": "sync",
    "standby_out_of_sync_minutes": "sync", "standby_full_sync_age_hours": "sync", "full_sync_duration_s": "sync",
    "full_sync_age_hours": "sync", "minutes_since_collection": "connectivity",
    "replication_flows": "sync", "replication_flows_unhealthy": "sync", "gm_rtt_ms": "connectivity", "gm_rtt_pct_of_max": "connectivity", "sync_queue_used_pct": "sync",
    "rx_drop_pct": "rates", "tx_drop_pct": "rates",
    "gm_last_seen_minutes": "connectivity", "split_brain_reported": "global_managers",
    "realization_checked": "collection", "stale_objects_deleted": "collection",
    "intersite_bgp_total": "intersite", "intersite_bgp_established": "intersite", "intersite_bgp_down": "intersite",
    "rtep_tunnels_total": "intersite", "rtep_tunnels_bgp_down": "intersite", "rtep_dropped_per_min": "intersite",
    "collection_duration_s": "collection", "api_calls": "collection", "api_calls_throttled": "collection",
    "api_errors": "collection", "data_gaps": "collection", "api_calls_retried": "collection",
    "global_managers_foreign": "global_managers", "locations_down": "locations", "managers_over_budget": "collection",
    "global_managers_configured": "global_managers", "global_managers_reachable": "global_managers",
    "global_managers_unreachable": "global_managers", "global_managers_active": "global_managers",
    "locations_total": "locations", "locations_gm_connected": "locations", "locations_collected": "locations",
    "locations_healthy": "locations", "locations_healthy_pct": "locations",
    "stretched_objects_total": "stretched", "stretched_objects_fully_realized": "stretched",
    "span_locations": "realization", "realized_locations": "realization", "unrealized_locations": "realization",
    "error_locations": "realization", "rules_total": "rules", "rules_disabled": "rules",
    "hit_count": "traffic", "hit_count_per_min": "traffic", "packet_count": "traffic", "byte_count": "traffic",
    "session_count": "traffic",
    "prefixes_in": "prefixes", "prefixes_out": "prefixes", "messages_in": "prefixes", "messages_out": "prefixes",
    "datapath_cpu_pct": "capacity", "datapath_cpu_peak_pct": "capacity", "services_cpu_pct": "capacity",
    "memory_used_pct": "capacity", "datapath_memory_pct": "capacity", "datapath_pool_peak_pct": "capacity",
    "disk_fullest_pct": "capacity",
    "sr_total": "service_routers", "sr_active": "service_routers", "sr_down": "service_routers",
    "locations_without_active_sr": "service_routers", "sr_ha_changes": "service_routers",
    "changes_detected": "changes",
    "clock_skew_to_gm_s": "time", "clock_skew_to_active_s": "time", "clock_max_skew_s": "time",
    "capacity_max_pct": "limits", "capacity_items_warning": "limits", "capacity_items_critical": "limits",
    "overridden_objects": "configuration",
    "availability_pct": "availability", "password_days_left": "account", "password_min_days_left": "account",
    "rules_unused": "rules", "rules_shadowed": "rules", "rules_any_allow": "rules", "rules_to_review": "rules",
}
for _k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_dropped", "tx_dropped", "rx_blocked", "tx_blocked"):
    _GROUP_BY_KEY[_k] = "totals"
    _GROUP_BY_KEY[RATES[_k][0]] = "rates"
# a type with only one or two metrics keeps them at the top
_FLAT_KINDS = {"manager_node", "sync_channel", "tier0_sr"}
# kinds whose alerts stay quiet while a location is cut off from the Global Manager (see "location_outage")
OUTAGE_KINDS = {"global_manager", "location", "sync_channel", "edge_cluster", "edge_node", "rtep_tunnel", "bgp_neighbor",
                "tier0", "tier1", "segment", "global_policy", "realization", "tier0_sr"}


def metric_group(kind: str, key: str) -> Optional[str]:
    return None if kind in _FLAT_KINDS else _GROUP_BY_KEY.get(key)


def fk(kind: str, key: str) -> str:
    """Full metric key as stored in VCF Operations."""
    g = metric_group(kind, key)
    return f"{g}|{key}" if g else key


# ---- metric flags. KPI: VCF Operations weighs these first when it looks for evidence of a problem.
KPI = {
    ("federation", "global_managers_active"), ("federation", "locations_healthy_pct"),
    ("global_manager", "nodes_offline"), ("global_manager", "standby_in_sync"),
    ("federation", "switchover_ready"), ("location", "healthy"), ("location", "gm_connection_up"), ("location", "intersite_bgp_down"),
    ("location", "full_sync_failed"), ("sync_channel", "connection_up"),
    ("edge_node", "intersite_bgp_established"), ("rtep_tunnel", "bgp_established"),
    ("rtep_tunnel", "rx_dropped_per_min"), ("rtep_tunnel", "tx_dropped_per_min"),
    ("rtep_tunnel", "rx_drop_pct"), ("rtep_tunnel", "tx_drop_pct"),
    ("bgp_neighbor", "established"), ("realization", "realized"),
    ("tier0", "error_locations"), ("tier1", "error_locations"), ("segment", "error_locations"),
    ("global_policy", "error_locations"),
    ("edge_node", "datapath_cpu_pct"), ("tier0_sr", "routing_up"), ("tier0", "sr_active"),
    ("tier0", "locations_without_active_sr"),
}
# state metrics that are 0 or 1
DISCRETE = {"switchover_ready", "healthy", "standby_in_sync", "reachable", "online", "gm_connection_up", "collector_reachable", "connection_up", "bgp_established",
            "established", "realized", "active", "routing_up"}
RATE_KEYS = {v[0] for v in RATES.values()}


def get_adapter_definition() -> AdapterDefinition:
    with Timer(logger, "Get Adapter Definition"):
        d = AdapterDefinition(ADAPTER_KIND, ADAPTER_NAME)
        d.define_string_parameter(
            "gm_hosts", label="Global Manager addresses",
            description="Cluster address (VIP or FQDN) of every Global Manager cluster of this federation, "
                        "comma separated: the active one and the standby one. The adapter finds the active one "
                        "on every cycle.", required=True)
        d.define_string_parameter(
            "lm_overrides", label="Local Manager address overrides",
            description="Optional. Use when the collector cannot reach a Local Manager on the address the "
                        "Global Manager knows. Format: location-name=address;location-name=address",
            required=False, advanced=True)
        d.define_string_parameter(
            "rule_policies", label="Policies collected rule by rule",
            description="Optional. Comma-separated IDs of global security policies whose rules become objects, "
                        "or * for all. Empty: policy totals only (recommended at scale).",
            required=False, advanced=True)
        d.define_string_parameter(
            "federation_name", label="Federation name",
            description="Optional. Name of the federation object in VCF Operations. Empty: 'NSX Federation' and the "
                        "first characters of the federation ID. Useful with several federations.",
            required=False, advanced=True)
        d.define_enum_parameter(
            "certificate_trust", values=["enforce", "ca", "report"], label="Certificate trust",
            description="enforce: a manager is collected only when its certificate is a trusted one (accepted on the "
                        "account for a Global Manager, known by the Global Manager for a Local Manager). A manager with "
                        "no trusted certificate on record is not collected. "
                        "ca: the same, and a certificate is also trusted when it chains to a trusted CA (the CA "
                        "certificates below, or the system store of the adapter container) and is valid for the address. "
                        "report: it is collected and the mismatch is reported.",
            default="enforce", advanced=True)
        d.define_string_parameter(
            "ca_certificates", label="Trusted CA certificates (PEM)",
            description="Optional, for certificate trust 'ca'. The PEM text of the root or issuing CA certificates of "
                        "the managers, one after the other. Pasting it on one line is fine.",
            required=False, advanced=True)
        d.define_enum_parameter(
            "link_nsx_objects", values=["auto", "on", "off"], label="Link to the objects of the NSX integration",
            description="Each federation edge node and edge cluster gets a relationship to the transport node and "
                        "edge cluster object of the built-in NSX integration. auto: only for a Local Manager that "
                        "the built-in integration monitors. on: always. off: never.",
            default="auto", advanced=True)
        d.define_enum_parameter(
            "lm_alarm_alerts", values=["auto", "always", "never"], label="Alerts for the federation alarms of a Local Manager",
            description="auto: this pack raises them only for a Local Manager that the built-in NSX integration does "
                        "not monitor, because that integration raises the same alarms. always: this pack raises them "
                        "for every Local Manager. never: it leaves them to the built-in integration.",
            default="auto", advanced=True)
        d.define_enum_parameter(
            "change_events", values=["on", "off"], label="Change events",
            description="on: each change the collection sees (Global Manager role or version, locations added or "
                        "removed, edges, Tier-0 service router HA state, stretched objects created, changed or deleted) "
                        "is sent as an information event on the object, and listed in 'Recent changes' on the "
                        "federation. off: only the list.",
            default="on", advanced=True)
        d.define_int_parameter(
            "unused_rule_days", label="Unused firewall rule period (days)",
            description="A rule of a global security policy whose hit count has not grown for this many days, at every "
                        "location of its span, is reported as unused. A rule that never had a hit counts from the day "
                        "the collection first saw it. 0 turns the check off.",
            default=UNUSED_RULE_DAYS, advanced=True)
        d.define_string_parameter(
            "delete_stale_after_days", label="Delete objects that left NSX after (days)",
            description="0: never (default). Above 0: an object of this account that NSX no longer returns and that "
                        "has had no data for that many days is deleted from VCF Operations, with its history.",
            required=False, default="0", advanced=True)
        d.define_int_parameter("max_parallel", label="Managers collected in parallel", default=8, advanced=True)
        d.define_int_parameter("request_timeout", label="API request timeout (s)", default=30, advanced=True)
        d.define_int_parameter("call_budget", label="API call budget per manager and cycle", default=2000, advanced=True)
        d.define_int_parameter(
            "container_memory_limit", label="Adapter Memory Limit (MB)",
            description="Maximum memory VCF Operations allocates to the adapter container.",
            required=True, advanced=True, default=1024)

        cred = d.define_credential_type("nsx_credential", "NSX read-only account")
        cred.define_string_parameter("username", "User name (Global Managers)")
        cred.define_password_parameter("password", "Password (Global Managers)")
        cred.define_string_parameter("lm_username", "User name (Local Managers, if different)", required=False)
        cred.define_password_parameter("lm_password", "Password (Local Managers, if different)", required=False)

        for key, (label, metrics, props) in MODEL.items():
            ot = d.define_object_type(key, label)
            ot.define_string_identifier("id", "ID")
            groups: Dict[str, Any] = {}
            for mkey, mlabel in metrics:
                g = metric_group(key, mkey)
                if g and g not in groups:
                    groups[g] = ot.define_group(g, _G[g])
                target = groups[g] if g else ot
                kpi = (key, mkey) in KPI
                target.define_metric(mkey, mlabel, unit=UNITS.get(mkey), is_rate=mkey in RATE_KEYS,
                                     is_discrete=mkey in DISCRETE, is_kpi=kpi, is_key_attribute=kpi)
            for pkey, plabel in props:
                ot.define_string_property(pkey, plabel)
            if key in OUTAGE_KINDS:
                ot.define_string_property("location_outage", "Locations in outage")
            if key == "sync_channel":
                ot.define_string_property("gm_outage", "Global Manager outage")
            if not any(pkey == "federation_id" for pkey, _ in props):
                # on every object: one custom group per federation selects its objects by it (install/federation_groups.py)
                ot.define_string_property("federation_id", "Federation ID")
        return d


# ---- helpers --------------------------------------------------------------------------
class Config:
    def __init__(self, ai: AdapterInstance) -> None:
        def val(key: str, default: Any = None) -> Any:
            v = ai.get_identifier_value(key)
            return default if v in (None, "") else v

        self.gm_hosts = [h.strip() for h in str(val("gm_hosts", "")).split(",") if h.strip()]
        self.lm_overrides: Dict[str, str] = {}
        for pair in str(val("lm_overrides", "")).replace(",", ";").split(";"):
            if "=" in pair:
                name, addr = pair.split("=", 1)
                self.lm_overrides[name.strip()] = addr.strip()
        rp = str(val("rule_policies", "")).strip()
        self.rule_policies = {p.strip() for p in rp.split(",") if p.strip()}
        self.trust_mode = str(val("certificate_trust", "enforce"))
        self.enforce_trust = self.trust_mode == "enforce"
        self.ca_mode = self.trust_mode == "ca"
        ca_pems, self.ca_invalid = split_pem(str(val("ca_certificates", "")))
        self.federation_name = str(val("federation_name", "")).strip()
        self.link_mode = str(val("link_nsx_objects", "auto"))
        self.lm_alarm_mode = str(val("lm_alarm_alerts", "auto"))
        self.change_events = str(val("change_events", "on")) != "off"
        try:
            days = float(str(val("delete_stale_after_days", "0")).replace(",", "."))
            self.delete_stale_days = 0.0 if days <= 0 else max(1.0, days)  # never less than one day
        except ValueError:
            self.delete_stale_days = 0.0
        def number(key: str, default: int, low: int) -> int:
            try:  # a setting that is not a number falls back to its default
                return max(low, int(float(str(val(key, default)))))
            except ValueError:
                return default
        self.max_parallel = number("max_parallel", 8, 1)
        self.timeout = number("request_timeout", 30, 1)
        self.call_budget = number("call_budget", 2000, 1)
        self.unused_rule_days = number("unused_rule_days", UNUSED_RULE_DAYS, 0)
        # thresholds of the alert definitions; change them in a VCF Operations policy, not here
        self.backup_max_age_h = 24
        self.cert_warning_days = 30
        self.user = ai.get_credential_value("username") or ""
        self.password = ai.get_credential_value("password") or ""
        self.lm_user = ai.get_credential_value("lm_username") or self.user
        self.lm_password = ai.get_credential_value("lm_password") or self.password
        self.trusted = set()
        for cert in ai.get_certificates() or []:
            try:
                pem = cert.get_certificate_string()
                self.trusted.add(pem_sha256(pem))
                ca_pems.extend(split_pem(pem)[0])  # an accepted CA certificate also anchors chains in CA mode
            except Exception:
                pass
        self.cadata = "".join(ca_pems)

    def gm_client(self, host: str) -> NsxClient:
        return NsxClient(host, self.user, self.password, self.timeout, self.call_budget,
                         self.trusted, self.enforce_trust, self.ca_mode, self.cadata)

    def lm_client(self, host: str, gm_thumbprint: Optional[str]) -> NsxClient:
        trusted = set(self.trusted)
        if gm_thumbprint:  # the Global Manager pins each Local Manager by SHA-256 thumbprint
            trusted.add(gm_thumbprint.lower().replace(":", ""))
        return NsxClient(host, self.lm_user, self.lm_password, self.timeout, self.call_budget,
                         trusted, self.enforce_trust, self.ca_mode, self.cadata)


def trust_text(client: NsxClient) -> str:
    """The certificate_trust property of a manager that was collected."""
    if client.trust == "pinned":
        return "trusted"
    if client.trust == "ca":
        return "trusted (CA chain)"
    if not client.trusted and not client.ca_mode:
        return "not checked (no trusted certificate on record)"
    return "NOT TRUSTED (reported only)"


def consolidated(status: Any) -> str:
    if isinstance(status, dict):
        inner = status.get("consolidated_status")
        return consolidated(inner) if isinstance(inner, dict) else str(inner or "UNKNOWN")
    return str(status or "UNKNOWN")


def tail(path: Optional[str]) -> str:
    return (path or "").rstrip("/").split("/")[-1]


def summarize_alarms(alarms: List[dict]) -> Dict[str, int]:
    out = {"critical": 0, "high": 0, "other": 0}
    for a in alarms:
        sev = str(a.get("severity", "")).upper()
        out["critical" if sev == "CRITICAL" else "high" if sev == "HIGH" else "other"] += 1
    return out


def cert_summary(certs: List[dict]) -> Tuple[Optional[float], str]:
    """(days until the first certificate expires, its name)."""
    best: Tuple[Optional[float], str] = (None, "")
    now = time.time() * 1000
    for c in certs:
        for det in c.get("details") or []:
            not_after = det.get("not_after")
            if not not_after:
                continue
            days = (float(not_after) - now) / 86400000.0
            if best[0] is None or days < best[0]:
                best = (round(days, 1), str(c.get("display_name") or det.get("subject_cn") or c.get("id")))
            break  # first entry is the leaf certificate
    return best


def same_release(a: str, b: str) -> bool:
    """True when two NSX versions are the same release (first three numbers). 9.1.1.0 and 9.1.1.0100 are
    the same release at different patch levels, which NSX Federation supports."""
    part = lambda v: [x for x in str(v).split(".")][:3]
    return part(a) == part(b)


def backup_summary(overview: dict) -> Tuple[bool, Optional[float], str]:
    """(enabled, hours since last successful cluster backup, last result)."""
    enabled = bool((overview.get("backup_config") or {}).get("backup_enabled"))
    history = (overview.get("backup_operation_history") or {}).get("cluster_backup_statuses") or []
    last_ok = max((h.get("end_time", 0) for h in history if h.get("success")), default=0)
    last = max(history, key=lambda h: h.get("end_time", 0), default=None)
    result = "NONE" if last is None else ("SUCCESS" if last.get("success") else "FAILED")
    age = round((time.time() * 1000 - last_ok) / 3600000.0, 1) if last_ok else None
    return enabled, age, result


def release_tuple(v: Any) -> Tuple[int, ...]:
    """First three numbers of an NSX version, for ordering releases."""
    out = []
    for x in str(v).split(".")[:3]:
        out.append(int(x) if x.isdigit() else 0)
    return tuple(out)


def rtt_values(stat: Any) -> List[float]:
    """Round-trip times in ms from the 'rtt_statistic' of a replication flow, whatever its exact shape."""
    if isinstance(stat, (int, float)) and not isinstance(stat, bool):
        return [float(stat)]
    if isinstance(stat, dict):
        return [float(v) for k, v in stat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
                and any(w in k.lower() for w in ("avg", "average", "mean", "last", "latest", "current", "rtt"))]
    return []


def drop_pct(prev: Dict[str, float], cur: Dict[str, float], dropped: str, packets: str) -> Optional[float]:
    """Dropped packets as a share of the packets of the interval. NSX counts a dropped packet in total_packets too:
    in the lab the received total of a tunnel matched the peer's sent total exactly while its drop counter read 1.
    None when a counter is missing or went down (reset on the NSX side), when the drops exceed the total (counters
    read at different moments), or when too few packets passed for a percentage to mean anything."""
    if not all(k in prev and k in cur for k in (dropped, packets)):
        return None
    d_drop, d_pkt = cur[dropped] - prev[dropped], cur[packets] - prev[packets]
    if d_drop < 0 or d_pkt < LOSS_MIN_PACKETS or d_drop > d_pkt:
        return None
    return round(100.0 * d_drop / d_pkt, 2)


def flow_summary(doc: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
    """Per location path: the highest latency NSX measured on the flows between the Global Manager and the location,
    and how full the fullest sync queue of these flows is, in %. Only the flows between a Global Manager and a location
    count, and a negative latency is read as no measurement."""
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    seen: Dict[str, Dict[str, List[float]]] = {}
    for f in doc.get("flow_info") or []:
        c = f.get("cross_site_flow_info") or {}
        if str(f.get("flow_type") or "GM_TO_LM").upper() not in ("GM_TO_LM", "LM_TO_GM"):
            continue  # between Global Managers, or between locations: not a round-trip time to the Global Manager
        for path in (c.get("from_site_path"), c.get("to_site_path")):
            if not str(path or "").startswith("/global-infra/sites/"):
                continue
            e = seen.setdefault(path, {"rtt": [], "queue": []})
            if num(c.get("latency_millis")) and c["latency_millis"] >= 0:
                e["rtt"].append(float(c["latency_millis"]))
            for q in f.get("queue_infos") or []:
                if num(q.get("current_size")) and num(q.get("max_size")) and q["max_size"] > 0 and q["current_size"] >= 0:
                    e["queue"].append(100.0 * q["current_size"] / q["max_size"])
    return {p: {"rtt_ms": max(e["rtt"]) if e["rtt"] else None,
                "queue_pct": round(max(e["queue"]), 2) if e["queue"] else None} for p, e in seen.items()}


def rtt_pct(rtt: Optional[float], maximum: Any) -> Optional[float]:
    """Round-trip time as a percentage of the maximum RTT set for the location in NSX."""
    try:
        m = float(maximum)
    except (TypeError, ValueError):
        return None
    return round(100.0 * rtt / m, 1) if rtt is not None and m > 0 else None


def epoch_seconds(text: Any) -> Optional[float]:
    """NSX prints some timestamps as 'seconds: 1795092255\nnanos: 2094...'."""
    for part in str(text or "").split():
        if part.isdigit() and len(part) >= 9:
            return float(part)
    return None


class OpsApi:
    """Read access to VCF Operations itself, with the connection the collector gives the adapter. Used for what
    NSX cannot tell: which Local Managers the built-in NSX integration monitors, the location list of earlier
    cycles when the container cache is empty, and the objects of this account. Every call may fail: the
    adapter then works without it and says so in a property of the federation object."""

    def __init__(self, ai: Any) -> None:
        self.client = getattr(ai, "suite_api_client", None)
        self.error = "" if self.client else "no connection information from the collector"
        self.used = False

    def _call(self, method: str, url: str) -> Optional[dict]:
        if self.client is None:
            return None
        try:
            with self.client as c:
                with getattr(c, method)(url) as resp:
                    self.used = True
                    if not resp.ok:
                        self.error = f"HTTP {resp.status_code} on {url.split('?')[0]}"
                        return None
                    return resp.json() if resp.content else {}
        except Exception as e:  # never let VCF Operations break an NSX collection
            self.error = repr(e)[:160]
            return None

    def get(self, url: str) -> Optional[dict]:
        return self._call("get", url)

    def delete(self, url: str) -> bool:
        return self._call("delete", url) is not None

    def state(self) -> str:
        return "ok" if not self.error else "not available: " + self.error

    def nsx_cluster_ids(self) -> Optional[List[str]]:
        """NSX cluster IDs of the accounts of the built-in NSX integration. None: unknown."""
        d = self.get("/api/adapters?adapterKindKey=NSXTAdapter")
        if d is None:
            return None
        return sorted({i.get("value") for a in d.get("adapterInstancesInfoDto", [])
                       for i in (a.get("resourceKey") or {}).get("resourceIdentifiers", [])
                       if (i.get("identifierType") or {}).get("name") == "NSX_CLUSTER_ID" and i.get("value")})

    def resources(self, kind: str) -> List[dict]:
        d = self.get(f"/api/resources?adapterKind={ADAPTER_KIND}&resourceKind={kind}&pageSize=5000")
        return (d or {}).get("resourceList", [])

    def properties(self, resource_id: str) -> Dict[str, str]:
        d = self.get(f"/api/resources/{resource_id}/properties")
        return {p.get("name"): p.get("value") for p in (d or {}).get("property", [])}


class Collector:
    def __init__(self, ai: AdapterInstance, result: CollectResult) -> None:
        self.cfg = Config(ai)
        self.ops = OpsApi(ai)
        self.result = result
        self.cache = Cache("fed_" + "_".join(sorted(self.cfg.gm_hosts)))
        self.clients: List[NsxClient] = []
        self.fed_id = ""
        self.fed: Optional[Object] = None
        self.locations: Dict[str, Object] = {}  # site path -> Location object
        self.location_names: Dict[str, str] = {}  # site path -> name
        self.down_locations: set = set()  # site paths that are disconnected: leaf events are suppressed
        self.stretched_total = 0
        self.stretched_full = 0
        self.gm_objects: Dict[str, Object] = {}  # Global Manager name -> object
        self.sites_of: Dict[int, set] = {}  # id(object) -> site paths it depends on
        self.span_of: Dict[str, set] = {}  # stretched object path -> site paths of its realization
        self.gm_list_read = False  # the list of Global Managers was read from the active one in this cycle
        self.split_brain_reported = 0
        self.config_version: Optional[Any] = None
        self.realization_checked = 0
        self.stale_deleted: List[str] = []
        # NSX cluster IDs the built-in NSX integration monitors (None = unknown), asked once an hour
        self.builtin_clusters: Optional[List[str]] = self.cache.cached("builtin_nsx_clusters", SLOW_TTL, self.ops.nsx_cluster_ids)
        if self.builtin_clusters is None:  # VCF Operations did not answer: the last known list, so alerts do not flip
            self.builtin_clusters = self.cache.get_any_age("builtin_nsx_clusters")
        self.standby_sync: List[Tuple[Optional[str], int, int, Optional[float]]] = []  # (name, in sync, entries left, full sync age h)
        self.standby_since: Dict[str, float] = {}  # standby name -> minutes out of sync
        self.active_name: Optional[str] = None
        self.sync_read = False  # the active Global Manager answered the sync status call in this cycle
        self.gm_state: Dict[str, Dict[str, Any]] = {}  # Global Manager name -> what decides if it can take over
        self.gm_answered: Set[str] = set()  # Global Managers that answered in this cycle (their data may be partial)
        # name -> site ID of every Global Manager and location seen, kept across cycles: the sync channels are keyed
        # by site ID, and NSX names the far end of a channel only by its site name
        self.site_ids: Dict[str, str] = dict(self.cache.get_any_age("site_ids") or {})
        # counter values of the previous cycle, for rates: object id -> {"t": epoch, counter: value}
        self.prev_counters: Dict[str, Dict[str, float]] = self.cache.get_any_age("counters") or {}
        self.new_counters: Dict[str, Dict[str, float]] = {}
        # Tier-0 service routers and RTEP edges per location, filled by the location workers (one key each):
        # site path -> records, and site path -> True when every edge of the location answered
        self.sr_records: Dict[str, List[Dict[str, Any]]] = {}
        self.sr_complete: Dict[str, bool] = {}
        self.edge_seen: Dict[str, Tuple[Dict[str, str], bool]] = {}
        # change timeline: what the previous cycles saw, per category, and the changes of this cycle
        self.change_prev: Dict[str, Dict[str, Any]] = dict(self.cache.get_any_age("change_state") or {})
        self.change_next: Dict[str, Dict[str, Any]] = dict(self.change_prev)
        self.changes: List[Tuple[Optional[Object], str]] = []
        self.partial: Set[str] = set()  # cached_clean keys whose value of this cycle is not complete
        self.edge_answered: Dict[str, Set[str]] = {}  # site path -> transport node IDs whose status call answered
        self.edge_objects: Dict[str, Object] = {}  # transport node ID -> edge node object
        # clock of each manager against the collector: name or site path -> (offset ms, call duration s, object)
        self.clocks: Dict[str, Tuple[float, float, Object]] = {}
        # one wall-clock reading per cycle; calls are timed on the monotonic clock, so a step of the clock of the
        # cloud proxy during the cycle changes no difference between two managers
        self.wall0, self.mono0 = time.time(), time.monotonic()
        self.overrides: Dict[str, Tuple[Dict[str, str], bool]] = {}  # site path -> ({path: label}, read complete)

    # -- object helpers
    def obj(self, kind: str, name: str, ident: str) -> Object:
        return self.result.object(ADAPTER_KIND, kind, name, [Identifier("id", f"{self.fed_id}:{ident}")])

    def nsx_object(self, kind: str, name: str, nsx_id: str, cluster_id: str, zone: bool = False) -> Object:
        """Key of an object of the built-in NSX integration (adapter kind NSXTAdapter), used only as the far end
        of a relationship. VCF Operations matches it by its identifiers; this pack sends no data for it."""
        ids = [Identifier("ID", nsx_id), Identifier("MANAGEMENT_CLUSTER_UUID", cluster_id)]
        if zone:
            ids.append(Identifier("TransportNodeZoneID", ""))
        return self.result.object("NSXTAdapter", kind, name, ids)

    @staticmethod
    def metrics(o: Object, values: Dict[str, Any]) -> None:
        if values.get("intersite_bgp_total") is not None and values.get("intersite_bgp_established") is not None:
            values = dict(values, intersite_bgp_down=max(0, values["intersite_bgp_total"] - values["intersite_bgp_established"]))
        kind = o.object_type()
        for k, v in values.items():
            if v is not None:
                o.with_metric(fk(kind, k), float(v))

    def counters(self, o: Object, ident: str, values: Dict[str, Any]) -> None:
        """Send running totals and, from the second cycle on, their rate since the previous cycle.
        A counter that went down (reset on the NSX side) gives no rate for that cycle, except the session drop counter,
        which gives 0."""
        self.metrics(o, values)
        now = time.time()
        current = {k: float(v) for k, v in values.items() if k in RATES and v is not None}
        prev = self.prev_counters.get(ident) or {}
        dt = now - float(prev.get("t", 0))
        if prev and 30 <= dt <= 3600:
            for k, v in current.items():
                if k in prev and v >= prev[k]:
                    rate_key, per = RATES[k]
                    self.metrics(o, {rate_key: round((v - prev[k]) / dt * per, 2)})
                elif k in prev and k == "connection_drops":
                    # the session counter restarted with the edge: 0, so that a flap rate of the cycle before does
                    # not stay as the last value and keep the flap alert open
                    self.metrics(o, {RATES[k][0]: 0.0})
            for pct_key, (dropped, packets) in LOSS.items():
                pct = drop_pct(prev, current, dropped, packets)
                if pct is not None:
                    self.metrics(o, {pct_key: pct})
        self.new_counters[ident] = dict(current, t=now)

    @staticmethod
    def props(o: Object, values: Dict[str, Any]) -> None:
        for k, v in values.items():
            if v is not None:
                if isinstance(v, float) and v.is_integer():
                    v = int(v)  # 10, not 10.0
                o.with_property(k, str(v))  # every property of the pack is defined as a string

    @staticmethod
    def event(o: Object, message: str, criticality: Criticality, alert_key: Optional[str] = None) -> None:
        # alert_key names the metric or property that an alert definition of the pack watches for this
        # condition. Such a condition raises a real alert, so no event is sent (it would show a second,
        # "Notification event" alert for the same thing). Conditions without an alert definition are sent
        # as events and surface as notification alerts.
        if alert_key is None:
            o.with_event(message=message, criticality=criticality)

    def read_clock(self, client: NsxClient, key: str, o: Object) -> None:
        """The clock of the manager against the clock of the collector: the system time NSX returns, minus the
        middle of the call. Two managers read in the same cycle give their difference; the collector clock cancels
        out. A call slower than CLOCK_MAX_RTT_S gives no reading."""
        m0 = time.monotonic()
        st = client.try_get("/api/v1/node/status", default={}) or {}
        m1 = time.monotonic()
        rtt = m1 - m0
        if client.ok("/api/v1/node/status") and isinstance(st, dict) and 0 <= rtt <= CLOCK_MAX_RTT_S:
            try:
                middle_ms = (self.wall0 + (m0 + m1) / 2.0 - self.mono0) * 1000.0
                self.clocks[key] = (float(st["system_time"]) - middle_ms, rtt, o)
            except (KeyError, TypeError, ValueError):
                pass
        # the server list changes rarely (once an hour); the state of the service is read in every cycle
        servers = self.cache.cached(f"ntp:{client.host}", SLOW_TTL, lambda: self.load_ntp_servers(client))
        state = client.try_get("/api/v1/node/services/ntp/status", default={}) or {}
        self.props(o, {"ntp_servers": (", ".join(str(x) for x in servers) or "none configured") if servers is not None else None,
                       "ntp_service": state.get("runtime_state") if isinstance(state, dict)
                       and client.ok("/api/v1/node/services/ntp/status") else None})

    def password_expiry(self, client: NsxClient, user: str, o: Object) -> None:
        """Days until the password of the collection account expires on this manager. NSX counts the days since
        the last change and the days a password is valid; both change once a day, so the list is cached."""
        path = "/api/v1/node/users"

        def load() -> Optional[List[Dict[str, Any]]]:
            doc = client.try_get(path, default={}) or {}
            if not client.ok(path) or not isinstance(doc, dict):
                return None
            return [{k: u.get(k) for k in ("username", "status", "last_password_change", "password_change_frequency",
                                           "password_reset_required", "locked")}
                    for u in doc.get("results") or [] if isinstance(u, dict)]
        users = self.cache.cached(f"users:{client.host}", SLOW_TTL, load)
        if users is None:
            return  # unknown
        me = next((u for u in users if str(u.get("username")) == user), None)
        days: Optional[int] = None
        if me is None:
            text = "not a local NSX account: the directory that holds it sets the expiry"
        else:
            try:
                freq, since = int(me.get("password_change_frequency") or 0), int(me.get("last_password_change") or 0)
            except (TypeError, ValueError):
                freq, since = 0, 0
            if me.get("password_reset_required") or str(me.get("status")).upper() == "PASSWORD_EXPIRED":
                days, text = 0, "expired: NSX asks for a new password"
            elif freq <= 0:
                # a value far above any threshold, so an alert raised before expiry was turned off clears
                days, text = PASSWORD_NEVER_EXPIRES, "never expires (expiry turned off for this account)"
            else:
                days = freq - since
                text = (f"expired {-days} day(s) ago" if days < 0 else "expires today" if days == 0
                        else f"expires in {days} day(s)") + f" ({freq}-day policy, changed {since} day(s) ago)"
            locked = me.get("locked")
            if locked:
                text += "; locked on " + (", ".join(str(x) for x in locked) if isinstance(locked, list) else str(locked))
        self.metrics(o, {"password_days_left": None if days is None else max(days, 0)})
        self.props(o, {"password_expiry": f"{user}: {text}"})

    @staticmethod
    def load_ntp_servers(client: NsxClient) -> Optional[List[str]]:
        conf = client.try_get("/api/v1/node/services/ntp", default={}) or {}
        if not client.ok("/api/v1/node/services/ntp") or not isinstance(conf, dict):
            return None  # not cached: read again next cycle
        return list((conf.get("service_properties") or {}).get("servers") or [])

    def finish_clocks(self, gm: Optional[Dict[str, Any]]) -> None:
        """Clock difference of each Global Manager and location with the active Global Manager."""
        ref = self.clocks.get("gm:" + (gm["site"].get("name") or gm["host"])) if gm else None
        if not ref:
            return  # no active Global Manager, or its clock was not read: no difference is sent
        for key, (offset, rtt, o) in self.clocks.items():
            if o is ref[2]:
                continue
            diff_s = (offset - ref[0]) / 1000.0
            margin = (rtt + ref[1]) / 2.0
            kind = o.object_type()
            text = (f"{'ahead' if diff_s >= 0 else 'behind'} by {abs(diff_s):.2f} s (reading within {margin:.2f} s)")
            if kind == "location":
                self.metrics(o, {"clock_skew_to_gm_s": round(abs(diff_s), 2)})
                self.props(o, {"clock_offset_to_gm": text})
            else:
                self.metrics(o, {"clock_skew_to_active_s": round(abs(diff_s), 2)})
                self.props(o, {"clock_offset_to_active": text})
        # the reference reads 0, so a value it carried as a standby does not stay after a switchover
        self.metrics(ref[2], {"clock_skew_to_active_s": 0})
        self.props(ref[2], {"clock_offset_to_active": "reference (active Global Manager)"})

    def limits_and_overrides(self, client: NsxClient, lo: Object, loc: Dict[str, Any]) -> None:
        cap_path = "/policy/api/v1/infra/capacity/dashboard/usage"

        def load_capacity() -> Optional[Dict[str, Any]]:
            doc = client.try_get(cap_path, default={}) or {}
            return self.capacity_summary(doc) if client.ok(cap_path) else None  # a failed read is not cached
        cap = self.cache.cached(f"capacity:{client.host}", INVENTORY_TTL, load_capacity)
        if cap:
            self.metrics(lo, {k: cap[k] for k in ("capacity_max_pct", "capacity_items_warning", "capacity_items_critical")})
            self.props(lo, {k: cap[k] for k in ("capacity_fullest", "capacity_over_threshold")})
        search = "/policy/api/v1/search/query"

        def load_overrides() -> Optional[List[Dict[str, Any]]]:
            found = client.try_list(search, {"query": "overridden:true"})
            if not client.ok(search):
                return None
            return [{"path": o.get("path"), "type": o.get("resource_type"), "name": o.get("display_name") or o.get("id")}
                    for o in found if isinstance(o, dict) and str(o.get("path") or "").startswith("/global-infra/")]
        ov = self.cache.cached(f"overrides:{client.host}", INVENTORY_TTL, load_overrides)
        if ov is not None:
            self.overrides[loc["path"]] = ({o["path"]: f"{o['type']} {o['name']}" for o in ov}, True)
            self.metrics(lo, {"overridden_objects": len(ov)})
            self.props(lo, {"overridden_list": "; ".join(f"{o['type']} {o['name']} ({o['path']})" for o in ov[:10])
                            + (f"; and {len(ov) - 10} more" if len(ov) > 10 else "") if ov else "none"})

    @staticmethod
    def capacity_summary(doc: Any) -> Optional[Dict[str, Any]]:
        """The configuration limits of a Local Manager. NSX rates each one with its own thresholds (70% and 100% by
        default): its WARNING counts as warning, and its CRITICAL or ERROR, or a limit reached (100% or more), as
        critical. A limit whose maximum is 1 (one compute manager) is left out: it is full by design. An item of
        another shape is skipped. None when nothing usable came back."""
        items = []
        for u in (doc.get("capacity_usage") if isinstance(doc, dict) else None) or []:
            if not isinstance(u, dict):
                continue
            try:
                pct, top = float(u.get("current_usage_percentage")), float(u.get("max_supported_count"))
            except (TypeError, ValueError):
                continue
            if top > 1:
                items.append((pct, str(u.get("severity") or "").upper(), u))
        if not items:
            return None
        name = lambda u: u.get("display_name") or u.get("usage_type")
        critical = lambda pct, sev: sev in ("CRITICAL", "ERROR") or pct >= 100.0
        pct, _sev, top = max(items, key=lambda x: x[0])
        over = sorted((x for x in items if x[1] not in ("", "INFO") or x[0] >= 100.0), key=lambda x: x[0], reverse=True)
        return {
            "capacity_max_pct": round(pct, 2),
            "capacity_items_warning": sum(1 for p, sev, _u in items if sev == "WARNING" and not critical(p, sev)),
            "capacity_items_critical": sum(1 for p, sev, _u in items if critical(p, sev)),
            "capacity_fullest": f"{name(top)}: {top.get('current_usage_count')} of {top.get('max_supported_count')} ({pct:.1f}%)",
            "capacity_over_threshold": "; ".join(f"{name(u)} {p:.1f}% ({'CRITICAL' if critical(p, sev) else sev})"
                                                 for p, sev, u in over[:10]) or "none",
        }

    # -- step 1: Global Managers
    def probe_gm(self, host: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {"host": host, "ok": False, "error": ""}
        client = self.cfg.gm_client(host)
        self.clients.append(client)
        info["client"] = client
        try:
            self_site = client.get("/api/v1/sites/self")
            info.update(ok=True, federation_id=self_site.get("id"), site=self_site.get("site") or {})
        except CertificateNotTrusted as e:
            info["error"] = "certificate not trusted: " + str(e)
            info["cert_problem"] = True
        except Exception as e:  # connection refused, timeout, auth...
            info["error"] = repr(e)[:300]
        return info

    def collect(self) -> None:
        started = time.time()
        cfg = self.cfg
        if not cfg.gm_hosts:
            self.result.with_error("No Global Manager address configured")
            return
        with ThreadPoolExecutor(max_workers=max(1, len(cfg.gm_hosts))) as pool:
            gms = list(pool.map(self.probe_gm, cfg.gm_hosts))
        answered = [g for g in gms if g["ok"]]
        # One account monitors one federation: the federation of the first Global Manager that answers, or the
        # one seen in earlier cycles. A listed manager of another federation is reported and left out.
        inventory: Dict[str, Any] = {}
        if not self.cache.get_any_age("federation_id"):
            # Empty cache (first cycle, or the container restarted): what VCF Operations already holds for
            # these addresses decides which federation this account monitors, before any manager that answers.
            inventory = self.from_inventory()
        known = inventory.get("federation_id")
        if known and answered and len(answered) == len(cfg.gm_hosts) and not any(g["federation_id"] == known for g in answered):
            known = None  # every listed Global Manager answers for another federation: it was rebuilt
        self.fed_id = (self.cache.get_any_age("federation_id") or known
                       or (answered[0]["federation_id"] if answered else "") or "")
        foreign = [g for g in answered if g["federation_id"] != self.fed_id]
        reachable = [g for g in answered if g["federation_id"] == self.fed_id]
        active = [g for g in reachable if str(g["site"].get("active_gm", "")).upper() == "ACTIVE"]
        if not self.fed_id:
            self.result.with_error(
                "No Global Manager answered and no earlier collection is cached: "
                + "; ".join(f"{g['host']}: {g['error']}" for g in gms))
            return
        self.cache.put("federation_id", self.fed_id)
        self.fed = self.result.object(ADAPTER_KIND, "federation", cfg.federation_name or f"NSX Federation {self.fed_id[:8]}",
                                      [Identifier("id", self.fed_id)])
        fed = self.fed

        # Global Managers of earlier cycles that do not answer for this federation now read "not reachable". The
        # address list comes from the cache, or from VCF Operations after a restart of the container.
        known = dict(self.cache.get_any_age("gm_sites") or {})  # address -> (name, site id)
        if not known:
            known = self.gm_sites_from_inventory()
        known.update({g["host"]: (g["site"].get("name"), g["site"].get("id")) for g in reachable if g["site"].get("id")})
        self.cache.put("gm_sites", known)
        for name, site_id in known.values():
            self.remember_site(name, site_id)
        live_sites = {g["site"].get("id") for g in reachable}
        for g in gms:
            if g in reachable or g["host"] not in known or known[g["host"]][1] in live_sites:
                continue  # answers for this federation, never seen, or another address of a manager that answers
            name, site_id = known[g["host"]]
            go = self.obj("global_manager", name or g["host"], f"gm:{site_id}")
            go.add_parent(self.fed)
            self.metrics(go, {"reachable": 0})
            if name:
                self.gm_objects[name] = go  # the active one can still report its sync state
        for g in gms:
            if not g["ok"]:
                crit = Criticality.CRITICAL if not reachable else Criticality.IMMEDIATE
                what = "has an untrusted (changed?) certificate" if g.get("cert_problem") else "is not reachable by the collector"
                self.event(fed, f"Global Manager {g['host']} {what}: {g['error']}", crit, "global_managers_unreachable")
        if len(active) > 1:
            self.event(fed, "Split-brain: more than one Global Manager claims the active role: "
                       + ", ".join(g["site"].get("name", g["host"]) for g in active),
                       Criticality.CRITICAL, "global_managers_active")
        if reachable and not active:
            self.event(fed, "No reachable Global Manager is in the active role", Criticality.CRITICAL,
                       "global_managers_active")

        gm = active[0] if active else None
        sites_doc: Dict[str, Any] = {}
        if gm:
            try:
                sites_doc = self.collect_active_gm(gm)
                self.cache.put("sites_doc", {"locations": [dict(l) for l in sites_doc["locations"]],
                                             "global_managers": sites_doc["global_managers"]})
            except BudgetExceeded:
                pass  # counted on the client
            except Exception as e:
                logger.exception(e)
                self.event(fed, f"Collection from the active Global Manager failed: {repr(e)[:200]}", Criticality.IMMEDIATE)
        mode = "normal"
        if not sites_doc:
            # No active Global Manager: keep monitoring the locations from the last known list.
            sites_doc = self.cache.get_any_age("sites_doc") or {}
            why = "no Global Manager answers" if not reachable else "no active Global Manager" if not gm else \
                "the active Global Manager did not return its locations"
            mode = f"degraded ({why}): location list from cache" if sites_doc else f"degraded ({why}): no location list"
            if not sites_doc:
                inventory = inventory or self.from_inventory()
                if inventory.get("locations"):
                    sites_doc = {"locations": inventory["locations"], "global_managers": []}
                    mode = f"degraded ({why}): location list from VCF Operations"

        # Global Manager objects (active and standby); the standby is an object of its own.
        gm_names = []
        for g in reachable:
            try:
                gm_names.append(self.collect_gm_object(g, is_active=(g is gm)))
            except BudgetExceeded:
                pass  # counted on the client
            except Exception as e:
                logger.exception(e)
        # a standby exists when the federation lists one, not when the account lists two addresses
        modes = [str(g.get("mode", "")).upper() for g in sites_doc.get("global_managers", [])]
        modes += [str(g["site"].get("active_gm", "")).upper() for g in reachable]
        standby_known: Optional[bool] = "STANDBY" in modes
        if not standby_known and not self.gm_list_read:
            standby_known = None  # the list of Global Managers was not read: unknown, not "no standby"
        self.finish_standby_sync()
        if standby_known is False:
            self.event(fed, "No standby Global Manager is configured for this federation", Criticality.WARNING, "standby_configured")

        # -- step 2: Local Managers, in parallel, each with its own budget
        lm_sites = sites_doc.get("locations", [])
        for loc in lm_sites:  # before the workers start: a channel to a location is keyed by its site ID
            self.remember_site(loc.get("name"), loc.get("site_id"))
        with ThreadPoolExecutor(max_workers=max(1, min(cfg.max_parallel, len(lm_sites) or 1))) as pool:
            lm_results = list(pool.map(self.collect_location, lm_sites))
        collected = sum(1 for r in lm_results if r)
        self.finish_policies(lm_sites)
        self.finish_tier0(lm_sites)
        self.finish_clocks(gm)

        # -- root cause for alert storms: while a location is cut off from the Global Manager, the objects that
        # depend on it carry its name, and their alerts stay quiet behind the alert of the location
        # Each object carries the locations it depends on that are cut off: a tunnel its two ends, a sync channel
        # its two ends, a stretched object its span, an edge or a BGP neighbor its own location. A fault at
        # another location is not hidden. The Global Manager, as the hub, carries every location in outage.
        outage = ", ".join(sorted(self.location_names.get(p, p) for p in self.down_locations)) or "none"
        # Same idea for the sync channels of a Global Manager that does not answer, or of all Global Managers
        # when none is active. Names of earlier cycles tell which channel belongs to which manager.
        heard = {g["site"].get("name") for g in reachable}  # Global Managers that answered in this cycle
        some_silent = len(answered) < len(cfg.gm_hosts)
        location_names = set(self.location_names.values())
        for o in self.result.objects.values():
            if o.adapter_type() != ADAPTER_KIND:
                continue
            o.with_property("federation_id", self.fed_id)
            if o.object_type() == "global_manager":
                o.with_property("location_outage", outage)
            elif o.object_type() in OUTAGE_KINDS:
                mine = sorted(self.location_names.get(p, p) for p in self.sites_of.get(id(o), ()) if p in self.down_locations)
                o.with_property("location_outage", ", ".join(mine) or "none")
            if o.object_type() == "sync_channel":
                ends = [e.strip() for e in o.get_key().name.split("->")]
                gm_ends = [e for e in ends if e not in location_names]
                hit = bool(gm_ends) and (not gm or (some_silent and any(e not in heard for e in gm_ends)))
                o.with_property("gm_outage", "none" if not hit else
                                "no active Global Manager" if not gm else "Global Manager not reachable")
        # a switchover is refused while the Global Managers run different versions
        active_version = gm["site"].get("site_version") if gm else None
        for g in reachable:
            go = self.gm_objects.get(g["site"].get("name") or g["host"])
            if go is not None and active_version and g["site"].get("site_version"):
                self.props(go, {"version_matches_active_gm": g["site"].get("site_version") == active_version})
        ready, readiness = self.switchover_readiness(standby_known, active_version)
        self.track_changes(gm, lm_sites, from_gm=gm is not None and mode == "normal")
        clean = (mode == "normal" and not self.over_budget and len(answered) == len(cfg.gm_hosts) and not foreign
                 and collected == len(lm_sites) and not any(c.failed for c in self.clients))
        if clean:
            self.delete_stale()

        # -- step 3: federation object and pack self-health
        failed = sorted(f"{c.host}{p} ({why})" for c in self.clients for p, why in c.failed.items())
        self.metrics(fed, {
            "global_managers_configured": len(cfg.gm_hosts),
            "global_managers_reachable": len(reachable),
            "global_managers_unreachable": len(cfg.gm_hosts) - len(answered),
            "global_managers_foreign": len(foreign),
            "locations_down": len(self.down_locations) if gm else None,
            "api_calls_retried": sum(c.retried for c in self.clients),
            "global_managers_active": len(active),
            # global configuration can be changed: exactly one Global Manager answers in the active role
            "availability_pct": 100 if len(active) == 1 else 0,
            "locations_total": len(lm_sites),
            "locations_gm_connected": (sum(1 for s in lm_sites if s.get("gm_connection_up"))
                                       if gm and all(s.get("gm_connection_up") is not None for s in lm_sites) else None),
            "locations_collected": collected,
            "stretched_objects_total": self.stretched_total if gm else None,
            "stretched_objects_fully_realized": self.stretched_full if gm else None,
            "collection_duration_s": round(time.time() - started, 1),
            "api_calls": sum(c.calls for c in self.clients),
            "api_calls_throttled": sum(c.throttled for c in self.clients),
            "api_errors": sum(c.errors for c in self.clients),
            "data_gaps": len(failed),
            "split_brain_reported": self.split_brain_reported if gm else None,
            "realization_checked": self.realization_checked if gm else None,
            "stale_objects_deleted": len(self.stale_deleted),
            "managers_over_budget": self.over_budget,
        })
        self.metrics(fed, self.rollups())
        self.metrics(fed, {"switchover_ready": ready})
        self.props(fed, {
            "failed_calls": "; ".join(failed[:8]) + (f"; and {len(failed) - 8} more" if len(failed) > 8 else "") or "none",
            "federation_id": self.fed_id,
            "global_manager_errors": "; ".join(
                [f"{g['host']}: {g['error']}" for g in gms if not g["ok"]]
                + [f"{g['host']}: belongs to federation {g['federation_id']}, not to this one" for g in foreign]) or "none",
            "location_outage": outage,
            "config_version": self.config_version,
            "global_manager_addresses": ",".join(sorted(cfg.gm_hosts)),
            "vcf_operations_api": self.ops.state(),
            "stale_objects": ", ".join(self.stale_deleted[:20]) or "none",
            "active_global_manager": gm["site"].get("name") if gm else "none",
            "standby_configured": standby_known,
            "collection_mode": mode,
            "switchover_readiness": readiness,
        })
        self.publish_changes()
        if self.over_budget:
            self.event(fed, f"{self.over_budget} manager(s) used the whole API call budget; the data of this cycle is partial. "
                       "Raise the budget or narrow the collection.", Criticality.WARNING, "managers_over_budget")
        self.cache.put("counters", self.new_counters)
        self.cache.save()
        for c in self.clients:
            c.close()

    # -- change timeline
    def diff(self, cat: str, cur: Dict[str, Any], complete: bool,
             same: Any = None) -> Tuple[List[str], List[str], List[str], Dict[str, Any]]:
        """What changed in one category since the previous cycles: (added, removed, changed keys, earlier values).
        The first read of a category is the baseline and reports nothing. A read that is not complete (a call
        failed) reports no removal and keeps the earlier value of each key it did not see; a key is reported as
        added only against a complete earlier read."""
        cur = {k: v for k, v in cur.items() if v is not None}
        prev = self.change_prev.get(cat) or {}
        old = prev.get("v") or {}
        if not prev:
            self.change_next[cat] = {"c": complete, "v": cur}
            return [], [], [], {}
        self.change_next[cat] = {"c": complete or prev.get("c", False), "v": cur if complete else dict(old, **cur)}
        same = same or (lambda a, b: a == b)
        added = sorted(k for k in cur if k not in old) if prev.get("c") else []
        removed = sorted(k for k in old if k not in cur) if complete else []
        changed = sorted(k for k in cur if k in old and not same(old[k], cur[k]))
        return added, removed, changed, old

    def note(self, o: Optional[Object], message: str) -> None:
        self.changes.append((o, message))

    def track_changes(self, gm: Optional[Dict[str, Any]], lm_sites: List[Dict[str, Any]], from_gm: bool) -> None:
        """Compare what this cycle sees with the earlier cycles and note each change (see publish_changes)."""
        fed = self.fed
        # Global Managers: which one is active, the role and the version of each
        if gm:
            name = gm["site"].get("name") or gm["host"]
            _a, _r, changed, old = self.diff("active_gm", {"active": name}, True)
            if changed:
                self.note(fed, f"Active Global Manager changed: {old['active']} -> {name}")
        for cat, field, what in (("gm_mode", "mode", "role"), ("gm_version", "version", "version")):
            cur = {n: st.get(field) for n, st in self.gm_state.items()}
            _a, _r, changed, old = self.diff(cat, cur, False)
            for n in changed:
                self.note(self.gm_objects.get(n) or fed, f"Global Manager {n} {what} changed: {old[n]} -> {cur[n]}")
        # locations, as the active Global Manager lists them
        by_site = {loc.get("site_id"): loc for loc in lm_sites if loc.get("site_id")}
        loc_obj = lambda site_id: self.locations.get((by_site.get(site_id) or {}).get("path")) or fed
        if from_gm:
            cur = {k: v.get("name") for k, v in by_site.items()}
            added, removed, changed, old = self.diff("locations", cur, True)
            for k in added:
                self.note(loc_obj(k), f"Location {cur[k]} added to the federation")
            for k in removed:
                self.note(fed, f"Location {old[k]} removed from the federation")
            for k in changed:
                self.note(loc_obj(k), f"Location renamed: {old[k]} -> {cur[k]}")
            cur = {k: v.get("version") for k, v in by_site.items()}
            _a, _r, changed, old = self.diff("location_version", cur, False)
            for k in changed:
                self.note(loc_obj(k), f"Location {by_site[k].get('name')} version changed: {old[k]} -> {cur[k]}")
        # edge nodes of the RTEP edge clusters, per location
        site_of = {loc.get("path"): loc.get("site_id") for loc in lm_sites}
        for path, (edges, complete) in self.edge_seen.items():
            loc_name = self.location_names.get(path, tail(path))
            added, removed, changed, old = self.diff(f"edges@{site_of.get(path) or path}", edges, complete)
            lo = self.locations.get(path) or fed
            for k in added:
                self.note(lo, f"Edge node {edges[k]} added to the RTEP edge clusters of location {loc_name}")
            for k in removed:
                self.note(lo, f"Edge node {old[k]} removed from the RTEP edge clusters of location {loc_name}")
            for k in changed:
                self.note(lo, f"Edge node renamed at location {loc_name}: {old[k]} -> {edges[k]}")
        # global objects overridden at a location (local changes to global intent)
        for path, (found, complete) in self.overrides.items():
            loc_name = self.location_names.get(path, tail(path))
            added, removed, _c, old = self.diff(f"overrides@{site_of.get(path) or path}", found, complete)
            lo = self.locations.get(path) or fed
            for k in added:
                self.note(lo, f"{found[k]} overridden at location {loc_name} ({k})")
            for k in removed:
                self.note(lo, f"Override of {old[k]} at location {loc_name} removed ({k})")
        # stretched objects: created, changed (a new revision) or deleted on the Global Manager. The list is the
        # inventory of the Global Manager, read again every INVENTORY_TTL seconds.
        inventory = getattr(self, "inventory", None)
        if gm and inventory is not None:
            by_path = getattr(self, "stretched_by_path", {})
            cur = {o["path"]: [kind, o.get("display_name") or o.get("id"), o.get("_revision"), o.get("_last_modified_user")]
                   for kind in STRETCHED for o in inventory.get(kind, []) if o.get("path")}
            # primary location first: an object whose primary location changed gets that message, not a second
            # one for the revision the change made
            prim = {o["path"]: tail((o.get("intersite_config") or {}).get("primary_site_path"))
                    for kind in ("tier0", "tier1") for o in inventory.get(kind, []) if o.get("path")}
            _a, _r, moved, old_prim = self.diff("primary_location", {k: v for k, v in prim.items() if v}, False)
            for k in moved:
                self.note(by_path.get(k) or fed, f"Primary location of {tail(k)} changed: {old_prim[k]} -> {prim[k]}")
            added, removed, changed, old = self.diff("stretched", cur, "gm_inventory_v2" not in self.partial,
                                                     same=lambda a, b: a[2] == b[2])
            changed = [k for k in changed if k not in moved]
            notes = [(by_path.get(k), f"{MODEL[cur[k][0]][0]} {cur[k][1]} created" + (f" by {cur[k][3]}" if cur[k][3] else ""))
                     for k in added]
            notes += [(fed, f"{MODEL[old[k][0]][0]} {old[k][1]} deleted") for k in removed]
            notes += [(by_path.get(k), f"{MODEL[cur[k][0]][0]} {cur[k][1]} changed" + (f" by {cur[k][3]}" if cur[k][3] else "")
                       + f" (revision {old[k][2]} -> {cur[k][2]})") for k in changed]
            for o, message in notes:
                self.note(o or fed, message)
        # the state of a location that left the federation is not kept
        if from_gm:
            live = {f"{prefix}@{sid}" for sid in by_site for prefix in ("edges", "service_routers")}
            live |= {f"overrides@{sid}" for sid in by_site}
            for cat in [c for c in self.change_next if c.startswith(("edges@", "service_routers@", "overrides@")) and c not in live]:
                del self.change_next[cat]

    def publish_changes(self) -> None:
        """Each change is an information event on the object it concerns (unless change events are off), and the
        latest ones are listed on the federation, whose property history is then the change timeline."""
        fed = self.fed
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        recent = list(self.cache.get_any_age("recent_changes") or [])
        recent = [f"{stamp}: {m}" for _o, m in reversed(self.changes)] + recent
        recent = recent[:10]
        self.cache.put("recent_changes", recent)
        self.cache.put("change_state", self.change_next)
        self.metrics(fed, {"changes_detected": len(self.changes)})
        if recent:  # with an empty cache VCF Operations keeps the list it has
            self.props(fed, {"last_change": recent[0], "recent_changes": " | ".join(recent)})
        if not self.cfg.change_events:
            return
        cap = 25
        for o, message in self.changes[:cap]:
            (o or fed).with_event(message=message, criticality=Criticality.INFO, cancel_wait_cycle=1)
        if len(self.changes) > cap:
            fed.with_event(message=f"{len(self.changes) - cap} more changes in this cycle; see 'Recent changes'",
                           criticality=Criticality.INFO, cancel_wait_cycle=1)

    def finish_tier0(self, lm_sites: List[Dict[str, Any]]) -> None:
        """Service routers of each stretched Tier-0 gateway, summed over its span, and their HA state changes."""
        by_path = getattr(self, "stretched_by_path", {})
        ha_modes = {o.get("path"): str(o.get("ha_mode") or "").upper() for o in getattr(self, "inventory", {}).get("tier0", [])}
        site_of = {loc.get("path"): loc.get("site_id") for loc in lm_sites}
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
        absent_since: Dict[str, float] = dict(self.cache.get_any_age("sr_absent_since") or {})
        ha_changes: Dict[str, int] = {}
        for site_path, recs in self.sr_records.items():
            cat = f"service_routers@{site_of.get(site_path) or site_path}"
            # A service router seen before on an edge that answered, but missing from its answer now (edge rebooting,
            # datapath down), is ABSENT, so ACTIVE -> missing -> STANDBY is still seen as a change. Only while its
            # Tier-0 exists, and for a day at most (the Tier-0 may have moved to other edges).
            seen = {r["key"] for r in recs}
            answered = self.edge_answered.get(site_path, set())
            for key, ha in ((self.change_prev.get(cat) or {}).get("v") or {}).items():
                path, _, tn_id = key[3:].rpartition(":")
                if key in seen or tn_id not in answered or path not in ha_modes:
                    continue
                first = absent_since.setdefault(key, now)
                if now - first > 86400:
                    continue
                no = self.edge_objects.get(tn_id)
                edge = no.get_key().name.rsplit(" (", 1)[0] if no is not None else tn_id
                loc_name = self.location_names.get(site_path, tail(site_path))
                so = self.obj("tier0_sr", f"{tail(path)} on {edge} ({loc_name})", key)
                self.at(so, site_path)
                if no is not None:
                    so.add_parent(no)
                if path in by_path:
                    so.add_parent(by_path[path])
                self.metrics(so, {"active": 0, "routing_up": 0})
                self.props(so, {"ha_status": "ABSENT", "routing_status": "not reported by the edge"})
                recs.append({"path": path, "key": key, "ha": "ABSENT", "routing": "ABSENT", "edge": edge,
                             "location": loc_name, "obj": so})
            for r in recs:
                if r["ha"] != "ABSENT":
                    absent_since.pop(r["key"], None)
            _a, _r, changed, old = self.diff(cat, {r["key"]: r["ha"] for r in recs}, self.sr_complete.get(site_path, False))
            for r in recs:
                if r["key"] in changed:
                    self.note(r["obj"], f"Tier-0 {tail(r['path'])} service router on {r['edge']} at {r['location']}: "
                                        f"HA state {old[r['key']]} -> {r['ha']}")
                    self.props(r["obj"], {"last_ha_change": f"{stamp}: {old[r['key']]} -> {r['ha']}"})
                    ha_changes[r["path"]] = ha_changes.get(r["path"], 0) + 1
        self.cache.put("sr_absent_since", {k: v for k, v in absent_since.items() if now - v <= 2 * 86400})
        for path, t0 in by_path.items():
            if path not in ha_modes:
                continue  # Tier-1 gateways, segments, policies
            # the changes seen are sent in every cycle, also when an edge did not answer: that is when a failover happens
            self.metrics(t0, {"sr_ha_changes": ha_changes.get(path, 0)})
            # the span NSX reports; when it is unknown, the locations where the gateway has service routers
            span = self.span_of.get(path) or {p for p, recs in self.sr_records.items() if any(r["path"] == path for r in recs)}
            complete = all(self.sr_complete.get(p, False) for p in span)
            mine = [r for p in span for r in self.sr_records.get(p, []) if r["path"] == path]
            aa = ha_modes[path] == "ACTIVE_ACTIVE"
            # active-active: each location of the span has its own active service routers. Active-standby: the
            # federation needs one active service router, at the location NSX chooses. A location counts only once
            # all its edges answered, so a partial read can show a location without one, never hide it.
            without = sorted(self.location_names.get(p, tail(p)) for p in span if self.sr_complete.get(p, False)
                             and not any(r["ha"] == "ACTIVE" for r in self.sr_records.get(p, []) if r["path"] == path))
            if aa and (complete or without):
                self.metrics(t0, {"locations_without_active_sr": len(without)})
                self.props(t0, {"no_active_sr_at": ", ".join(without) or "none"})
            elif not aa:
                self.props(t0, {"no_active_sr_at": "not applicable (active-standby)"})
            if not complete or not mine:
                continue  # an edge of the span did not answer, or no service router yet: the totals would be wrong
            active = [r for r in mine if r["ha"] == "ACTIVE"]
            self.metrics(t0, {"sr_total": len(mine), "sr_active": len(active),
                              "sr_down": sum(1 for r in mine if r["routing"] and r["routing"] != "UP"),
                              # active-active: an active service router at every location of the span
                              "availability_pct": 100 if active and not (aa and without) else 0})
            self.props(t0, {"active_service_routers": ", ".join(sorted(f"{r['edge']} ({r['location']})" for r in active)) or "none"})

    def switchover_readiness(self, standby_known: Optional[bool], active_version: Optional[str]) -> Tuple[int, str]:
        """Can a standby Global Manager take over now? The metric is 1 only when that is confirmed: nothing
        stands in the way and every value behind it was read. Otherwise it is 0, and the property says why:
        "not ready: <reasons>", "unknown: <what could not be read>" or "not applicable (...)". The value is sent
        in every cycle, so an earlier 1 never stays on a manager that changed role. The federation reads 1 when one
        standby is ready. The reasons are the ones NSX checks before a switchover, or that make it fail."""
        sync = {n: ok for n, ok, _r, _a in self.standby_sync}
        results: Dict[str, Tuple[int, str]] = {}
        for name, go in self.gm_objects.items():
            st = self.gm_state.get(name)
            text = None
            if name == self.active_name:
                text = "not applicable (active)"
            elif st is not None and st["mode"] != "STANDBY" and name not in sync:
                text = f"not applicable (mode {st['mode']})"
            elif st is None and name not in self.gm_answered and self.sync_read and name not in sync:
                # not reachable, and the active does not list it as a standby (a former active, or removed)
                text = "not applicable (not a standby of the active Global Manager)"
            if text:
                self.metrics(go, {"switchover_ready": 0})
                self.props(go, {"switchover_readiness": text})
                continue
            blockers: List[str] = []
            unknown: List[str] = []
            if st is None and name in self.gm_answered:
                unknown.append("the data of this cycle is incomplete")
            elif st is None:
                # the collector does not reach it; its own state is unknown, the path between the managers may be fine
                unknown.append("not reachable by the collector, so its cluster state is unknown")
            else:
                if st["cluster"] is None:
                    unknown.append("cluster status not read")
                elif st["cluster"] != "STABLE":
                    blockers.append(f"cluster status {st['cluster']}")
                if st["offline"]:
                    blockers.append(f"{st['offline']} node(s) offline")
                if st["degraded"]:
                    blockers.append(f"{st['degraded']} service group(s) not stable")
                if st["version"] and active_version and st["version"] != active_version:
                    blockers.append(f"runs {st['version']}, the active Global Manager runs {active_version}")
                if st["cert_days"] is not None and st["cert_days"] <= 0:
                    blockers.append("a certificate has expired")
                if str(st["upgrade"] or "").upper() in ("IN_PROGRESS", "PAUSED", "PAUSING"):
                    blockers.append(f"upgrade {str(st['upgrade']).lower().replace('_', ' ')}")
            if not self.active_name:
                unknown.append("no active Global Manager reports the sync state")
            elif name in sync:
                if not sync[name]:
                    blockers.append("not in sync with the active Global Manager")
            elif not self.sync_read:
                unknown.append("sync state not read")
            else:
                unknown.append("the active Global Manager reports no sync state for it")
            ready = 0 if blockers or unknown else 1
            text = ("not ready: " + "; ".join(blockers)) if blockers else ("unknown: " + "; ".join(unknown)) if unknown else "ready"
            self.metrics(go, {"switchover_ready": ready})
            self.props(go, {"switchover_readiness": text})
            results[name] = (ready, text)
        if standby_known is False:
            return 0, "not ready: no standby Global Manager is configured"
        if not results:
            if standby_known:
                return 0, "unknown: the standby Global Manager is not monitored by this account (add its address)"
            return 0, "unknown: no standby Global Manager was read"
        fed_ready = 1 if any(r == 1 for r, _t in results.values()) else 0
        return fed_ready, "; ".join(f"{n}: {t}" for n, (_r, t) in sorted(results.items()))

    @property
    def over_budget(self) -> int:
        """Managers that ran out of calls in this cycle, each counted once."""
        return sum(1 for c in self.clients if c.budget_hit)

    def at(self, o: Object, *site_paths: Any) -> None:
        """Record the locations (site paths) an object depends on, for the outage property."""
        self.sites_of.setdefault(id(o), set()).update(p for p in site_paths if p)

    def remember_site(self, name: Any, site_id: Any, overwrite: bool = True) -> None:
        if name and site_id and self.site_ids.get(name) != site_id and (overwrite or name not in self.site_ids):
            self.site_ids[name] = str(site_id)
            self.cache.put("site_ids", self.site_ids)

    def path_of(self, location_name: Any) -> Optional[str]:
        return next((p for p, n in self.location_names.items() if n == location_name), None)

    def cached_clean(self, client: NsxClient, key: str, max_age_s: float, loader: Any) -> Any:
        """Like Cache.cached, for a loader made of several calls: a result with a failed call in it is not
        cached, and the last complete result is used for this cycle when there is one."""
        value = self.cache.get(key, max_age_s)
        if value is None:
            before = len(client.failed)
            value = loader()
            if len(client.failed) == before:
                self.cache.put(key, value)
            else:
                older = self.cache.get_any_age(key)
                if older is None:
                    self.partial.add(key)  # a partial read, and no complete one to fall back on
                value = older or value
        return value

    def gm_sites_from_inventory(self) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """Address -> (name, site id) of the Global Managers of this federation that VCF Operations holds."""
        out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        for res in self.ops.resources("global_manager"):
            ident = next((i.get("value") for i in res["resourceKey"].get("resourceIdentifiers", [])
                          if (i.get("identifierType") or {}).get("name") == "id"), "")
            if not ident.startswith(self.fed_id + ":gm:"):
                continue
            address = self.ops.properties(res["identifier"]).get("address")
            if address:
                out[address] = (res["resourceKey"].get("name"), ident.split(":gm:", 1)[1])
        return out

    def from_inventory(self) -> Dict[str, Any]:
        """Federation ID and location list of this account as VCF Operations stored them in earlier cycles. Used
        when no Global Manager answers and the container cache is empty (the container was restarted)."""
        mine = ",".join(sorted(self.cfg.gm_hosts))
        for fed in self.ops.resources("federation"):
            if self.ops.properties(fed["identifier"]).get("global_manager_addresses") != mine:
                continue
            fed_id = next((i.get("value") for i in fed["resourceKey"].get("resourceIdentifiers", [])
                           if (i.get("identifierType") or {}).get("name") == "id"), "")
            locations = []
            for lo in self.ops.resources("location"):
                ident = next((i.get("value") for i in lo["resourceKey"].get("resourceIdentifiers", [])
                              if (i.get("identifierType") or {}).get("name") == "id"), "")
                if not fed_id or not ident.startswith(fed_id + ":site:"):
                    continue
                props = self.ops.properties(lo["identifier"])
                if props.get("site_path") and props.get("local_manager_address"):
                    locations.append({"name": lo["resourceKey"].get("name"), "site_id": ident.split(":site:", 1)[1],
                                      "path": props["site_path"], "address": props["local_manager_address"],
                                      "thumbprint": props.get("lm_thumbprint"), "version": props.get("version")})
            return {"federation_id": fed_id, "locations": locations}
        return {}

    def delete_stale(self) -> None:
        """Opt-in: delete the objects of this federation that NSX no longer returns and that have had no data for
        the configured number of days. Runs once an hour, in a complete cycle only, at most 50 objects at a time."""
        days = self.cfg.delete_stale_days
        if days <= 0 or self.cache.get("stale_checked", SLOW_TTL):
            return
        self.cache.put("stale_checked", True)
        current = {i.value for o in self.result.objects.values() if o.adapter_type() == ADAPTER_KIND
                   for i in o.get_key().identifiers.values() if i.key == "id"}
        limit = (time.time() - days * 86400) * 1000
        for kind in MODEL:
            for res in self.ops.resources(kind):
                ident = next((i.get("value") for i in res["resourceKey"].get("resourceIdentifiers", [])
                              if (i.get("identifierType") or {}).get("name") == "id"), "")
                if not ident.startswith(self.fed_id + ":") or ident in current or len(self.stale_deleted) >= 50:
                    continue
                latest = self.ops.get(f"/api/resources/{res['identifier']}/stats/latest")
                if latest is None:
                    continue
                stamps = [ts for v in latest.get("values", []) for s in (v.get("stat-list") or {}).get("stat", [])
                          if not str((s.get("statKey") or {}).get("key", "")).startswith(("badge|", "System Attributes|"))
                          for ts in s.get("timestamps", [])]
                newest = max(stamps, default=res.get("creationTime", 0))
                if newest < limit and self.ops.delete(f"/api/resources/{res['identifier']}"):
                    self.stale_deleted.append(f"{kind}: {res['resourceKey'].get('name')}")

    def rollups(self) -> Dict[str, Any]:
        """Federation-wide figures computed from the objects of this cycle, so a dashboard or an alert needs
        one number for the whole federation (what a super metric would do, without a policy to enable)."""
        # CollectResult.get_objects_by_type compares the adapter type with the object type (SDK 1.x), so filter here
        get = lambda kind: [o for o in self.result.objects.values()
                            if o.adapter_type() == ADAPTER_KIND and o.object_type() == kind]
        val = lambda o, k: o.get_last_metric_value(fk(o.object_type(), k))
        locs = get("location")
        healthy = [o for o in locs if val(o, "gm_connection_up") != 0 and val(o, "collector_reachable") == 1
                   and not val(o, "intersite_bgp_down") and not val(o, "nodes_offline") and not val(o, "full_sync_failed")]
        for o in locs:
            self.metrics(o, {"healthy": int(o in healthy), "availability_pct": 100 if o in healthy else 0})
        # availability: the average of these 0/100 values over a period is the share of the time a manager was up
        for o in get("global_manager"):
            self.metrics(o, {"availability_pct": 100 if val(o, "reachable") == 1 and not val(o, "nodes_offline") else 0})
        pw = [val(o, "password_days_left") for o in get("global_manager") + locs]
        pw = [x for x in pw if x is not None]
        tunnels = get("rtep_tunnel")
        drops = [val(o, k) for o in tunnels for k in ("rx_dropped_per_min", "tx_dropped_per_min")]
        drops = [d for d in drops if d is not None]
        certs = [val(o, "cert_min_days_to_expiry") for o in get("global_manager") + locs]
        certs = [c for c in certs if c is not None]
        crit = [val(o, "alarms_open_critical") for o in get("global_manager") + locs]
        skews = [val(o, "clock_skew_to_gm_s") for o in locs] + [val(o, "clock_skew_to_active_s") for o in get("global_manager")]
        skews = [x for x in skews if x is not None]
        caps = [val(o, "capacity_max_pct") for o in locs]
        caps = [x for x in caps if x is not None]
        overridden = [val(o, "overridden_objects") for o in locs]
        return {
            "clock_max_skew_s": max(skews) if skews else None,
            "password_min_days_left": min(pw) if pw else None,
            "rules_to_review": getattr(self, "rules_to_review", None),
            "capacity_max_pct": max(caps) if caps else None,
            # a sum over the locations; unknown when the Local Manager of one did not answer in this cycle
            "overridden_objects": sum(overridden) if overridden and None not in overridden else None,
            "locations_healthy": len(healthy),
            "locations_healthy_pct": round(100.0 * len(healthy) / len(locs), 1) if locs else None,
            "rtep_tunnels_total": len(tunnels),
            "rtep_tunnels_bgp_down": sum(1 for o in tunnels if val(o, "bgp_established") == 0),
            "rtep_dropped_per_min": round(sum(drops), 2) if drops else None,
            "cert_min_days_to_expiry": min(certs) if certs else None,
            "alarms_open_critical": sum(c for c in crit if c is not None),
        }

    # -- active Global Manager: sites, global objects, realization
    def collect_active_gm(self, gm: Dict[str, Any]) -> Dict[str, Any]:
        client: NsxClient = gm["client"]
        fed_sites = client.get("/api/v1/sites").get("sites", [])
        for s in fed_sites:  # every Global Manager and location, also a standby that the account does not list
            self.remember_site(s.get("name"), s.get("id"), overwrite=False)
        self.split_brain_reported = int(any(s.get("split_brain") for s in fed_sites))
        self.config_version = next((s.get("config_version") for s in fed_sites if s.get("is_local")), None)
        status = client.try_get("/api/v1/sites/status", default={}) or {}
        status_ok = client.ok("/api/v1/sites/status")
        conn = {rc.get("site_name"): rc for rc in status.get("remote_connections", [])}
        policy_sites = client.list(f"{GI}/sites")
        rtep = {tail(sc.get("site_path")): sc.get("rtep_ips", [])
                for sc in (client.try_get(f"{GI}/federation-config", default={}) or {}).get("site_config", [])}
        gms = client.try_list(f"{GI}/global-managers")
        self.gm_list_read = client.ok(f"{GI}/global-managers")
        versions = {s.get("id"): s.get("site_version") for s in fed_sites}
        gm_version = gm["site"].get("site_version")

        locations = []
        for ps in policy_sites:
            name = ps.get("display_name") or ps.get("id")
            info = (ps.get("site_connection_info") or [{}])[0]
            rc = conn.get(name) or conn.get(ps.get("id")) or {}
            stubs = rc.get("stubs", [])
            locations.append({
                "name": name, "policy_id": ps.get("id"), "path": ps.get("path"),
                "site_id": ps.get("unique_id") or ps.get("id"),
                "address": self.cfg.lm_overrides.get(name) or info.get("fqdn"),
                "thumbprint": info.get("thumbprint"),
                "maximum_rtt": ps.get("maximum_rtt"),
                "version": versions.get(ps.get("unique_id")),
                "gm_version": gm_version,
                "rtep_ips": rtep.get(ps.get("id"), []),
                # None = unknown: the status call failed, so nothing is said about the connection
                "gm_connection_up": any(s.get("connection_up") for s in stubs) if status_ok and stubs else None,
            })
        doc = {"locations": locations,
               "global_managers": [{"id": g.get("id"), "mode": g.get("mode")} for g in gms]}

        # latency and sync queues of every flow, one call for all the locations. An NSX without this API answers
        # 404: the round-trip time then comes from the replication status, as before.
        flow_doc = client.try_get(FLOWS, default={}) or {}
        flows = {} if client.absent(FLOWS) or not client.ok(FLOWS) else flow_summary(flow_doc)

        # Location objects exist as soon as the Global Manager lists them.
        for loc in locations:
            lo = self.obj("location", loc["name"], f"site:{loc['site_id']}")
            self.at(lo, loc["path"])
            self.locations[loc["path"]] = lo
            self.location_names[loc["path"]] = loc["name"]
            self.remember_site(loc["name"], loc["site_id"])
            lo.add_parent(self.fed)
            self.metrics(lo, {"gm_connection_up": None if loc["gm_connection_up"] is None else int(loc["gm_connection_up"])})
            self.props(lo, {
                "site_id": loc["site_id"], "version": loc["version"], "local_manager_address": loc["address"],
                "rtep_ips": ", ".join(loc["rtep_ips"]), "maximum_rtt_ms": loc["maximum_rtt"],
                "version_matches_gm": same_release(loc["version"], gm_version) if loc["version"] and gm_version else None,
                "release_newer_than_gm": (release_tuple(loc["version"]) > release_tuple(gm_version))
                if loc["version"] and gm_version else None,
                "site_path": loc["path"], "lm_thumbprint": loc["thumbprint"],
            })
            self.replication(client, lo, loc, flows.get(loc["path"]))
            if loc["gm_connection_up"] is False:
                self.down_locations.add(loc["path"])
                self.event(lo, f"Location {loc['name']} is not connected to the active Global Manager",
                           Criticality.CRITICAL, "gm_connection_up")
            if loc["version"] and gm_version and not same_release(loc["version"], gm_version):
                self.event(lo, f"Version mismatch: location {loc['name']} runs {loc['version']}, the Global Manager "
                           f"runs {gm_version}. Expected only during an upgrade.", Criticality.INFO, "version_matches_gm")

        try:  # a fault here must not cost the location list: the Local Managers are collected from it
            self.collect_stretched(client)
        except BudgetExceeded:
            pass  # counted on the client
        except Exception as e:
            logger.exception(e)
            self.event(self.fed, f"Collection of the stretched objects failed: {repr(e)[:200]}", Criticality.IMMEDIATE)
        return doc

    def collect_stretched(self, client: NsxClient) -> None:
        inventory = self.cached_clean(client, "gm_inventory_v2", INVENTORY_TTL, lambda: self.load_gm_inventory(client))
        items: List[Tuple[str, dict]] = []
        for kind in STRETCHED:
            items.extend((kind, o) for o in inventory.get(kind, []))

        # Above REALIZATION_EVERY_CYCLE objects, one call per object and cycle does not scale. The state of an
        # object is then read again when it is new, changed in NSX, was not fully realized, or on its turn in a
        # sweep that covers every object in REALIZATION_SWEEP_CYCLES cycles. Otherwise the last state is used.
        known: Dict[str, Any] = self.cache.get_any_age("realization") or {}
        cycle = int(self.cache.get_any_age("realization_cycle") or 0) + 1
        self.cache.put("realization_cycle", cycle)
        tiered = len(items) > REALIZATION_EVERY_CYCLE

        def due(index: int, o: dict) -> bool:
            prev = known.get(o["path"])
            return (not tiered or prev is None or prev.get("m") != o.get("_last_modified_time") or not prev.get("ok")
                    or index % REALIZATION_SWEEP_CYCLES == cycle % REALIZATION_SWEEP_CYCLES)

        def realization(item: Tuple[int, Tuple[str, dict]]) -> Tuple[str, dict, Optional[dict]]:
            index, (kind, o) = item
            if not due(index, o):
                return kind, o, known[o["path"]]["st"]
            if client.calls >= client.call_budget - GM_RESERVED_CALLS:
                client.budget_hit = True
                return kind, o, {"_budget": True}
            try:
                raw = client.get(f"{GI}/realized-state/status", {"intent_path": o["path"]})
            except BudgetExceeded:
                return kind, o, {"_budget": True}
            except Exception as e:
                client._failed(f"{GI}/realized-state/status?intent_path={o['path']}",
                               f"HTTP {e.status}" if isinstance(e, NsxError) else type(e).__name__)
                return kind, o, None
            st = {"consolidated_status": consolidated(raw.get("consolidated_status")),
                  "consolidated_status_per_enforcement_point": [
                      {"site_path": ep.get("site_path"), "consolidated_status": consolidated(ep.get("consolidated_status"))}
                      for ep in raw.get("consolidated_status_per_enforcement_point") or []], "_fresh": True}
            return kind, o, st

        with ThreadPoolExecutor(max_workers=8) as pool:
            realized = list(pool.map(realization, enumerate(items)))
        fresh: Dict[str, Any] = {}
        for _kind, o, st in realized:
            if st and not st.get("_budget"):
                eps = st.get("consolidated_status_per_enforcement_point") or []
                fresh[o["path"]] = {"m": o.get("_last_modified_time"), "st": {k: v for k, v in st.items() if k != "_fresh"},
                                    "ok": bool(eps) and all(e.get("consolidated_status") == "SUCCESS" for e in eps)}
                self.realization_checked += 1 if st.get("_fresh") else 0
        self.cache.put("realization", fresh)

        by_path: Dict[str, Object] = {}
        budget_hit = False
        for kind, o, st in realized:
            so = self.obj(kind, o.get("display_name") or o["id"], f"{kind}:{o['path']}")
            self.at(so, *[ep.get("site_path") for ep in (st or {}).get("consolidated_status_per_enforcement_point") or []])
            by_path[o["path"]] = so
            so.add_parent(self.fed)
            self.props(so, {
                "path": o["path"], "revision": o.get("_revision"),
                "last_modified_user": o.get("_last_modified_user"),
                "last_modified_time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(o["_last_modified_time"]) / 1000.0))
                if o.get("_last_modified_time") else None,
            })
            if kind == "tier0":
                self.props(so, {"ha_mode": o.get("ha_mode"),
                                "primary_location": tail((o.get("intersite_config") or {}).get("primary_site_path")) or "all (active-active)"})
            elif kind == "tier1":
                self.props(so, {"tier0": tail(o.get("tier0_path")),
                                "primary_location": tail((o.get("intersite_config") or {}).get("primary_site_path")) or "follows Tier-0"})
            elif kind == "segment":
                self.props(so, {"subnets": ", ".join(s.get("network", "") for s in o.get("subnets") or []),
                                "gateway": tail(o.get("connectivity_path")), "segment_type": o.get("type")})
            elif kind == "global_policy":
                rules = o.get("rules")
                self.props(so, {"category": o.get("category"), "domain": tail(o.get("parent_path")),
                                "scope": ", ".join(tail(s) for s in o.get("scope") or [])})
                if rules is not None:
                    self.metrics(so, {"rules_total": o.get("rule_count", len(rules)),
                                      "rules_disabled": sum(1 for r in rules if r.get("disabled"))})
            if st and st.get("_budget"):
                budget_hit = True
                continue
            if not st:
                self.props(so, {"status": "UNKNOWN"})
                continue
            per_ep = st.get("consolidated_status_per_enforcement_point") or []
            span = {ep.get("site_path") for ep in per_ep if ep.get("site_path")}
            if span:  # an object not realized anywhere yet has no known span: statistics are read everywhere
                self.span_of[o["path"]] = span
            ok = err = 0
            for ep in per_ep:
                site_path = ep.get("site_path") or ""
                state = consolidated(ep.get("consolidated_status"))
                site_name = self.location_names.get(site_path, tail(site_path))
                ro = self.obj("realization", f"{o.get('display_name') or o['id']} @ {site_name}",
                              f"real:{o['path']}@{site_path}")
                self.at(ro, site_path)
                ro.add_parent(so)
                if site_path in self.locations:
                    ro.add_parent(self.locations[site_path])
                self.props(ro, {"status": state, "location": site_name, "object_path": o["path"], "object_type": kind})
                self.metrics(ro, {"realized": 1 if state == "SUCCESS" else 0})
                if state == "SUCCESS":
                    ok += 1
                elif state in ("ERROR", "FAILED", "DEGRADED", "UNINITIALIZED", "UNKNOWN"):
                    err += 1
                    if site_path not in self.down_locations:  # storm suppression: the location alert is the root cause
                        self.event(ro, f"{MODEL[kind][0]} {o.get('display_name')} is not realized at {site_name}: {state}",
                                   Criticality.IMMEDIATE, "realized")
            self.props(so, {"status": consolidated(st.get("consolidated_status"))})
            self.metrics(so, {"span_locations": len(per_ep), "realized_locations": ok, "error_locations": err,
                              "unrealized_locations": len(per_ep) - ok})
            self.stretched_total += 1
            if per_ep and ok == len(per_ep):
                self.stretched_full += 1
        # parent links between stretched objects
        for kind, o, _ in realized:
            parent_path = o.get("tier0_path") if kind == "tier1" else o.get("connectivity_path") if kind == "segment" else None
            if parent_path and parent_path in by_path:
                by_path[o["path"]].add_parent(by_path[parent_path])
        self.stretched_by_path = by_path
        self.inventory = inventory

    def load_gm_inventory(self, client: NsxClient) -> Dict[str, List[dict]]:
        keep = ("id", "display_name", "path", "parent_path", "_revision", "_last_modified_user", "_last_modified_time",
                "ha_mode", "intersite_config", "tier0_path", "connectivity_path", "subnets", "type", "category", "scope")
        slim = lambda o: {k: o[k] for k in keep if k in o}
        inv: Dict[str, List[dict]] = {
            "tier0": [slim(o) for o in client.try_list(f"{GI}/tier-0s")],
            "tier1": [slim(o) for o in client.try_list(f"{GI}/tier-1s")],
            "segment": [slim(o) for o in client.try_list(f"{GI}/segments")],
            "global_policy": [],
        }
        for domain in client.try_list(f"{GI}/domains"):
            for pol in client.try_list(f"{GI}/domains/{domain['id']}/security-policies"):
                if pol.get("_system_owned") or str(pol.get("id", "")).startswith("default-"):
                    continue  # system defaults are not user intent
                entry = slim(pol)
                detail = f"{GI}/domains/{domain['id']}/security-policies/{pol['id']}"
                if client.calls >= client.call_budget - GM_RESERVED_CALLS:  # keep calls for the manager itself
                    client.budget_hit = True
                    client._failed(detail, "call budget")
                    full: Dict[str, Any] = {}
                else:
                    full = client.try_get(detail, default={}) or {}
                # None: the rules are unknown (failed call, budget), not an empty policy
                entry["rules"] = [{k: r.get(k) for k in RULE_FIELDS} for r in full.get("rules") or []] \
                    if client.ok(detail) else None
                entry["domain_id"] = domain["id"]
                inv["global_policy"].append(entry)
        return inv

    # -- Global Manager object (active or standby)
    def collect_gm_object(self, g: Dict[str, Any], is_active: bool) -> str:
        client: NsxClient = g["client"]
        site = g["site"]
        name = site.get("name") or g["host"]
        go = self.obj("global_manager", name, f"gm:{site.get('id')}")
        self.gm_objects[name] = go
        self.gm_answered.add(name)
        go.add_parent(self.fed)
        cs = client.try_get("/api/v1/cluster/status", default={}) or {}
        mcs = cs.get("mgmt_cluster_status") or {}
        groups = (cs.get("detailed_cluster_status") or {}).get("groups") or []
        gm_alarms = client.try_list("/api/v1/alarms", {"status": "OPEN"})
        alarms = summarize_alarms(gm_alarms) if client.ok("/api/v1/alarms") else dict.fromkeys(("critical", "high", "other"))
        cs_ok = client.ok("/api/v1/cluster/status")
        b_enabled, b_age, b_result, c_days, c_name = self.backup_and_certs(client)
        mode = str(site.get("active_gm", "UNKNOWN")).upper()
        self.metrics(go, {
            "reachable": 1,
            "nodes_online": len(mcs.get("online_nodes") or []) if cs_ok else None,
            "nodes_offline": len(mcs.get("offline_nodes") or []) if cs_ok else None,
            "service_groups_degraded": sum(1 for x in groups if x.get("group_status") != "STABLE") if cs_ok else None,
            "alarms_open_critical": alarms["critical"], "alarms_open_high": alarms["high"], "alarms_open_other": alarms["other"],
            "backup_age_hours": b_age, "cert_min_days_to_expiry": c_days,
        })
        self.props(go, {
            "mode": mode, "cluster_status": mcs.get("status"), "version": site.get("site_version"), "address": g["host"],
            "backup_enabled": b_enabled, "backup_last_result": b_result, "cert_first_to_expire": c_name,
            "certificate_trust": trust_text(client),
        })
        up = self.upgrade_state(client)
        self.props(go, {"upgrade_status": up.get("upgrade_status"), "upgrade_target_version": up.get("target_version") or "none"})
        self.read_clock(client, "gm:" + name, go)
        if cs_ok:
            self.password_expiry(client, self.cfg.user, go)
        self.gm_state[name] = {
            "mode": mode, "version": site.get("site_version"), "cert_days": c_days, "upgrade": up.get("upgrade_status"),
            "cluster": mcs.get("status") if cs_ok else None,
            "offline": len(mcs.get("offline_nodes") or []) if cs_ok else None,
            "degraded": sum(1 for x in groups if x.get("group_status") != "STABLE") if cs_ok else None}
        for node in mcs.get("online_nodes") or []:
            self.node(go, node, 1)
        for node in mcs.get("offline_nodes") or []:
            self.node(go, node, 0)
        if not is_active:  # values the active Global Manager reports; a former active must not keep its own
            role = "not applicable (standby)" if mode == "STANDBY" else f"not reported (mode {mode})"
            self.props(go, {"switchover_status": role, "standby_sync_status": role, "standby_data_consistent": role})
            self.metrics(go, {"standby_sync_entries": 0})
        if is_active:
            so = client.try_get("/api/v1/sites/switchover-status", default={}) or {}
            st = client.try_get("/api/v1/sites/status", default={}) or {}
            sync = st.get("active_standby_sync_statuses") or []
            self.props(go, {"switchover_status": so.get("overall_status"),
                            "standby_sync_status": ("; ".join(
                                f"{s.get('standby_site', '?')}: {s.get('status', '?')}" for s in sync) or "no standby")
                            if client.ok("/api/v1/sites/status") else None})
            if client.ok("/api/v1/sites/status"):
                self.metrics(go, {"standby_sync_entries": len(sync)})
            # NSX 9.1 steady state: status ONGOING, sync_type DELTA_SYNC, full sync COMPLETE. A standby is out of
            # sync when its full sync is not complete or a status reports a failure.
            def in_sync(s: Dict[str, Any]) -> bool:
                state = str(s.get("status", "")).upper()
                full = str((s.get("full_sync_status") or {}).get("status", "")).upper()
                if "FAIL" in state or "ERROR" in state or "FAIL" in full or "ERROR" in full:
                    return False
                return state in ("SUCCESS", "SYNCED", "IN_SYNC") or (
                    state in ("ONGOING", "SYNCHRONIZING", "IN_PROGRESS")
                    and str(s.get("sync_type", "")).upper() == "DELTA_SYNC" and full == "COMPLETE")
            self.active_name, self.sync_read = name, client.ok("/api/v1/sites/status")
            if not sync and client.ok("/api/v1/sites/status"):
                self.metrics(go, {"standby_in_sync": 1, "standby_sync_remaining": 0, "standby_out_of_sync_minutes": 0})
                self.cache.put("standby_out_of_sync_since", {})
            if sync:
                self.metrics(go, {"standby_in_sync": int(all(in_sync(s) for s in sync)),
                                  "standby_sync_remaining": sum(int(s.get("remaining_entries_to_send") or 0) for s in sync)})
                # the same values on each standby object, set once all Global Manager objects exist
                full_done = lambda s: epoch_seconds((s.get("full_sync_status") or {}).get("completed_at"))
                self.standby_sync = [(s.get("standby_site"), int(in_sync(s)), int(s.get("remaining_entries_to_send") or 0),
                                      round((time.time() - full_done(s)) / 3600.0, 1) if full_done(s) else None)
                                     for s in sync]
                # how long a standby has been out of sync, and when its last full sync completed
                since = self.cache.get_any_age("standby_out_of_sync_since") or {}
                now = time.time()
                since = {n: since.get(n, now) for n, ok, _r, _a in self.standby_sync if not ok}
                self.cache.put("standby_out_of_sync_since", since)
                self.standby_since = {n: round((now - v) / 60.0, 1) for n, v in since.items()}
                ages = [now - e for e in (epoch_seconds((s.get("full_sync_status") or {}).get("completed_at")) for s in sync) if e]
                self.metrics(go, {
                    "standby_out_of_sync_minutes": round(max((now - v for v in since.values()), default=0.0) / 60.0, 1),
                    "standby_full_sync_age_hours": round(max(ages) / 3600.0, 1) if ages else None})
                self.props(go, {"standby_data_consistent": "; ".join(
                    f"{s.get('standby_site', '?')}: {s.get('is_data_consistent')}" for s in sync)})
            # sync channels as seen by the active Global Manager
            for rc in st.get("remote_connections", []):
                self.channel(go, name, rc)
        if mcs.get("status") and mcs.get("status") != "STABLE":
            self.event(go, f"Global Manager {name} cluster status is {mcs.get('status')}", Criticality.CRITICAL, "cluster_status")
        if is_active:
            if b_enabled is None:
                pass  # backup state unknown in this cycle
            elif not b_enabled:
                self.event(go, f"Backup is not configured on Global Manager {name}", Criticality.IMMEDIATE, "backup_enabled")
            elif b_age is None or b_age > self.cfg.backup_max_age_h or b_result == "FAILED":
                self.event(go, f"Global Manager {name}: last successful backup is "
                           f"{'missing' if b_age is None else str(b_age) + ' h old'} (last result {b_result})",
                           Criticality.IMMEDIATE, "backup_age_hours")
        if c_days is not None and c_days < self.cfg.cert_warning_days:
            self.event(go, f"Certificate '{c_name}' on Global Manager {name} expires in {c_days} days",
                       Criticality.CRITICAL if c_days < 7 else Criticality.WARNING, "cert_min_days_to_expiry")
        # alarms of the Global Manager itself: the federation ones and every critical one, forwarded once each (capped)
        wanted = [a for a in gm_alarms if str(a.get("feature_name", "")).lower() == "federation"
                  or str(a.get("severity", "")).upper() == "CRITICAL"][:20]
        for a in wanted:
            sev = str(a.get("severity", "")).upper()
            self.event(go, f"NSX alarm {a.get('event_type')}: {a.get('summary', '')}"[:400],
                       Criticality.CRITICAL if sev == "CRITICAL" else Criticality.IMMEDIATE if sev == "HIGH" else Criticality.WARNING)
        return name

    def replication(self, client: NsxClient, lo: Object, loc: Dict[str, Any], flow: Optional[Dict[str, Any]] = None) -> None:
        """What the Location Manager page of NSX shows for a location: the state of each replication flow, the
        round-trip time when NSX reports one, when the location was last seen, and the onboarding status."""
        path = f"/api/v1/federation/replication/gm/sites/{loc['site_id']}/status"
        rep = client.try_get(path, default={}) or {}
        if client.ok(path):
            flows = rep.get("flow_statuses") or []
            bad = [f for f in flows if any(w in str(f.get("replication_state", "")).upper()
                                           for w in ("ERROR", "FAIL", "DISCONNECT", "DOWN", "STOP", "UNAVAILABLE"))]
            rtts = [v for f in flows for v in rtt_values(f.get("rtt_statistic"))]
            seen = rep.get("last_seen_online")
            rtt = round(max(rtts), 1) if rtts else None
            if (flow or {}).get("rtt_ms") is not None:  # NSX 9.1 leaves rtt_statistic empty: the flow details carry it
                rtt = None  # sent below, with or without an answer to the status call
            self.metrics(lo, {
                "replication_flows": len(flows), "replication_flows_unhealthy": len(bad),
                "gm_rtt_ms": rtt, "gm_rtt_pct_of_max": rtt_pct(rtt, loc.get("maximum_rtt")),
                "gm_last_seen_minutes": round(max(0.0, time.time() * 1000 - float(seen)) / 60000.0, 1) if seen else None})
            self.props(lo, {"replication_state": "; ".join(
                f"{str(f.get('flow_id', '?')).split('.')[-1]}: {f.get('replication_state')}" for f in flows) or "no flow reported"})
        if flow:  # from the flow details, which do not depend on the status call above
            self.metrics(lo, {"gm_rtt_ms": flow.get("rtt_ms"), "gm_rtt_pct_of_max": rtt_pct(flow.get("rtt_ms"), loc.get("maximum_rtt")),
                              "sync_queue_used_pct": flow.get("queue_pct")})
        onboarding = self.cache.cached(f"onboarding:{loc['policy_id']}", INVENTORY_TTL, lambda: client.try_get(
            f"{GI}/sites/{loc['policy_id']}/onboarding/status"))
        if onboarding:
            self.props(lo, {"onboarding_status": onboarding.get("status")})

    def upgrade_state(self, client: NsxClient) -> Dict[str, Any]:
        """Upgrade summary of a manager, read every 30 minutes. Empty when the call fails."""
        return self.cache.cached(f"upgrade:{client.host}", INVENTORY_TTL,
                                 lambda: client.try_get("/api/v1/upgrade/summary")) or {}

    def finish_standby_sync(self) -> None:
        """A standby Global Manager gets its own sync state, as the active one reports it. After a switchover the
        former active is a standby and gets a fresh value, not the last one it sent as the active."""
        for standby, ok, remaining, full_age in self.standby_sync:
            so = self.gm_objects.get(standby)
            if so is not None:
                self.metrics(so, {"standby_in_sync": ok, "standby_sync_remaining": remaining,
                                  "standby_full_sync_age_hours": full_age,
                                  "standby_out_of_sync_minutes": self.standby_since.get(standby, 0)})
        # 3. A Global Manager that is neither the active nor a standby the active reports (mode NONE, or removed
        # from the federation) has no sync state: a last "not in sync" must not stay on it for ever.
        listed = {s[0] for s in self.standby_sync}
        for name, go in self.gm_objects.items():
            if name not in listed and name != self.active_name and self.sync_read:
                self.metrics(go, {"standby_in_sync": 1, "standby_sync_remaining": 0, "standby_out_of_sync_minutes": 0})

    def backup_and_certs(self, client: NsxClient) -> Tuple[Optional[bool], Optional[float], Optional[str], Optional[float], Optional[str]]:
        """(backup enabled, backup age in hours, last backup result, days to the first certificate expiry, its name).
        Slow-moving, so cached. A call that failed gives None ("unknown") for its values and is not cached."""
        backup = self.cache.cached(f"backup:{client.host}", SLOW_TTL,
                                   lambda: client.try_get("/api/v1/cluster/backups/overview"))

        def load_certs() -> Optional[List[dict]]:
            raw = client.try_list("/api/v1/trust-management/certificates", {"details": "true"})
            if not client.ok("/api/v1/trust-management/certificates"):
                return None
            return [{"display_name": c.get("display_name"), "id": c.get("id"),
                     "details": [{"not_after": d.get("not_after"), "subject_cn": d.get("subject_cn")}
                                 for d in (c.get("details") or [])[:1]]} for c in raw]
        certs = self.cache.cached(f"certs:{client.host}", SLOW_TTL, load_certs)
        b_enabled, b_age, b_result = backup_summary(backup) if backup is not None else (None, None, None)
        c_days, c_name = cert_summary(certs) if certs is not None else (None, None)
        return b_enabled, b_age, b_result, c_days, c_name

    def node(self, parent: Object, node: dict, online: int) -> None:
        uuid = node.get("uuid") or node.get("mgmt_cluster_listen_ip_address")
        address = node.get("mgmt_cluster_listen_ip_address") or uuid
        no = self.obj("manager_node", f"{address} ({parent.get_key().name})", f"node:{uuid}")
        no.add_parent(parent)
        self.metrics(no, {"online": online})
        self.props(no, {"address": node.get("mgmt_cluster_listen_ip_address"), "uuid": uuid})

    def channel(self, source_obj: Object, source_name: str, rc: dict) -> None:
        target = rc.get("site_name") or "?"
        stubs = rc.get("stubs") or []
        up = any(s.get("connection_up") for s in stubs) if stubs else None  # no stub listed: unknown
        # keyed by the site IDs of both ends, so a renamed location or Global Manager keeps its channels
        ends = f"{self.site_ids.get(source_name, source_name)}>{self.site_ids.get(target, target)}"
        co = self.obj("sync_channel", f"{source_name} -> {target}", f"channel:{ends}")
        self.at(co, self.path_of(source_name), self.path_of(target))
        co.add_parent(source_obj)
        self.metrics(co, {"connection_up": None if up is None else int(up)})
        self.props(co, {"source": source_name, "target": target,
                        "target_address": ", ".join(str(s.get("address")) for s in stubs)})
        return None

    # -- one location (runs in a worker thread)
    def collect_location(self, loc: Dict[str, Any]) -> bool:
        lo = self.obj("location", loc["name"], f"site:{loc['site_id']}")
        self.at(lo, loc["path"])
        if loc["path"] not in self.locations:  # degraded mode: list came from the cache
            self.locations[loc["path"]] = lo
            self.location_names[loc["path"]] = loc["name"]
            self.remember_site(loc["name"], loc["site_id"])
            lo.add_parent(self.fed)
        address = self.cfg.lm_overrides.get(loc["name"]) or loc.get("address")
        if not address:
            self.metrics(lo, {"collector_reachable": 0})
            self.props(lo, {"collector_error": "no Local Manager address known"})
            return False
        client = self.cfg.lm_client(address, loc.get("thumbprint"))
        self.clients.append(client)
        try:
            self.collect_lm(client, lo, loc)
            denied = sorted({why for why in client.failed.values() if why in ("HTTP 401", "HTTP 403")})
            if "/api/v1/cluster/status" in client.failed and len(client.failed) * 2 >= client.requests:
                raise RuntimeError(("the account is refused (" + ", ".join(denied) + ")") if denied else
                                   f"{len(client.failed)} of {client.requests} calls failed")
            loc["_reached"] = True
            self.cache.put(f"collected:{loc['path']}", time.time())
            self.metrics(lo, {"collector_reachable": 1, "api_calls": client.calls, "minutes_since_collection": 0})
            self.props(lo, {"collector_error": "", "certificate_trust": trust_text(client)})
            if client.trusted and not client.trust:
                self.event(lo, f"The certificate of Local Manager {address} is not the one that is trusted "
                           "(changed or replaced?)", Criticality.WARNING, "certificate_trust")
            return True
        except CertificateNotTrusted as e:
            self.since_collection(lo, loc)
            self.metrics(lo, {"collector_reachable": 0})
            self.props(lo, {"collector_error": str(e), "certificate_trust": "NOT TRUSTED"})
            self.event(lo, f"Local Manager {address} presents a certificate that is not trusted (changed or replaced?). "
                       "The location is not collected until the certificate is accepted.", Criticality.IMMEDIATE,
                       "certificate_trust")
        except BudgetExceeded:
            self.metrics(lo, {"collector_reachable": 1, "api_calls": client.calls})
            return True
        except Exception as e:
            logger.exception(e)
            self.since_collection(lo, loc)
            self.metrics(lo, {"collector_reachable": 0})
            self.props(lo, {"collector_error": repr(e)[:300]})
            # A monitoring-path fault, not a federation fault: lower criticality, separate wording.
            self.event(lo, f"The collector cannot reach Local Manager {address} of location {loc['name']}: {repr(e)[:150]}. "
                       "This is the monitoring path; the Global Manager connection is reported separately.",
                       Criticality.WARNING, "collector_reachable")
        return False

    def since_collection(self, lo: Object, loc: Dict[str, Any]) -> None:
        last = self.cache.get_any_age(f"collected:{loc['path']}")
        if last:
            self.metrics(lo, {"minutes_since_collection": round((time.time() - float(last)) / 60.0, 1)})

    def collect_lm(self, client: NsxClient, lo: Object, loc: Dict[str, Any]) -> None:
        name = loc["name"]
        site_down = loc["path"] in self.down_locations
        # manager cluster, alarms, backup, certificates
        cs = client.try_get("/api/v1/cluster/status", default={}) or {}
        lm_cluster_id = cs.get("cluster_id")
        mcs = cs.get("mgmt_cluster_status") or {}
        all_alarms = client.try_list("/api/v1/alarms", {"status": "OPEN"})
        alarms = summarize_alarms(all_alarms) if client.ok("/api/v1/alarms") else dict.fromkeys(("critical", "high", "other"))
        cs_ok = client.ok("/api/v1/cluster/status")
        b_enabled, b_age, b_result, c_days, c_name = self.backup_and_certs(client)
        self.props(lo, {"backup_last_result": b_result})
        for node in mcs.get("online_nodes") or []:
            self.node(lo, node, 1)
        for node in mcs.get("offline_nodes") or []:
            self.node(lo, node, 0)
        sync_states = client.try_list("/policy/api/v1/infra/full-sync-states")
        not_done = [s for s in sync_states if str(s.get("last_completed_stage", "")).upper() not in ("COMPLETED", "")]
        # does the built-in NSX integration monitor this Local Manager? (None = unknown)
        builtin = None if self.builtin_clusters is None or not lm_cluster_id else lm_cluster_id in self.builtin_clusters
        link = self.cfg.link_mode == "on" or (self.cfg.link_mode == "auto" and builtin is True)
        own_alarms = self.cfg.lm_alarm_mode == "always" or (self.cfg.lm_alarm_mode == "auto" and builtin is not True)
        done = [s for s in sync_states if str(s.get("last_completed_stage", "")).upper() == "COMPLETED"
                and s.get("start_time") and (s.get("last_update_time") or s.get("last_upate_time"))]
        last = max(done, key=lambda s: s.get("last_update_time") or s.get("last_upate_time"), default=None)
        if last:
            end = float(last.get("last_update_time") or last.get("last_upate_time"))
            self.metrics(lo, {"full_sync_duration_s": round(max(0.0, end - float(last["start_time"])) / 1000.0, 1),
                              "full_sync_age_hours": round(max(0.0, time.time() * 1000 - end) / 3600000.0, 1)})
        up = self.upgrade_state(client)
        self.props(lo, {"upgrade_status": up.get("upgrade_status"), "upgrade_target_version": up.get("target_version") or "none",
                        "alarm_alerts_by": "this pack" if own_alarms else "the built-in NSX integration",
                        # NSX raises capacity alarms of its own; the built-in integration forwards them when it
                        # monitors this Local Manager, whatever the setting for the federation alarms says
                        "capacity_alerts_by": "the built-in NSX integration" if builtin is True else "this pack"})
        self.metrics(lo, {
            "nodes_online": len(mcs.get("online_nodes") or []) if cs_ok else None,
            "nodes_offline": len(mcs.get("offline_nodes") or []) if cs_ok else None,
            "alarms_open_critical": alarms["critical"], "alarms_open_high": alarms["high"], "alarms_open_other": alarms["other"],
            "backup_age_hours": b_age, "cert_min_days_to_expiry": c_days,
            "full_sync_failed": len(not_done) if client.ok("/policy/api/v1/infra/full-sync-states") else None,
        })
        self.read_clock(client, "loc:" + loc["path"], lo)
        if cs_ok:  # a cached expiry must not stand for a manager that is down
            self.password_expiry(client, self.cfg.lm_user, lo)
        # configuration limits and overrides change slowly: read with the inventory, every INVENTORY_TTL seconds.
        # Only when the Local Manager answered in this cycle: cached values must not stand for a manager that is down.
        # A fault here leaves the values unknown and never stops the rest of the location.
        if cs_ok:
            try:
                self.limits_and_overrides(client, lo, loc)
            except BudgetExceeded:
                raise
            except Exception as e:
                logger.exception(e)
        self.props(lo, {"cluster_status": mcs.get("status"), "backup_enabled": b_enabled, "cert_first_to_expire": c_name,
                        "full_sync_stage": "; ".join(f"{s.get('id')}: {s.get('last_completed_stage')}" for s in sync_states) or "none recorded"})
        if not_done and not site_down:
            self.event(lo, f"Location {name}: full sync not completed: "
                       + ", ".join(f"{s.get('id')} at stage {s.get('last_completed_stage')}" for s in not_done),
                       Criticality.IMMEDIATE, "full_sync_failed")
        if c_days is not None and c_days < self.cfg.cert_warning_days:
            self.event(lo, f"Certificate '{c_name}' on the Local Manager of {name} expires in {c_days} days",
                       Criticality.CRITICAL if c_days < 7 else Criticality.WARNING, "cert_min_days_to_expiry")
        # federation alarms raised on this Local Manager, forwarded once each (capped)
        # (not when the built-in NSX integration monitors this Local Manager: it raises the same alarms)
        for a in [a for a in all_alarms if own_alarms and str(a.get("feature_name", "")).lower() == "federation"][:20]:
            sev = str(a.get("severity", "")).upper()
            self.event(lo, f"NSX alarm {a.get('event_type')}: {a.get('summary', '')}"[:400],
                       Criticality.CRITICAL if sev == "CRITICAL" else Criticality.IMMEDIATE if sev == "HIGH" else Criticality.WARNING)

        # sync channels as seen from this location (to the Global Managers and to the other locations)
        st = client.try_get("/api/v1/sites/status", default={}) or {}
        for rc in st.get("remote_connections", []):
            self.channel(lo, name, rc)

        # edge clusters that carry RTEPs, their nodes, inter-site BGP and tunnel statistics
        inv = self.cached_clean(client, f"lm_inventory:{client.host}", INVENTORY_TTL, lambda: self.load_lm_inventory(client))
        bgp_total = bgp_up = 0
        bgp_known = True
        edges: Dict[str, str] = {}  # transport node ID -> name, for the change timeline
        sr_records: List[Dict[str, Any]] = []
        answered: Set[str] = set()
        status_known = True
        for ec in inv["edge_clusters"]:
            eo = self.obj("edge_cluster", f"{ec['name']} ({name})", f"ec:{ec['id']}")
            self.at(eo, loc["path"])
            eo.add_parent(lo)
            if link and lm_cluster_id:
                eo.add_child(self.nsx_object("EdgeCluster", ec["name"], ec["id"], lm_cluster_id))
            status = client.try_get(f"/api/v1/edge-clusters/{ec['id']}/inter-site/status", default={}) or {}
            if not client.ok(f"/api/v1/edge-clusters/{ec['id']}/inter-site/status"):
                bgp_known = False  # no answer: the sessions of this cluster are unknown, not zero
                continue
            members = status.get("member_status") or []
            ec_total = sum(int(m.get("total_bgp_sessions") or 0) for m in members)
            ec_up = sum(int(m.get("established_bgp_sessions") or 0) for m in members)
            bgp_total += ec_total
            bgp_up += ec_up
            self.metrics(eo, {"intersite_bgp_total": ec_total, "intersite_bgp_established": ec_up, "members": len(members)})
            self.props(eo, {"intersite_status": status.get("overall_status"), "rtep_ips": ", ".join(ec.get("rtep_ips") or []),
                            "intersite_forwarding": ec.get("inter_site_forwarding_enabled"), "location": name})
            for m in members:
                tn_id = (m.get("transport_node") or {}).get("target_id")
                if not tn_id:
                    continue
                tn_name = inv["edge_names"].get(tn_id) or (m.get("transport_node") or {}).get("target_display_name") or tn_id
                # The object is identified by the NSX transport node ID, which a rename or a redeploy of the edge
                # keeps; only a node deleted and created again becomes a new object.
                edge_key = tn_id
                no = self.obj("edge_node", f"{tn_name} ({name})", f"edgenode:{edge_key}")
                self.at(no, loc["path"])
                no.add_parent(eo)
                if link and lm_cluster_id:
                    no.add_child(self.nsx_object("TransportNode", tn_name, tn_id, lm_cluster_id, zone=True))
                total, up = int(m.get("total_bgp_sessions") or 0), int(m.get("established_bgp_sessions") or 0)
                bgp_state = {(n.get("source_address"), n.get("neighbor_address")): str(n.get("connection_state", "")).upper()
                             for n in m.get("neighbor_status") or []}
                # the per-node summary carries the connection drop counter of each inter-site session, which the
                # cluster status above does not; a node without inter-site BGP answers 404, which is not a gap
                bgp_drops: Dict[Tuple[Any, Any], Any] = {}
                if bgp_state:  # a node with no inter-site session has no tunnel to carry the counter: no call
                    summary_path = f"/api/v1/transport-nodes/{tn_id}/inter-site/bgp/summary"
                    summary = client.try_get(summary_path, default={}) or {}
                    if not client.absent(summary_path) and client.ok(summary_path):
                        bgp_drops = {(n.get("source_address"), n.get("neighbor_address")): n.get("connection_drop_count")
                                     for n in summary.get("neighbor_status") or []}
                stats = client.try_get(f"/api/v1/transport-nodes/{tn_id}/inter-site/statistics", default={}) or {}
                stats_ok = client.ok(f"/api/v1/transport-nodes/{tn_id}/inter-site/statistics")
                tunnels = 0
                seen = set()
                for per_site in stats.get("stats_per_site") or []:
                    remote = (per_site.get("remote_site") or {}).get("target_display_name") or "?"
                    for t in per_site.get("stats_per_tunnel") or []:
                        src, dst = t.get("tunnel_source_address"), t.get("tunnel_destination_address")
                        seen.add((src, dst))
                        tunnels += 1
                        self.tunnel(no, edge_key, tn_name, src, dst, remote, t, bgp_state.get((src, dst)), bgp_drops.get((src, dst)))
                for (src, dst), state in bgp_state.items():  # sessions with no tunnel statistics yet
                    if (src, dst) not in seen:
                        tunnels += 1
                        self.tunnel(no, edge_key, tn_name, src, dst, "?" if stats_ok else None, {}, state, bgp_drops.get((src, dst)))
                self.metrics(no, {"intersite_bgp_total": total, "intersite_bgp_established": up,
                                  "rtep_tunnels": tunnels if stats_ok else None})
                self.props(no, {"location": name})
                edges[tn_id] = tn_name
                status_path = f"/api/v1/transport-nodes/{tn_id}/status"
                node_status = client.try_get(status_path, default={}) or {}
                self.edge_objects[tn_id] = no
                if client.ok(status_path):
                    try:
                        sr_records += self.edge_status(no, tn_id, tn_name, loc, node_status, builtin is True)
                        answered.add(tn_id)
                    except (TypeError, ValueError) as e:  # an answer of another shape: unknown, not a failed location
                        client._failed(status_path, type(e).__name__)
                        status_known = False
                else:
                    status_known = False
                if up < total and not site_down:
                    self.event(no, f"Edge node {tn_name} at {name}: {total - up} of {total} inter-site BGP sessions are not established",
                               Criticality.IMMEDIATE, "intersite_bgp_down")
        if bgp_known:
            self.metrics(lo, {"intersite_bgp_total": bgp_total, "intersite_bgp_established": bgp_up})
        self.edge_seen[loc["path"]] = (edges, bgp_known and client.ok("/policy/api/v1/infra/sites/default/enforcement-points/default/edge-clusters"))
        self.sr_records[loc["path"]] = sr_records
        self.sr_complete[loc["path"]] = bgp_known and status_known
        self.edge_answered[loc["path"]] = answered

        # Tier-0 uplink BGP neighbors of the stretched Tier-0 gateways, as realized at this location
        for t0 in inv["tier0_locales"]:
            t0_obj = getattr(self, "stretched_by_path", {}).get(t0["path"])
            for locale in t0["locales"]:
                for n in client.try_list(f"{LGI}/tier-0s/{t0['id']}/locale-services/{locale}/bgp/neighbors/status"):
                    edge = "edge member " + tail(n.get("edge_path"))
                    internal = str(n.get("neighbor_address", "")).startswith("169.254.")
                    kind_label = "inter-SR" if internal else "uplink"
                    key = f"bgp:{t0['path']}@{loc['site_id']}:{n.get('source_address')}>{n.get('neighbor_address')}"
                    bo = self.obj("bgp_neighbor", f"{t0['name']} {kind_label} {n.get('source_address')} -> {n.get('neighbor_address')} ({name})", key)
                    self.at(bo, loc["path"])
                    bo.add_parent(t0_obj or lo)
                    state = str(n.get("connection_state", "")).upper()
                    self.counters(bo, key, {
                        "established": 1 if state == "ESTABLISHED" else 0,
                        "prefixes_in": n.get("total_in_prefix_count"), "prefixes_out": n.get("total_out_prefix_count"),
                        "messages_in": n.get("messages_received"), "messages_out": n.get("messages_sent"),
                        "seconds_established": n.get("time_since_established"), "connection_drops": n.get("connection_drop_count"),
                    })
                    self.props(bo, {"state": state, "remote_as": n.get("remote_as_number"), "source_address": n.get("source_address"),
                                    "neighbor_address": n.get("neighbor_address"), "location": name, "edge": edge,
                                    "session_type": kind_label})
                    if state != "ESTABLISHED" and not site_down:
                        self.event(bo, f"Tier-0 {t0['name']} {kind_label} BGP neighbor {n.get('neighbor_address')} at {name} is {state}",
                                   Criticality.IMMEDIATE, "established")
                    elif state == "ESTABLISHED" and not internal and n.get("total_in_prefix_count") == 0:
                        self.event(bo, f"Tier-0 {t0['name']} receives no prefixes from BGP neighbor {n.get('neighbor_address')} at {name}",
                                   Criticality.WARNING, "prefixes_in")

        # rule statistics of the global policies, per location (and per rule when asked for)
        for pol in getattr(self, "inventory", {}).get("global_policy", []):
            if pol["path"] in self.span_of and loc["path"] not in self.span_of[pol["path"]]:
                continue  # not realized at this location: there are no statistics to read
            stats_path = f"{LGI}/domains/{pol.get('domain_id', 'default')}/security-policies/{pol['id']}/statistics"
            stats = client.try_get(stats_path, default={}) or {}
            if pol["path"] not in self.span_of and client.absent(stats_path):
                # span unknown and the manager does not know the policy: nothing to count there. Where the policy
                # is known to be realized, a 404 stays a failed call, so a partial sum is never sent.
                loc.setdefault("_policy_absent", set()).add(pol["path"])
                continue
            if not client.ok(stats_path):
                continue  # unknown, not zero: a zero would give a false rate in the next cycle
            totals = {"hit_count": 0, "packet_count": 0, "byte_count": 0, "session_count": 0}
            per_rule: Dict[str, Dict[str, int]] = {}
            for ep in stats.get("results") or []:
                for r in (ep.get("statistics") or {}).get("results") or []:
                    rule_key = tail(r.get("rule")) or str(r.get("internal_rule_id"))
                    pr = per_rule.setdefault(rule_key, dict.fromkeys(totals, 0))
                    for k in totals:
                        totals[k] += int(r.get(k) or 0)
                        pr[k] += int(r.get(k) or 0)
            ro = self.obj("realization", f"{pol.get('display_name') or pol['id']} @ {name}", f"real:{pol['path']}@{loc['path']}")
            self.at(ro, loc["path"])
            self.counters(ro, f"real:{pol['path']}@{loc['path']}", totals)
            loc.setdefault("_policy_hits", {})[pol["path"]] = (totals, per_rule)

    def edge_status(self, no: Object, tn_id: str, tn_name: str, loc: Dict[str, Any], st: Dict[str, Any],
                    builtin: bool) -> List[Dict[str, Any]]:
        """Capacity of a federation edge node, and the service routers of the stretched Tier-0 gateways it runs,
        from one call (GET /api/v1/transport-nodes/<id>/status). Returns one record per service router."""
        ns = st.get("node_status") or {}
        ss = ns.get("system_status") or {}
        cpu, mem = ss.get("cpu_usage") or {}, ss.get("edge_mem_usage") or {}
        dp = mem.get("datapath_mem_usage_details") or {}
        pct = lambda v: None if v is None else round(float(v), 2)
        fs = [100.0 * float(f["used"]) / float(f["total"]) for f in ss.get("file_systems") or []
              if f.get("total") and f.get("used") is not None]
        self.metrics(no, {
            "datapath_cpu_pct": pct(cpu.get("avg_cpu_core_usage_dpdk")),
            "datapath_cpu_peak_pct": pct(cpu.get("highest_cpu_core_usage_dpdk")),
            "services_cpu_pct": pct(cpu.get("avg_cpu_core_usage_non_dpdk")),
            "memory_used_pct": pct(mem.get("system_mem_usage")),
            "datapath_memory_pct": pct(mem.get("datapath_total_usage")),
            "datapath_pool_peak_pct": pct(dp.get("highest_datapath_mem_pool_usage")),
            "disk_fullest_pct": round(max(fs), 1) if fs else None,
        })
        maintenance = ns.get("maintenance_mode")
        self.props(no, {
            "edge_status": st.get("status"), "maintenance_mode": maintenance, "software_version": ns.get("software_version"),
            "datapath_pool_fullest": ", ".join(dp.get("highest_datapath_mem_pool_usage_names") or []) or None,
            # the built-in NSX integration raises the NSX alarms of the edges it monitors (CPU, memory, disk, status),
            # whatever the setting for the federation alarms of the Local Manager says
            "capacity_alerts_by": "the built-in NSX integration" if builtin else "this pack",
        })
        ha_modes = {o.get("path"): o.get("ha_mode") for o in getattr(self, "inventory", {}).get("tier0", [])}
        records = []
        for t in st.get("tier_0s") or []:
            path = t.get("path") or ""
            if not path.startswith("/global-infra/"):
                continue  # a Tier-0 of this location only: not part of the federation
            name = t.get("display_name") or tail(path)
            key = f"sr:{path}:{tn_id}"  # NSX IDs: a rename of the edge or the gateway keeps the object
            so = self.obj("tier0_sr", f"{name} on {tn_name} ({loc['name']})", key)
            self.at(so, loc["path"])
            so.add_parent(no)
            t0_obj = getattr(self, "stretched_by_path", {}).get(path)
            if t0_obj is not None:
                so.add_parent(t0_obj)
            ha = str(t.get("high_availability_status") or "UNKNOWN").upper()
            routing = str(t.get("routing_status") or "").upper()
            self.metrics(so, {"active": 1 if ha == "ACTIVE" else 0, "routing_up": (1 if routing == "UP" else 0) if routing else None,
                              "bgp_neighbors_up": t.get("bgp_neighbors_up_count"),
                              "bgp_neighbors_down": t.get("bgp_neighbors_down_count")})
            self.props(so, {"ha_status": ha, "routing_status": routing or None, "tier0": name, "ha_mode": ha_modes.get(path),
                            "edge": tn_name, "location": loc["name"], "edge_maintenance": maintenance})
            records.append({"path": path, "key": key, "ha": ha, "routing": routing, "edge": tn_name,
                            "location": loc["name"], "obj": so})
        return records

    def tunnel(self, node: Object, tn_id: str, tn_name: str, src: Any, dst: Any, remote: str, t: dict, bgp_state: Optional[str],
               bgp_drops: Optional[int]) -> None:
        to = self.obj("rtep_tunnel", f"{tn_name}: {src} -> {dst}", f"tunnel:{tn_id}:{src}>{dst}")
        self.at(to, *self.sites_of.get(id(node), ()), self.path_of(remote))
        to.add_parent(node)
        rx, tx = t.get("rx") or {}, t.get("tx") or {}
        self.counters(to, f"tunnel:{tn_id}:{src}>{dst}", {
            "rx_bytes": rx.get("total_bytes"), "tx_bytes": tx.get("total_bytes"),
            "rx_packets": rx.get("total_packets"), "tx_packets": tx.get("total_packets"),
            "rx_dropped": rx.get("dropped_packets"), "tx_dropped": tx.get("dropped_packets"),
            "rx_blocked": rx.get("blocked_packets"), "tx_blocked": tx.get("blocked_packets"),
            "bgp_established": None if bgp_state is None else (1 if bgp_state == "ESTABLISHED" else 0),
            "connection_drops": bgp_drops,
        })
        self.props(to, {"local_rtep": src, "remote_rtep": dst, "remote_location": remote, "bgp_state": bgp_state})

    def load_lm_inventory(self, client: NsxClient) -> Dict[str, Any]:
        clusters = [{"id": c.get("nsx_id") or c.get("id"), "name": c.get("display_name"), "rtep_ips": c.get("rtep_ips") or [],
                     "inter_site_forwarding_enabled": c.get("inter_site_forwarding_enabled")}
                    for c in client.try_list("/policy/api/v1/infra/sites/default/enforcement-points/default/edge-clusters")
                    if c.get("rtep_ips")]
        names = {}
        for tn in client.try_list("/api/v1/transport-nodes", {"node_types": "EdgeNode"}):
            names[tn.get("id")] = tn.get("display_name")
        t0s = []
        for t0 in client.try_list(f"{LGI}/tier-0s"):
            locales = [ls.get("id") for ls in client.try_list(f"{LGI}/tier-0s/{t0['id']}/locale-services")]
            t0s.append({"id": t0["id"], "name": t0.get("display_name") or t0["id"], "path": t0.get("path"), "locales": locales})
        return {"edge_clusters": clusters, "edge_names": names, "tier0_locales": t0s}

    def review_rules(self, po: Object, pol: Dict[str, Any], hits: Optional[Dict[str, Dict[str, int]]],
                     realized: bool = True) -> None:
        """Shadowed rules, rules that allow any traffic, and rules with no new hit for the unused-rule period.
        `hits`: the hits of each rule summed over the span, None when a location did not answer. `realized`: False
        when the policy is realized at no location, so there are no hits to count."""
        if pol.get("rules") is None or any("services" not in r for r in pol["rules"]):
            self.review_complete = False  # rules unknown in this cycle, or read before 1.12.0: nothing is sent
            return
        shadowed, wide = rule_review(pol)
        review = self.rule_review_paths
        self.metrics(po, {"rules_shadowed": len(shadowed), "rules_any_allow": len(wide)})
        self.props(po, {"shadowed_rules": name_list([f"{rule_name(b)} (by {rule_name(a)})" for b, a in shadowed]),
                        "any_allow_rules": name_list([rule_name(r) for r in wide])})
        review.update(str(r.get("path")) for r, _a in shadowed)
        review.update(str(r.get("path")) for r in wide)
        days = self.cfg.unused_rule_days
        if days <= 0:
            self.props(po, {"unused_rules": "check turned off"})
            return
        if not realized:
            self.props(po, {"unused_rules": "not realized at any location"})
            return
        if hits is None:
            self.review_complete = False
            return  # unknown, not zero
        now = time.time()
        seen = self.rule_seen
        unused = []
        for r in pol.get("rules") or []:
            path = str(r.get("path"))
            if r.get("disabled"):
                seen.pop(path, None)  # once enabled again, the period starts on that day
                continue
            total = (hits.get(str(r.get("id"))) or hits.get(str(r.get("rule_id"))) or {}).get("hit_count", 0)
            last = seen.get(path)
            if last is None or total != last[0]:  # first seen, new hits, or a counter reset: the period starts again
                seen[path] = [total, now]
            elif now - last[1] >= days * 86400:
                unused.append(r)
        self.metrics(po, {"rules_unused": len(unused)})
        self.props(po, {"unused_rules": name_list([
            f"{rule_name(r)} (no hit since {time.strftime('%Y-%m-%d', time.gmtime(seen[str(r.get('path'))][1]))})"
            for r in unused])})
        review.update(str(r.get("path")) for r in unused)

    def finish_policies(self, lm_sites: List[Dict[str, Any]]) -> None:
        """Sum the per-location policy statistics, and create rule objects for the selected policies."""
        by_path = getattr(self, "stretched_by_path", {})
        self.rule_review_paths: set = set()
        # the rollup is sent only when every policy of a fresh inventory was reviewed
        self.review_complete = getattr(self, "inventory", None) is not None
        self.rule_seen: Dict[str, List[float]] = dict(self.cache.get_any_age("rule_seen") or {})
        policies = getattr(self, "inventory", {}).get("global_policy", [])
        for pol in policies:
            po = by_path.get(pol["path"])
            if po is None:
                self.review_complete = False
                continue
            total_hits = 0
            rule_totals: Dict[str, Dict[str, int]] = {}
            span = self.span_of.get(pol["path"]) or {loc["path"] for loc in lm_sites}
            members = [loc for loc in lm_sites if loc["path"] in span and pol["path"] not in loc.get("_policy_absent", ())]
            complete = bool(members) and all(loc.get("_reached") and pol["path"] in (loc.get("_policy_hits") or {})
                                             for loc in members)
            for loc in lm_sites:
                totals, per_rule = (loc.get("_policy_hits") or {}).get(pol["path"], ({}, {}))
                total_hits += int(totals.get("hit_count", 0))
                for rk, vals in per_rule.items():
                    agg = rule_totals.setdefault(rk, {})
                    for k, v in vals.items():
                        agg[k] = agg.get(k, 0) + v
            if complete:  # a sum without one location would read as a drop, then as a burst
                self.counters(po, f"policy:{pol['path']}", {"hit_count": total_hits})
            self.review_rules(po, pol, rule_totals if complete else None, realized=bool(members))
            wanted = self.cfg.rule_policies
            if wanted and ("*" in wanted or pol["id"] in wanted):
                for r in pol.get("rules") or []:
                    ro = self.obj("rule", f"{pol.get('display_name')} / {r.get('display_name') or r.get('id')}", f"rule:{r.get('path')}")
                    ro.add_parent(po)
                    vals = rule_totals.get(str(r.get("id"))) or rule_totals.get(str(r.get("rule_id"))) or {}
                    if complete:
                        self.counters(ro, f"rule:{r.get('path')}",
                                      {k: vals.get(k, 0) for k in ("hit_count", "packet_count", "byte_count", "session_count")})
                    self.props(ro, {"action": r.get("action"), "disabled": bool(r.get("disabled")),
                                    "sources": ", ".join(tail(g) for g in r.get("source_groups") or []),
                                    "destinations": ", ".join(tail(g) for g in r.get("destination_groups") or [])})
        if getattr(self, "inventory", None) is not None and all(pol.get("rules") is not None for pol in policies):
            # forget the rules that left NSX (not when the rules of a policy are unknown in this cycle)
            current = {str(r.get("path")) for pol in policies for r in pol.get("rules") or []}
            self.rule_seen = {k: v for k, v in self.rule_seen.items() if k in current}
        self.cache.put("rule_seen", self.rule_seen)
        # a partial count would read as rules fixed, then as new ones
        self.rules_to_review = len(self.rule_review_paths) if self.review_complete else None


# ---- firewall rule review ---------------------------------------------------------------
def _is_any(v: Any) -> bool:
    return not v or list(v) == ["ANY"]


def _covers(a: Any, b: Any) -> bool:
    """Every value list `b` allows is in `a` (group membership is not expanded, so this never over-reports)."""
    if _is_any(a):
        return True
    return not _is_any(b) and set(b) <= set(a)


def rule_covers(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when rule `a` matches every packet rule `b` matches, so `b` placed after it can never be hit."""
    for f, ex in (("source_groups", "sources_excluded"), ("destination_groups", "destinations_excluded")):
        if a.get(ex) or b.get(ex):  # negated lists: only the exact same match is certain
            if bool(a.get(ex)) != bool(b.get(ex)) or set(a.get(f) or ["ANY"]) != set(b.get(f) or ["ANY"]):
                return False
        elif not _covers(a.get(f), b.get(f)):
            return False
    if a.get("service_entries"):  # services written in the rule itself: only a list of the same services is certain
        # (with ANY next to them, what NSX matches is not certain from the rule alone)
        if b.get("service_entries") or _is_any(a.get("services")) or _is_any(b.get("services")) \
                or not _covers(a.get("services"), b.get("services")):
            return False
    elif b.get("service_entries"):
        if not _is_any(a.get("services")):
            return False
    elif not _covers(a.get("services"), b.get("services")):
        return False
    if not _covers(a.get("profiles"), b.get("profiles")) or not _covers(a.get("scope"), b.get("scope")):
        return False
    da, db = str(a.get("direction") or "IN_OUT"), str(b.get("direction") or "IN_OUT")
    pa, pb = str(a.get("ip_protocol") or "IPV4_IPV6"), str(b.get("ip_protocol") or "IPV4_IPV6")
    return da in ("IN_OUT", db) and pa in ("IPV4_IPV6", pb)


def rule_review(policy: Dict[str, Any]) -> Tuple[List[Tuple[dict, dict]], List[dict]]:
    """(shadowed rules with the rule that matches first, rules that allow any traffic) of one policy. Rules read
    before 1.12.0 lack the fields: nothing is reported for them until the inventory is read again."""
    rules = [r for r in policy.get("rules") or [] if "services" in r]
    if len(rules) != len(policy.get("rules") or []):
        return [], []
    order = sorted(range(len(rules)), key=lambda i: (rules[i].get("sequence_number") or 0, i))
    enabled = [rules[i] for i in order if not rules[i].get("disabled")]
    shadowed = []
    for n, b in enumerate(enabled):
        first = next((a for a in enabled[:n] if rule_covers(a, b)), None)
        if first is not None:
            shadowed.append((b, first))
    wide = [r for r in enabled if str(r.get("action")).upper() == "ALLOW" and _is_any(policy.get("scope"))
            and all(_is_any(r.get(f)) for f in ("source_groups", "destination_groups", "services", "profiles", "scope"))
            and not r.get("service_entries") and not r.get("sources_excluded") and not r.get("destinations_excluded")]
    return shadowed, wide


def rule_name(r: Dict[str, Any]) -> str:
    return str(r.get("display_name") or r.get("id"))


def name_list(names: List[str], most: int = 10) -> str:
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "") or "none"


# ---- SDK entry points -----------------------------------------------------------------
def test(adapter_instance: AdapterInstance) -> TestResult:
    with Timer(logger, "Test"):
        result = TestResult()
        try:
            cfg = Config(adapter_instance)
            if not cfg.gm_hosts:
                result.with_error("Enter at least one Global Manager address")
                return result
            if cfg.ca_invalid:
                result.with_error(f"{cfg.ca_invalid} of the blocks in 'Trusted CA certificates' are not valid certificates")
                return result
            ok, active, errors = 0, 0, []
            for host in cfg.gm_hosts:
                try:
                    client = cfg.gm_client(host)
                    site = client.get("/api/v1/sites/self").get("site") or {}
                    client.close()
                    ok += 1
                    mode = str(site.get("active_gm", "")).upper()
                    if mode == "ACTIVE":
                        active += 1
                    elif mode not in ("STANDBY",):
                        errors.append(f"{host} answers but is not a Global Manager of a federation (mode {mode or 'none'})")
                except Exception as e:
                    errors.append(f"{host}: {repr(e)[:200]}")
            if ok == 0:
                result.with_error("No Global Manager could be reached. " + "; ".join(errors))
            elif active == 0:
                result.with_error("No reachable Global Manager is active. " + "; ".join(errors))
            elif active > 1:
                result.with_error("More than one Global Manager claims the active role (split-brain)")
            elif errors:
                result.with_error("; ".join(errors))
        except Exception as e:
            logger.exception(e)
            result.with_error("Unexpected connection test error: " + repr(e))
        return result


def collect(adapter_instance: AdapterInstance) -> CollectResult:
    with Timer(logger, "Collection"):
        result = CollectResult()
        try:
            collector = Collector(adapter_instance, result)
            collector.collect()
        except Exception as e:
            logger.error("Unexpected collection error")
            logger.exception(e)
            result.with_error("Unexpected collection error: " + repr(e))
        return result


def get_endpoints(adapter_instance: AdapterInstance) -> EndpointResult:
    with Timer(logger, "Get Endpoints"):
        result = EndpointResult()
        cfg = Config(adapter_instance)
        for host in cfg.gm_hosts:
            result.with_endpoint(f"https://{host}")
        # Local Managers are trusted through the thumbprint the Global Manager holds for each of them,
        # so only overridden addresses are offered to the VCF Operations trust store.
        for addr in cfg.lm_overrides.values():
            result.with_endpoint(f"https://{addr}")
        return result


def main(argv: List[str]) -> None:
    logging.setup_logging("adapter.log")
    logging.rotate()
    logger.info(f"Running adapter code with arguments: {argv}")
    if len(argv) != 3:
        logger.error("Arguments must be <method> <inputfile> <ouputfile>")
        sys.exit(1)
    method = argv[0]
    try:
        if method == "test":
            test(AdapterInstance.from_input()).send_results()
        elif method == "endpoint_urls":
            get_endpoints(AdapterInstance.from_input()).send_results()
        elif method == "collect":
            collect(AdapterInstance.from_input()).send_results()
        elif method == "adapter_definition":
            result = get_adapter_definition()
            if type(result) is AdapterDefinition:
                result.send_results()
            else:
                logger.info("get_adapter_definition method did not return an AdapterDefinition")
                sys.exit(1)
        else:
            logger.error(f"Command {method} not found")
            sys.exit(1)
    finally:
        logger.info(Timer.graph())
        sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
