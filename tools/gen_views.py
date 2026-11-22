#!/usr/bin/env python3
"""Generate the list views and the report of the NSX Federation pack.

Writes pack/content/reports/NSX_Federation/{content.xml,resources/content.properties} and, with
--zip <file> --marker <file>, a content-import zip to test the views without a pack install.
"""
import io
import typing
import json
import os
import sys
import uuid
import zipfile
from xml.sax.saxutils import escape, quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AK = "NSXFederation"
OUT = os.path.join(ROOT, "pack/content/reports/NSX_Federation")
NS = uuid.UUID("6f1d2c0e-0000-4000-8000-4e5358464544")
uid = lambda *p: str(uuid.uuid5(NS, "view/" + "/".join(p)))

_src = open(os.path.join(ROOT, "pack/app/adapter.py")).read()
_ns = {k: getattr(typing, k) for k in ("Dict", "Tuple", "List", "Optional", "Any")}
exec(_src[_src.index("RATES = {"):_src.index("def get_adapter_definition")], _ns)  # rates, model, metric groups
fk = _ns["fk"]
MODEL = _ns["MODEL"]
LABEL = {k: {**dict(MODEL[k][1]), **dict(MODEL[k][2])} for k in MODEL}
IS_PROP = {k: {p for p, _ in MODEL[k][2]} for k in MODEL}

# view title -> (object type, description, [(metric or property key, column label)])
VIEWS = [
    ("NSX Federation - Locations", "location", "State of each location of the federation.", [
        ("version", "Version"), ("gm_connection_up", "GM connection"), ("collector_reachable", "Collector reach"),
        ("intersite_bgp_established", "Inter-site BGP up"), ("intersite_bgp_down", "Inter-site BGP down"),
        ("full_sync_failed", "Full sync failed"), ("nodes_offline", "Nodes offline"), ("backup_age_hours", "Backup age (h)"),
        ("cert_min_days_to_expiry", "Cert expiry (days)"), ("alarms_open_critical", "Critical alarms"),
        ("alarms_open_high", "High alarms"), ("upgrade_status", "Upgrade"),
        ("replication_flows_unhealthy", "Replication flows in error"), ("minutes_since_collection", "Last collected (min ago)")]),
    ("NSX Federation - Sync channels", "sync_channel", "Connections between the managers of the federation.", [
        ("source", "Source"), ("target", "Target"), ("target_address", "Target address"), ("connection_up", "Up")]),
    ("NSX Federation - RTEP tunnels", "rtep_tunnel", "Inter-site tunnels between edge nodes, with rates.", [
        ("remote_location", "Remote location"), ("bgp_state", "BGP state"), ("rx_bytes_per_s", "Rx bytes/s"),
        ("tx_bytes_per_s", "Tx bytes/s"), ("rx_dropped_per_min", "Rx drops/min"), ("tx_dropped_per_min", "Tx drops/min"),
        ("rx_blocked_per_min", "Rx blocked/min"), ("tx_blocked_per_min", "Tx blocked/min")]),
    ("NSX Federation - Tier-0 BGP neighbors", "bgp_neighbor", "BGP sessions of the stretched Tier-0 gateways.", [
        ("location", "Location"), ("session_type", "Type"), ("state", "State"), ("remote_as", "Remote AS"),
        ("prefixes_in", "Prefixes in"), ("prefixes_out", "Prefixes out"), ("connection_drops", "Drops"),
        ("seconds_established", "Uptime (s)")]),
    ("NSX Federation - Edge capacity", "edge_node", "CPU, memory and disk of the federation edge nodes.", [
        ("location", "Location"), ("edge_status", "Status"), ("maintenance_mode", "Maintenance"),
        ("datapath_cpu_pct", "Datapath CPU (%)"), ("datapath_cpu_peak_pct", "Busiest datapath core (%)"),
        ("services_cpu_pct", "Services CPU (%)"), ("memory_used_pct", "Memory (%)"),
        ("datapath_pool_peak_pct", "Fullest datapath pool (%)"), ("disk_fullest_pct", "Fullest file system (%)")]),
    ("NSX Federation - Tier-0 service routers", "tier0_sr", "HA state and routing of each stretched Tier-0 gateway on each edge.", [
        ("tier0", "Tier-0"), ("location", "Location"), ("edge", "Edge"), ("ha_status", "HA state"),
        ("routing_status", "Routing"), ("bgp_neighbors_down", "BGP neighbors down"), ("last_ha_change", "Last HA change")]),
    ("NSX Federation - Recent changes", "federation", "The changes the collection saw, newest first.", [
        ("changes_detected", "Changes in the last cycle"), ("last_change", "Last change"), ("recent_changes", "Recent changes")]),
    ("NSX Federation - Realization per location", "realization", "Each stretched object at each location of its span.", [
        ("object_type", "Object type"), ("location", "Location"), ("status", "Status"), ("realized", "Realized")]),
]
REPORT = ("NSX Federation - Health report",
          "Locations, sync channels, RTEP tunnels, Tier-0 BGP neighbors, edge capacity, Tier-0 service routers, "
          "recent changes and the realization of stretched objects.")


def view_xml(title, kind, desc, cols, n):
    items = []
    for key, label in cols:
        prop = key in IS_PROP[kind]
        assert key in LABEL[kind], (kind, key)
        items.append(
            '<Item><Value><Property name="objectType" value="RESOURCE"/>'
            f'<Property name="attributeKey" value="{key if prop else fk(kind, key)}"/>'
            f'<Property name="isStringAttribute" value="{"true" if prop else "false"}"/>'
            f'<Property name="adapterKind" value="{AK}"/><Property name="resourceKind" value="{kind}"/>'
            + ('' if prop else '<Property name="rollUpType" value="NONE"/>') +
            '<Property name="rollUpCount" value="0"/>'
            '<Property name="transformations"><List><Item value="CURRENT"/></List></Property>'
            f'<Property name="isProperty" value="{"true" if prop else "false"}"/>'
            f'<Property name="displayName" value={quoteattr(label)}/>'
            '<Property name="addTimestampAsColumn" value="false"/><Property name="isShowRelativeTimestamp" value="false"/>'
            '</Value></Item>')
    return (f'<ViewDef id="{uid(title)}"><Title>{escape(title)}</Title><Description>{escape(desc)}</Description>'
            f'<SubjectType adapterKind="{AK}" resourceKind="{kind}" type="descendant"/>'
            f'<SubjectType adapterKind="{AK}" resourceKind="{kind}" type="self"/>'
            '<Usage>dashboard</Usage><Usage>report</Usage><Usage>details</Usage><Usage>content</Usage><Controls>'
            f'<Control id="time-interval-selector_id_{n}1" type="time-interval-selector" visible="false">'
            '<Property name="advancedTimeMode" value="false"/><Property name="unit" value="DAYS"/><Property name="count" value="7"/></Control>'
            f'<Control id="attributes-selector_id_{n}2" type="attributes-selector" visible="false">'
            f'<Property name="attributeInfos"><List>{"".join(items)}</List></Property></Control>'
            f'<Control id="pagination-control_id_{n}3" type="pagination-control" visible="true">'
            '<Property name="start" value="0"/><Property name="size" value="50"/></Control>'
            f'<Control id="metadata_id_{n}4" type="metadata" visible="false"><Property name="maxPointsCount" value="5000"/>'
            '<Property name="hideObjectNameColumn" value="false"/><Property name="listTopResultSize" value="-1"/></Control></Controls>'
            f'<DataProviders><DataProvider dataType="list-view" id="list-view_id_{n}0"/></DataProviders>'
            '<Presentation type="list"/></ViewDef>')


def views_doc():
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Content><Views>'
            + "".join(view_xml(t, k, d, c, 900 + i) for i, (t, k, d, c) in enumerate(VIEWS)) + '</Views></Content>\n')


def report_xml():
    sections = ('<Section><ContentType>CoverPage</ContentType><ContentKey>COVER_PAGE</ContentKey></Section>'
                '<Section><ContentType>TableOfContents</ContentType><ContentKey>TABLE_OF_CONTENTS</ContentKey></Section>')
    for t, *_ in VIEWS:
        sections += (f'<Section><ContentType>View</ContentType><ContentKey>{uid(t)}</ContentKey>'
                     '<ContentOrientation>Landscape</ContentOrientation>'
                     '<ContentFormatting><ColorizeListView>true</ColorizeListView></ContentFormatting></Section>')
    return (f'<ReportDef id="{uid("report", REPORT[0])}"><isTenant>false</isTenant><Title>{escape(REPORT[0])}</Title>'
            f'<Description>{escape(REPORT[1])}</Description>'
            f'<SubjectType adapterKind="{AK}" resourceKind="federation" type="self"/>'
            f'<Sections>{sections}</Sections>'
            '<Settings><ShowPageFooter>true</ShowPageFooter><OutputFormat>pdf</OutputFormat><OutputFormat>csv</OutputFormat></Settings>'
            '</ReportDef>')


def pack_doc():
    """Views and the report in one content.xml, the form the SDK expects under content/reports."""
    v = views_doc()
    return v.replace('</Views></Content>', f'</Views><Reports>{report_xml()}</Reports></Content>')


def properties():
    lines = []
    for t, _k, d, _c in VIEWS:
        lines += [f"view.{uid(t)}.title={t}", f"view.{uid(t)}.desc={d}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    os.makedirs(os.path.join(OUT, "resources"), exist_ok=True)
    open(os.path.join(OUT, "content.xml"), "w").write(pack_doc())
    open(os.path.join(OUT, "resources", "content.properties"), "w").write(properties())
    print("wrote", OUT, len(VIEWS), "views, 1 report")
    if "--zip" in sys.argv:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("content.xml", views_doc())
            z.writestr("resources/content.properties", properties())
        with zipfile.ZipFile(sys.argv[sys.argv.index("--zip") + 1], "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("views.zip", inner.getvalue())
            z.writestr("configuration.json", json.dumps({"views": len(VIEWS), "type": "CUSTOM"}))
            z.write(sys.argv[sys.argv.index("--marker") + 1], "6844548499441080431L.v1")
