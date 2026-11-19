#  NSX Federation management pack for VCF Operations (Integration SDK, Python).
#  Read-only. One adapter instance = one federation.
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import aria.ops.adapter_logging as logging
from aria.ops.adapter_instance import AdapterInstance
from aria.ops.definition.adapter_definition import AdapterDefinition
from aria.ops.definition.units import Units
from aria.ops.event import Criticality
from aria.ops.object import Identifier, Object
from aria.ops.result import CollectResult, EndpointResult, TestResult
from aria.ops.timer import Timer
from constants import ADAPTER_KIND, ADAPTER_NAME
from nsxclient import BudgetExceeded, Cache, CertificateNotTrusted, NsxClient, pem_sha256

logger = logging.getLogger(__name__)

GI = "/global-manager/api/v1/global-infra"  # policy tree on a Global Manager
LGI = "/policy/api/v1/global-infra"  # the same tree as received by a Local Manager
INVENTORY_TTL = 30 * 60  # seconds: inventory tier
SLOW_TTL = 60 * 60  # seconds: certificates and backups
FEDERATION_ALARM_FEATURES = {"federation", "communication", "clustering", "certificates"}

# ---- units, by metric key (the same key has the same unit on every object type) ----------
_PKT, _PER_MIN = Units.MISC.PACKETS, Units.RATE.PER_MINUTE
UNITS = {
    "backup_age_hours": Units.TIME.HOURS, "cert_min_days_to_expiry": Units.TIME.DAYS,
    "collection_duration_s": Units.TIME.SECONDS, "seconds_established": Units.TIME.SECONDS,
    "api_calls": Units.MISC.CALLS, "api_calls_throttled": Units.MISC.CALLS, "api_errors": Units.MISC.ERRORS, "data_gaps": Units.MISC.CALLS,
    "rx_bytes": Units.DATA_SIZE.BYTE, "tx_bytes": Units.DATA_SIZE.BYTE, "byte_count": Units.DATA_SIZE.BYTE,
    "rx_bytes_per_s": Units.DATA_RATE.BYTE_PER_SECOND, "tx_bytes_per_s": Units.DATA_RATE.BYTE_PER_SECOND,
    "rx_packets": _PKT, "tx_packets": _PKT, "rx_dropped": _PKT, "tx_dropped": _PKT, "rx_blocked": _PKT, "tx_blocked": _PKT,
    "packet_count": _PKT,
    "rx_packets_per_min": _PER_MIN, "tx_packets_per_min": _PER_MIN, "rx_dropped_per_min": _PER_MIN,
    "tx_dropped_per_min": _PER_MIN, "rx_blocked_per_min": _PER_MIN, "tx_blocked_per_min": _PER_MIN,
    "hit_count_per_min": _PER_MIN, "rtep_dropped_per_min": _PER_MIN,
    "locations_healthy_pct": Units.RATIO.PERCENT,
}
# counters that also get a rate: key -> (rate key, seconds the rate is expressed in)
RATES = {
    "rx_packets": ("rx_packets_per_min", 60), "tx_packets": ("tx_packets_per_min", 60),
    "rx_dropped": ("rx_dropped_per_min", 60), "tx_dropped": ("tx_dropped_per_min", 60),
    "rx_blocked": ("rx_blocked_per_min", 60), "tx_blocked": ("tx_blocked_per_min", 60),
    "rx_bytes": ("rx_bytes_per_s", 1), "tx_bytes": ("tx_bytes_per_s", 1),
    "hit_count": ("hit_count_per_min", 60),
}

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
        ("collection_duration_s", "Collection duration (s)"),
        ("api_calls", "NSX API calls in this cycle"),
        ("api_calls_throttled", "NSX API calls throttled"),
        ("api_errors", "NSX API errors"),
        ("data_gaps", "NSX API calls without an answer (data unknown)"),
        ("managers_over_budget", "Managers that used their call budget"),
        ("locations_healthy", "Locations healthy"),
        ("locations_healthy_pct", "Locations healthy (%)"),
        ("rtep_tunnels_total", "RTEP tunnels"),
        ("rtep_tunnels_bgp_down", "RTEP tunnels with inter-site BGP down"),
        ("rtep_dropped_per_min", "RTEP packets dropped per minute, all tunnels"),
        ("cert_min_days_to_expiry", "Days until the first manager certificate expires"),
        ("alarms_open_critical", "Open critical alarms, all managers"),
    ], [
        ("federation_id", "Federation ID"),
        ("active_global_manager", "Active Global Manager"),
        ("standby_configured", "Standby Global Manager configured"),
        ("collection_mode", "Collection mode"),
        ("failed_calls", "NSX API calls without an answer"),
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
        ("standby_in_sync", "Standby Global Managers in sync (1 = all)"),
        ("standby_sync_remaining", "Entries left to send to the standby"),
    ], [
        ("mode", "Mode"), ("cluster_status", "Cluster status"), ("version", "Version"),
        ("address", "Address used by the collector"),
        ("backup_enabled", "Backup enabled"), ("backup_last_result", "Last backup result"),
        ("cert_first_to_expire", "First certificate to expire"),
        ("switchover_status", "Switchover status"),
        ("standby_sync_status", "Standby sync status"),
    ]),
    "manager_node": ("Global Manager Node", [("online", "Online")],
                     [("address", "Address"), ("uuid", "Node UUID")]),
    "location": ("Location", [
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
    ], [
        ("site_id", "Site ID"), ("version", "Version"), ("version_matches_gm", "Same version as the Global Manager"),
        ("local_manager_address", "Local Manager address"), ("rtep_ips", "RTEP addresses"),
        ("maximum_rtt_ms", "Maximum RTT setting (ms)"), ("cluster_status", "Local Manager cluster status"),
        ("full_sync_stage", "Last full sync stage"), ("backup_enabled", "Backup enabled"),
        ("cert_first_to_expire", "First certificate to expire"), ("certificate_trust", "Certificate trust"),
        ("collector_error", "Collector error"),
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
    ], [("location", "Location")]),
    "rtep_tunnel": ("RTEP Tunnel", [
        ("rx_bytes", "Received bytes"), ("tx_bytes", "Sent bytes"),
        ("rx_packets", "Received packets"), ("tx_packets", "Sent packets"),
        ("rx_dropped", "Received packets dropped"), ("tx_dropped", "Sent packets dropped"),
        ("rx_blocked", "Received packets blocked"), ("tx_blocked", "Sent packets blocked"),
        ("rx_bytes_per_s", "Received bytes per second"), ("tx_bytes_per_s", "Sent bytes per second"),
        ("rx_packets_per_min", "Received packets per minute"), ("tx_packets_per_min", "Sent packets per minute"),
        ("rx_dropped_per_min", "Received packets dropped per minute"), ("tx_dropped_per_min", "Sent packets dropped per minute"),
        ("rx_blocked_per_min", "Received packets blocked per minute"), ("tx_blocked_per_min", "Sent packets blocked per minute"),
        ("bgp_established", "Inter-site BGP established"),
    ], [("local_rtep", "Local RTEP"), ("remote_rtep", "Remote RTEP"), ("remote_location", "Remote location"),
        ("bgp_state", "Inter-site BGP state")]),
    "tier0": ("Stretched Tier-0 Gateway", [], [("ha_mode", "HA mode"), ("primary_location", "Primary location")]),
    "tier1": ("Stretched Tier-1 Gateway", [], [("tier0", "Connected Tier-0"), ("primary_location", "Primary location")]),
    "segment": ("Stretched Segment", [], [("subnets", "Subnets"), ("gateway", "Connected gateway"), ("segment_type", "Type")]),
    "global_policy": ("Global Security Policy", [
        ("rules_total", "Rules"), ("rules_disabled", "Rules disabled"),
        ("hit_count", "Rule hits (all locations)"), ("hit_count_per_min", "Rule hits per minute (all locations)"),
    ], [("category", "Category"), ("domain", "Domain"), ("scope", "Applied to")]),
    "realization": ("Stretched Object at Location", [
        ("realized", "Realized"),
        ("hit_count", "Rule hits"), ("hit_count_per_min", "Rule hits per minute"),
        ("packet_count", "Packets"), ("byte_count", "Bytes"), ("session_count", "Sessions"),
    ], [("status", "Realization status"), ("location", "Location"), ("object_path", "Object path"), ("object_type", "Object type")]),
    "bgp_neighbor": ("Tier-0 BGP Neighbor", [
        ("established", "Established"), ("prefixes_in", "Prefixes received"), ("prefixes_out", "Prefixes advertised"),
        ("messages_in", "Messages received"), ("messages_out", "Messages sent"),
        ("seconds_established", "Seconds established"), ("connection_drops", "Connection drops"),
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
        ("last_modified_user", "Last modified by"), ("last_modified_time", "Last modified (epoch ms)"),
    ])


# ---- metric groups: the folders of the Metrics tab. The key VCF Operations stores is "<group>|<metric>".
_G = {
    "alarms": "Alarms", "certificates": "Certificates", "backup": "Backup", "cluster": "Cluster",
    "connectivity": "Connectivity", "sync": "Sync", "intersite": "Inter-site", "collection": "Collection",
    "global_managers": "Global Managers", "locations": "Locations", "stretched": "Stretched objects",
    "totals": "Counters since start", "rates": "Rates", "realization": "Realization", "rules": "Rules",
    "traffic": "Traffic", "prefixes": "Prefixes and messages",
}
_GROUP_BY_KEY = {
    "alarms_open_critical": "alarms", "alarms_open_high": "alarms", "alarms_open_other": "alarms",
    "cert_min_days_to_expiry": "certificates", "backup_age_hours": "backup",
    "nodes_online": "cluster", "nodes_offline": "cluster", "service_groups_degraded": "cluster",
    "reachable": "connectivity", "gm_connection_up": "connectivity", "collector_reachable": "connectivity",
    "standby_sync_entries": "sync", "full_sync_failed": "sync", "standby_in_sync": "sync", "standby_sync_remaining": "sync",
    "intersite_bgp_total": "intersite", "intersite_bgp_established": "intersite", "intersite_bgp_down": "intersite",
    "rtep_tunnels_total": "intersite", "rtep_tunnels_bgp_down": "intersite", "rtep_dropped_per_min": "intersite",
    "collection_duration_s": "collection", "api_calls": "collection", "api_calls_throttled": "collection",
    "api_errors": "collection", "data_gaps": "collection", "managers_over_budget": "collection",
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
}
for _k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_dropped", "tx_dropped", "rx_blocked", "tx_blocked"):
    _GROUP_BY_KEY[_k] = "totals"
    _GROUP_BY_KEY[RATES[_k][0]] = "rates"
# a type with only one or two metrics keeps them at the top
_FLAT_KINDS = {"manager_node", "sync_channel"}


def metric_group(kind: str, key: str) -> Optional[str]:
    return None if kind in _FLAT_KINDS else _GROUP_BY_KEY.get(key)


def fk(kind: str, key: str) -> str:
    """Full metric key as stored in VCF Operations."""
    g = metric_group(kind, key)
    return f"{g}|{key}" if g else key


# ---- metric flags. KPI: VCF Operations weighs these first when it looks for evidence of a problem.
KPI = {
    ("federation", "global_managers_active"), ("federation", "locations_healthy_pct"),
    ("global_manager", "nodes_offline"), ("global_manager", "standby_in_sync"), ("location", "gm_connection_up"), ("location", "intersite_bgp_down"),
    ("location", "full_sync_failed"), ("sync_channel", "connection_up"),
    ("edge_node", "intersite_bgp_established"), ("rtep_tunnel", "bgp_established"),
    ("rtep_tunnel", "rx_dropped_per_min"), ("rtep_tunnel", "tx_dropped_per_min"),
    ("bgp_neighbor", "established"), ("realization", "realized"),
    ("tier0", "error_locations"), ("tier1", "error_locations"), ("segment", "error_locations"),
    ("global_policy", "error_locations"),
}
# state metrics that are 0 or 1
DISCRETE = {"standby_in_sync", "reachable", "online", "gm_connection_up", "collector_reachable", "connection_up", "bgp_established",
            "established", "realized"}
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
        d.define_enum_parameter(
            "certificate_trust", values=["enforce", "report"], label="Certificate trust",
            description="enforce: a manager whose certificate is not trusted is not collected. "
                        "report: it is collected and the mismatch is reported.",
            default="enforce", advanced=True)
        d.define_enum_parameter(
            "link_nsx_objects", values=["off", "on"], label="Link to the objects of the NSX integration",
            description="on: each federation edge node and edge cluster gets a relationship to the transport node and "
                        "edge cluster object of the built-in NSX integration, when that integration monitors the same "
                        "Local Manager. Experimental.",
            default="off", advanced=True)
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
        self.enforce_trust = str(val("certificate_trust", "enforce")) == "enforce"
        self.link_nsx_objects = str(val("link_nsx_objects", "off")) == "on"
        self.max_parallel = int(val("max_parallel", 8))
        self.timeout = int(val("request_timeout", 30))
        self.call_budget = int(val("call_budget", 2000))
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
                self.trusted.add(pem_sha256(cert.get_certificate_string()))
            except Exception:
                pass

    def gm_client(self, host: str) -> NsxClient:
        return NsxClient(host, self.user, self.password, self.timeout, self.call_budget,
                         self.trusted, self.enforce_trust)

    def lm_client(self, host: str, gm_thumbprint: Optional[str]) -> NsxClient:
        trusted = set(self.trusted)
        if gm_thumbprint:  # the Global Manager pins each Local Manager by SHA-256 thumbprint
            trusted.add(gm_thumbprint.lower().replace(":", ""))
        return NsxClient(host, self.lm_user, self.lm_password, self.timeout, self.call_budget,
                         trusted, self.enforce_trust)


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


class Collector:
    def __init__(self, ai: AdapterInstance, result: CollectResult) -> None:
        self.cfg = Config(ai)
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
        self.over_budget = 0
        # counter values of the previous cycle, for rates: object id -> {"t": epoch, counter: value}
        self.prev_counters: Dict[str, Dict[str, float]] = self.cache.get_any_age("counters") or {}
        self.new_counters: Dict[str, Dict[str, float]] = {}

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
        A counter that went down (reset on the NSX side) gives no rate for that cycle."""
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
        self.new_counters[ident] = dict(current, t=now)

    @staticmethod
    def props(o: Object, values: Dict[str, Any]) -> None:
        for k, v in values.items():
            if v is not None:
                o.with_property(k, v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))

    @staticmethod
    def event(o: Object, message: str, criticality: Criticality, alert_key: Optional[str] = None) -> None:
        # alert_key names the metric or property that an alert definition of the pack watches for this
        # condition. Such a condition raises a real alert, so no event is sent (it would show a second,
        # "Notification event" alert for the same thing). Conditions without an alert definition are sent
        # as events and surface as notification alerts.
        if alert_key is None:
            o.with_event(message=message, criticality=criticality)

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
        reachable = [g for g in gms if g["ok"]]
        active = [g for g in reachable if str(g["site"].get("active_gm", "")).upper() == "ACTIVE"]

        self.fed_id = (reachable[0]["federation_id"] if reachable else self.cache.get_any_age("federation_id")) or ""
        if not self.fed_id:
            self.result.with_error(
                "No Global Manager answered and no earlier collection is cached: "
                + "; ".join(f"{g['host']}: {g['error']}" for g in gms))
            return
        self.cache.put("federation_id", self.fed_id)
        self.fed = self.result.object(ADAPTER_KIND, "federation", f"NSX Federation {self.fed_id[:8]}",
                                      [Identifier("id", self.fed_id)])
        fed = self.fed

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
                self.over_budget += 1
            except Exception as e:
                logger.exception(e)
                self.event(fed, f"Collection from the active Global Manager failed: {repr(e)[:200]}", Criticality.IMMEDIATE)
        mode = "normal"
        if not sites_doc:
            # No active Global Manager: keep monitoring the locations from the last known list.
            sites_doc = self.cache.get_any_age("sites_doc") or {}
            mode = "degraded: location list from cache" if sites_doc else "degraded: no location list"

        # Global Manager objects (active and standby); the standby is an object of its own.
        gm_names = []
        for g in reachable:
            try:
                gm_names.append(self.collect_gm_object(g, is_active=(g is gm)))
            except BudgetExceeded:
                self.over_budget += 1
            except Exception as e:
                logger.exception(e)
        standby_known = len(sites_doc.get("global_managers", [])) > 1 or len(cfg.gm_hosts) > 1
        if not standby_known:
            self.event(fed, "No standby Global Manager is configured for this federation", Criticality.WARNING, "standby_configured")

        # -- step 2: Local Managers, in parallel, each with its own budget
        lm_sites = sites_doc.get("locations", [])
        with ThreadPoolExecutor(max_workers=max(1, min(cfg.max_parallel, len(lm_sites) or 1))) as pool:
            lm_results = list(pool.map(self.collect_location, lm_sites))
        collected = sum(1 for r in lm_results if r)
        self.finish_policies(lm_sites)

        # -- step 3: federation object and pack self-health
        failed = sorted(f"{c.host}{p} ({why})" for c in self.clients for p, why in c.failed.items())
        self.metrics(fed, {
            "global_managers_configured": len(cfg.gm_hosts),
            "global_managers_reachable": len(reachable),
            "global_managers_unreachable": len(cfg.gm_hosts) - len(reachable),
            "global_managers_active": len(active),
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
            "managers_over_budget": self.over_budget,
        })
        self.metrics(fed, self.rollups())
        self.props(fed, {
            "failed_calls": "; ".join(failed[:8]) + (f"; and {len(failed) - 8} more" if len(failed) > 8 else "") or "none",
            "federation_id": self.fed_id,
            "active_global_manager": gm["site"].get("name") if gm else "none",
            "standby_configured": standby_known,
            "collection_mode": mode,
        })
        if self.over_budget:
            self.event(fed, f"{self.over_budget} manager(s) used the whole API call budget; the data of this cycle is partial. "
                       "Raise the budget or narrow the collection.", Criticality.WARNING, "managers_over_budget")
        self.cache.put("counters", self.new_counters)
        self.cache.save()
        for c in self.clients:
            c.close()

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
        tunnels = get("rtep_tunnel")
        drops = [val(o, k) for o in tunnels for k in ("rx_dropped_per_min", "tx_dropped_per_min")]
        drops = [d for d in drops if d is not None]
        certs = [val(o, "cert_min_days_to_expiry") for o in get("global_manager") + locs]
        certs = [c for c in certs if c is not None]
        crit = [val(o, "alarms_open_critical") for o in get("global_manager") + locs]
        return {
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
        status = client.try_get("/api/v1/sites/status", default={}) or {}
        status_ok = client.ok("/api/v1/sites/status")
        conn = {rc.get("site_name"): rc for rc in status.get("remote_connections", [])}
        policy_sites = client.list(f"{GI}/sites")
        rtep = {tail(sc.get("site_path")): sc.get("rtep_ips", [])
                for sc in (client.try_get(f"{GI}/federation-config", default={}) or {}).get("site_config", [])}
        gms = client.try_list(f"{GI}/global-managers")
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
                "gm_connection_up": (bool(stubs) and any(s.get("connection_up") for s in stubs)) if status_ok else None,
            })
        doc = {"locations": locations,
               "global_managers": [{"id": g.get("id"), "mode": g.get("mode")} for g in gms]}

        # Location objects exist as soon as the Global Manager lists them.
        for loc in locations:
            lo = self.obj("location", loc["name"], f"site:{loc['site_id']}")
            self.locations[loc["path"]] = lo
            self.location_names[loc["path"]] = loc["name"]
            lo.add_parent(self.fed)
            self.metrics(lo, {"gm_connection_up": None if loc["gm_connection_up"] is None else int(loc["gm_connection_up"])})
            self.props(lo, {
                "site_id": loc["site_id"], "version": loc["version"], "local_manager_address": loc["address"],
                "rtep_ips": ", ".join(loc["rtep_ips"]), "maximum_rtt_ms": loc["maximum_rtt"],
                "version_matches_gm": same_release(loc["version"], gm_version) if loc["version"] and gm_version else None,
            })
            if loc["gm_connection_up"] is False:
                self.down_locations.add(loc["path"])
                self.event(lo, f"Location {loc['name']} is not connected to the active Global Manager",
                           Criticality.CRITICAL, "gm_connection_up")
            if loc["version"] and gm_version and not same_release(loc["version"], gm_version):
                self.event(lo, f"Version mismatch: location {loc['name']} runs {loc['version']}, the Global Manager "
                           f"runs {gm_version}. Expected only during an upgrade.", Criticality.INFO, "version_matches_gm")

        self.collect_stretched(client)
        return doc

    def collect_stretched(self, client: NsxClient) -> None:
        inventory = self.cache.cached("gm_inventory", INVENTORY_TTL, lambda: self.load_gm_inventory(client))
        items: List[Tuple[str, dict]] = []
        for kind in STRETCHED:
            items.extend((kind, o) for o in inventory.get(kind, []))

        def realization(item: Tuple[str, dict]) -> Tuple[str, dict, Optional[dict]]:
            kind, o = item
            try:
                return kind, o, client.get(f"{GI}/realized-state/status", {"intent_path": o["path"]})
            except BudgetExceeded:
                return kind, o, {"_budget": True}
            except Exception:
                return kind, o, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            realized = list(pool.map(realization, items))

        by_path: Dict[str, Object] = {}
        budget_hit = False
        for kind, o, st in realized:
            so = self.obj(kind, o.get("display_name") or o["id"], f"{kind}:{o['path']}")
            by_path[o["path"]] = so
            so.add_parent(self.fed)
            self.props(so, {
                "path": o["path"], "revision": o.get("_revision"),
                "last_modified_user": o.get("_last_modified_user"), "last_modified_time": o.get("_last_modified_time"),
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
                rules = o.get("rules") or []
                self.props(so, {"category": o.get("category"), "domain": tail(o.get("parent_path")),
                                "scope": ", ".join(tail(s) for s in o.get("scope") or [])})
                self.metrics(so, {"rules_total": o.get("rule_count", len(rules)),
                                  "rules_disabled": sum(1 for r in rules if r.get("disabled"))})
            if st and st.get("_budget"):
                budget_hit = True
                continue
            if not st:
                self.props(so, {"status": "UNKNOWN"})
                continue
            per_ep = st.get("consolidated_status_per_enforcement_point") or []
            ok = err = 0
            for ep in per_ep:
                site_path = ep.get("site_path") or ""
                state = consolidated(ep.get("consolidated_status"))
                site_name = self.location_names.get(site_path, tail(site_path))
                ro = self.obj("realization", f"{o.get('display_name') or o['id']} @ {site_name}",
                              f"real:{o['path']}@{site_path}")
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
        if budget_hit:
            self.over_budget += 1
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
            "tier0": [slim(o) for o in client.list(f"{GI}/tier-0s")],
            "tier1": [slim(o) for o in client.list(f"{GI}/tier-1s")],
            "segment": [slim(o) for o in client.list(f"{GI}/segments")],
            "global_policy": [],
        }
        for domain in client.try_list(f"{GI}/domains"):
            for pol in client.try_list(f"{GI}/domains/{domain['id']}/security-policies"):
                if pol.get("_system_owned") or str(pol.get("id", "")).startswith("default-"):
                    continue  # system defaults are not user intent
                entry = slim(pol)
                full = client.try_get(f"{GI}/domains/{domain['id']}/security-policies/{pol['id']}", default={}) or {}
                entry["rules"] = [{k: r.get(k) for k in ("id", "display_name", "path", "action", "disabled",
                                                           "source_groups", "destination_groups", "rule_id")}
                                  for r in full.get("rules") or []]
                entry["domain_id"] = domain["id"]
                inv["global_policy"].append(entry)
        return inv

    # -- Global Manager object (active or standby)
    def collect_gm_object(self, g: Dict[str, Any], is_active: bool) -> str:
        client: NsxClient = g["client"]
        site = g["site"]
        name = site.get("name") or g["host"]
        go = self.obj("global_manager", name, f"gm:{site.get('id')}")
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
        })
        for node in mcs.get("online_nodes") or []:
            self.node(go, node, 1)
        for node in mcs.get("offline_nodes") or []:
            self.node(go, node, 0)
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
                    str(s.get("sync_type", "")).upper() == "DELTA_SYNC" and full == "COMPLETE")
            if sync:
                self.metrics(go, {"standby_in_sync": int(all(in_sync(s) for s in sync)),
                                  "standby_sync_remaining": sum(int(s.get("remaining_entries_to_send") or 0) for s in sync)})
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
        no = self.obj("manager_node", node.get("mgmt_cluster_listen_ip_address") or uuid, f"node:{uuid}")
        no.add_parent(parent)
        self.metrics(no, {"online": online})
        self.props(no, {"address": node.get("mgmt_cluster_listen_ip_address"), "uuid": uuid})

    def channel(self, source_obj: Object, source_name: str, rc: dict) -> None:
        target = rc.get("site_name") or "?"
        stubs = rc.get("stubs") or []
        up = bool(stubs) and any(s.get("connection_up") for s in stubs)
        co = self.obj("sync_channel", f"{source_name} -> {target}", f"chan:{source_name}>{target}")
        co.add_parent(source_obj)
        self.metrics(co, {"connection_up": 1 if up else 0})
        self.props(co, {"source": source_name, "target": target,
                        "target_address": ", ".join(str(s.get("address")) for s in stubs)})
        return None

    # -- one location (runs in a worker thread)
    def collect_location(self, loc: Dict[str, Any]) -> bool:
        lo = self.obj("location", loc["name"], f"site:{loc['site_id']}")
        if loc["path"] not in self.locations:  # degraded mode: list came from the cache
            self.locations[loc["path"]] = lo
            self.location_names[loc["path"]] = loc["name"]
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
            self.metrics(lo, {"collector_reachable": 1, "api_calls": client.calls})
            self.props(lo, {"collector_error": "", "certificate_trust": "trusted"
                            if (not client.trusted or client.fingerprint in client.trusted) else "NOT TRUSTED (reported only)"})
            if client.trusted and client.fingerprint not in client.trusted:
                self.event(lo, f"The certificate of Local Manager {address} is not the one that is trusted "
                           "(changed or replaced?)", Criticality.WARNING, "certificate_trust")
            return True
        except CertificateNotTrusted as e:
            self.metrics(lo, {"collector_reachable": 0})
            self.props(lo, {"collector_error": str(e), "certificate_trust": "NOT TRUSTED"})
            self.event(lo, f"Local Manager {address} presents a certificate that is not trusted (changed or replaced?). "
                       "The location is not collected until the certificate is accepted.", Criticality.IMMEDIATE,
                       "certificate_trust")
        except BudgetExceeded:
            self.over_budget += 1
            self.metrics(lo, {"collector_reachable": 1, "api_calls": client.calls})
            return True
        except Exception as e:
            logger.exception(e)
            self.metrics(lo, {"collector_reachable": 0})
            self.props(lo, {"collector_error": repr(e)[:300]})
            # A monitoring-path fault, not a federation fault: lower criticality, separate wording.
            self.event(lo, f"The collector cannot reach Local Manager {address} of location {loc['name']}: {repr(e)[:150]}. "
                       "This is the monitoring path; the Global Manager connection is reported separately.",
                       Criticality.WARNING, "collector_reachable")
        return False

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
        b_enabled, b_age, _, c_days, c_name = self.backup_and_certs(client)
        sync_states = client.try_list("/policy/api/v1/infra/full-sync-states")
        not_done = [s for s in sync_states if str(s.get("last_completed_stage", "")).upper() not in ("COMPLETED", "")]
        self.metrics(lo, {
            "nodes_online": len(mcs.get("online_nodes") or []) if cs_ok else None,
            "nodes_offline": len(mcs.get("offline_nodes") or []) if cs_ok else None,
            "alarms_open_critical": alarms["critical"], "alarms_open_high": alarms["high"], "alarms_open_other": alarms["other"],
            "backup_age_hours": b_age, "cert_min_days_to_expiry": c_days,
            "full_sync_failed": len(not_done) if client.ok("/policy/api/v1/infra/full-sync-states") else None,
        })
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
        for a in [a for a in all_alarms if str(a.get("feature_name", "")).lower() == "federation"][:20]:
            sev = str(a.get("severity", "")).upper()
            self.event(lo, f"NSX alarm {a.get('event_type')}: {a.get('summary', '')}"[:400],
                       Criticality.CRITICAL if sev == "CRITICAL" else Criticality.IMMEDIATE if sev == "HIGH" else Criticality.WARNING)

        # sync channels as seen from this location (to the Global Managers and to the other locations)
        st = client.try_get("/api/v1/sites/status", default={}) or {}
        for rc in st.get("remote_connections", []):
            self.channel(lo, name, rc)
            stubs = rc.get("stubs") or []
            if not (bool(stubs) and any(s.get("connection_up") for s in stubs)) and not site_down:
                co = self.obj("sync_channel", f"{name} -> {rc.get('site_name')}", f"chan:{name}>{rc.get('site_name')}")
                self.event(co, f"Sync channel from {name} to {rc.get('site_name')} is down", Criticality.IMMEDIATE, "connection_up")

        # edge clusters that carry RTEPs, their nodes, inter-site BGP and tunnel statistics
        inv = self.cache.cached(f"lm_inventory:{client.host}", INVENTORY_TTL, lambda: self.load_lm_inventory(client))
        bgp_total = bgp_up = 0
        bgp_known = True
        for ec in inv["edge_clusters"]:
            eo = self.obj("edge_cluster", f"{ec['name']} ({name})", f"ec:{ec['id']}")
            eo.add_parent(lo)
            if self.cfg.link_nsx_objects and lm_cluster_id:
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
                # The object is identified by location and node name, not by the NSX node ID: an edge node that is
                # redeployed under the same name stays the same object and keeps its history.
                edge_key = f"{loc['site_id']}:{tn_name}"
                no = self.obj("edge_node", f"{tn_name} ({name})", f"edge:{edge_key}")
                no.add_parent(eo)
                if self.cfg.link_nsx_objects and lm_cluster_id:
                    no.add_child(self.nsx_object("TransportNode", tn_name, tn_id, lm_cluster_id, zone=True))
                total, up = int(m.get("total_bgp_sessions") or 0), int(m.get("established_bgp_sessions") or 0)
                bgp_state = {(n.get("source_address"), n.get("neighbor_address")): str(n.get("connection_state", "")).upper()
                             for n in m.get("neighbor_status") or []}
                stats = client.try_get(f"/api/v1/transport-nodes/{tn_id}/inter-site/statistics", default={}) or {}
                tunnels = 0
                seen = set()
                for per_site in stats.get("stats_per_site") or []:
                    remote = (per_site.get("remote_site") or {}).get("target_display_name") or "?"
                    for t in per_site.get("stats_per_tunnel") or []:
                        src, dst = t.get("tunnel_source_address"), t.get("tunnel_destination_address")
                        seen.add((src, dst))
                        tunnels += 1
                        self.tunnel(no, edge_key, tn_name, src, dst, remote, t, bgp_state.get((src, dst)))
                for (src, dst), state in bgp_state.items():  # sessions with no tunnel statistics yet
                    if (src, dst) not in seen:
                        tunnels += 1
                        self.tunnel(no, edge_key, tn_name, src, dst, "?", {}, state)
                self.metrics(no, {"intersite_bgp_total": total, "intersite_bgp_established": up, "rtep_tunnels": tunnels})
                self.props(no, {"location": name})
                if up < total and not site_down:
                    self.event(no, f"Edge node {tn_name} at {name}: {total - up} of {total} inter-site BGP sessions are not established",
                               Criticality.IMMEDIATE, "intersite_bgp_down")
        if bgp_known:
            self.metrics(lo, {"intersite_bgp_total": bgp_total, "intersite_bgp_established": bgp_up})

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
                    bo.add_parent(t0_obj or lo)
                    state = str(n.get("connection_state", "")).upper()
                    self.metrics(bo, {
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
            stats = client.try_get(f"{LGI}/domains/{pol.get('domain_id', 'default')}/security-policies/{pol['id']}/statistics",
                                   default={}) or {}
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
            self.counters(ro, f"real:{pol['path']}@{loc['path']}", totals)
            loc.setdefault("_policy_hits", {})[pol["path"]] = (totals, per_rule)

    def tunnel(self, node: Object, tn_id: str, tn_name: str, src: Any, dst: Any, remote: str, t: dict, bgp_state: Optional[str]) -> None:
        to = self.obj("rtep_tunnel", f"{tn_name}: {src} -> {dst}", f"rtep:{tn_id}:{src}>{dst}")
        to.add_parent(node)
        rx, tx = t.get("rx") or {}, t.get("tx") or {}
        self.counters(to, f"rtep:{tn_id}:{src}>{dst}", {
            "rx_bytes": rx.get("total_bytes"), "tx_bytes": tx.get("total_bytes"),
            "rx_packets": rx.get("total_packets"), "tx_packets": tx.get("total_packets"),
            "rx_dropped": rx.get("dropped_packets"), "tx_dropped": tx.get("dropped_packets"),
            "rx_blocked": rx.get("blocked_packets"), "tx_blocked": tx.get("blocked_packets"),
            "bgp_established": None if bgp_state is None else (1 if bgp_state == "ESTABLISHED" else 0),
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

    def finish_policies(self, lm_sites: List[Dict[str, Any]]) -> None:
        """Sum the per-location policy statistics, and create rule objects for the selected policies."""
        by_path = getattr(self, "stretched_by_path", {})
        for pol in getattr(self, "inventory", {}).get("global_policy", []):
            po = by_path.get(pol["path"])
            if po is None:
                continue
            total_hits = 0
            rule_totals: Dict[str, Dict[str, int]] = {}
            for loc in lm_sites:
                totals, per_rule = (loc.get("_policy_hits") or {}).get(pol["path"], ({}, {}))
                total_hits += int(totals.get("hit_count", 0))
                for rk, vals in per_rule.items():
                    agg = rule_totals.setdefault(rk, {})
                    for k, v in vals.items():
                        agg[k] = agg.get(k, 0) + v
            self.counters(po, f"policy:{pol['path']}", {"hit_count": total_hits})
            wanted = self.cfg.rule_policies
            if wanted and ("*" in wanted or pol["id"] in wanted):
                for r in pol.get("rules") or []:
                    ro = self.obj("rule", f"{pol.get('display_name')} / {r.get('display_name') or r.get('id')}", f"rule:{r.get('path')}")
                    ro.add_parent(po)
                    vals = rule_totals.get(str(r.get("id"))) or rule_totals.get(str(r.get("rule_id"))) or {}
                    self.counters(ro, f"rule:{r.get('path')}",
                                  {k: vals.get(k, 0) for k in ("hit_count", "packet_count", "byte_count", "session_count")})
                    self.props(ro, {"action": r.get("action"), "disabled": bool(r.get("disabled")),
                                    "sources": ", ".join(tail(g) for g in r.get("source_groups") or []),
                                    "destinations": ", ".join(tail(g) for g in r.get("destination_groups") or [])})


# ---- SDK entry points -----------------------------------------------------------------
def test(adapter_instance: AdapterInstance) -> TestResult:
    with Timer(logger, "Test"):
        result = TestResult()
        try:
            cfg = Config(adapter_instance)
            if not cfg.gm_hosts:
                result.with_error("Enter at least one Global Manager address")
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
