# Operating the NSX Federation pack

## Account

| Setting | Value |
| --- | --- |
| Global Manager addresses | The cluster address of every Global Manager cluster of one federation, active and standby. One account monitors one federation; a second federation needs a second account |
| Credential | A read-only NSX account. The built-in `audit` account was tested. A separate account for the Local Managers is optional |
| Certificate trust | `enforce` (default), `ca` or `report`, see below |
| Trusted CA certificates (PEM) | Optional, for `ca`: the root or issuing CA certificates of the managers |
| Federation name | Optional name of the federation object. Set it when VCF Operations monitors several federations |
| Link to the objects of the NSX integration | `auto` (default): edge nodes and edge clusters are linked to the objects of the built-in NSX integration for a Local Manager that integration monitors. `on`: always. `off`: never |
| Alerts for the federation alarms of a Local Manager | `auto` (default): raised by this pack only for a Local Manager the built-in NSX integration does not monitor. `always`, `never` |
| Change events | `on` (default): each change the collection sees is an information event on its object. `off`: the changes are only listed on the federation. See "Change timeline" |
| Delete objects that left NSX after (days) | `0` (default): never. See "Objects that no longer exist" |
| Policies collected rule by rule | Empty by default. Rules become objects only for the listed policies |
| Unused firewall rule period (days) | `30` (default): a rule of a global security policy with no new hit at any location of its span for that long is reported as unused. `0`: off. See "Password expiry, firewall rule review and availability" |
| API call budget per manager and cycle | 2000 by default, see Sizing |
| Local Manager address overrides | Optional: `location-name=address;location-name=address`, when the collector cannot reach a Local Manager on the address the Global Manager knows |
| Managers collected in parallel | 8 by default |
| API request timeout (s) | 30 by default |
| Adapter Memory Limit (MB) | 1024 by default: the memory VCF Operations allocates to the adapter container |

## Certificate trust

The adapter reads the certificate a manager presents and compares its SHA-256 fingerprint with the trusted
ones. Every connection of the cycle is then bound to that fingerprint, so the credentials never go to a server
that presents another certificate.

- A Global Manager is trusted by the certificate accepted on the account in VCF Operations.
- A Local Manager is trusted by the thumbprint the Global Manager holds for it.
- `enforce`: a manager is not collected when its certificate is not a trusted one, or when no trusted
  certificate is on record for it. The alert names the manager.
- `ca`: as `enforce`, and a certificate is also trusted when it chains to a trusted CA and is valid for the
  address the adapter uses (a DNS name or an IP address in its subject alternative names). The trusted CAs are
  the certificates in "Trusted CA certificates (PEM)", any CA certificate accepted on the account, and the
  OpenSSL store of the adapter container image. CAs imported into the cloud proxy or into VCF Operations are
  not in that store: paste them in the setting. Each manager address must match its certificate. Use the FQDN
  of the Global Manager cluster, and set an override for a Local Manager that the Global Manager knows by an
  address that is not in the certificate. The connection test reports a block of the PEM setting that is not a
  certificate, and text with no PEM block at all.
- `report`: the manager is collected and the mismatch raises an alert on the location.

The property "Certificate trust" of each collected Global Manager and location says how its certificate was
trusted: `trusted` (pinned) or `trusted (CA chain)`. In `report` mode an untrusted certificate reads
`NOT TRUSTED (reported only)`. A location left uncollected because of its certificate reads `NOT TRUSTED`.
When the chain check fails, the OpenSSL reason (an unknown issuer, a name mismatch) is in the property
"Collector error" of the location, or, for a Global Manager, in "Global Managers not collected" on the
federation.

An account created through the API has no accepted certificate. With `enforce` it collects nothing until the
certificates are accepted in the UI (edit the account, validate the connection, accept). With `ca` it collects
as soon as the managers present certificates signed by a trusted CA.

## Sizing

Calls per collection cycle, measured with the test suite:

- Active Global Manager: about 20, plus one per location and one per stretched object (Tier-0, Tier-1, segment,
  global security policy). Above 200 stretched objects, the realization of an object is read again only when the
  object is new, changed in NSX or not fully realized, or once in six cycles. Otherwise the last state is used.
- Standby Global Manager: 7.
- Each Local Manager: about 12, plus one per edge cluster, three per edge node, one per Tier-0 locale and one per
  collected policy.
- Every 30 minutes, two more per Local Manager (configuration limits, overrides). Every hour, a few more per
  manager (NTP servers, local users, backup, certificates).

The first cycle after a start reads every object, so the budget of 2000 calls per manager covers about 1,800
stretched objects in that cycle. The following cycles need about a sixth. For more objects, raise the budget and
watch the metric "Collection duration": a cycle has to finish inside the collection interval (5 minutes by
default). The alert "NSX Federation collection is slow or throttled" fires above 4 minutes or when NSX answers
HTTP 429. NSX limits each client to 100 requests per second by default.

Only a two-location federation was measured on real managers. Larger sizes ran against the mock NSX of the test
suite (`test_scale_sizing`), with 1,000 more segments, 200 more Tier-1 gateways and 100 more global security
policies of 50 rules each (1,310 stretched objects):

| Cycle | Active Global Manager | Busiest Local Manager | Realization states read |
| --- | --- | --- | --- |
| First after a start (empty cache) | 1,440 calls | 126 calls | 1,310 |
| Following cycles | 233 calls | 115 calls | 218 |

The duration of a cycle depends on how fast the managers answer. In the lab a call took 0.2 to 0.5 seconds,
and the adapter reads up to 8 managers in parallel ("Managers collected in parallel"). Watch the calls of the
active Global Manager: at about 1,800 stretched objects the first cycle after a start reaches the budget of
2000. That cycle is then partial (alert "NSX Federation collection is partial"), and the next ones complete it.

## Alerts during a location outage

When the active Global Manager reports a location as not connected, the adapter writes the name of that location
to the property "Locations in outage" of the objects that depend on it: the location itself, its edge clusters,
edge nodes and BGP neighbors, the tunnels and sync channels with one end at that location, the stretched objects
whose span includes it and their realization there, and the Global Managers. The alerts of these objects require
the property to read "none". They stay quiet, and the alert of the location is the one to act on. If their
fault is still there, they fire once the outage symptom has cleared, three cycles after the location is back.
Objects of the other locations are not affected: a fault there raises its alert.

The alarm alerts of the Global Manager and of the locations are quiet in the same way, except the alarms between
Global Managers. A stretched object is quiet while any location of its span is in outage; its realization at
each of the other locations still raises its own alert. The outage symptom clears three cycles after the
location is back, one cycle after the symptoms it keeps quiet, so the end of an outage raises no alert either.

When a Global Manager does not answer, the alerts of its own sync channels are quiet behind the alert on the
federation. When no Global Manager is active, the same applies to every channel with a Global Manager at one
end. Channels between two locations are never quiet for that reason.

A second, unrelated fault on the objects of the location in outage raises no alert while the outage lasts. Its
metrics and properties are still collected.

For planned work, use a VCF Operations maintenance schedule on the group "NSX Federation - All objects".

## Latency, packet loss and BGP flaps

- Latency: the adapter reads the latency NSX measures on each flow between the Global Manager and a location,
  from `GET /global-manager/api/v1/observability/flow-details` on the active Global Manager. This is the call
  behind the "Latest Latency" column of the Location Manager page. The metric "Round-trip time to the Global
  Manager" is the higher value of the two flows of the location. "% of the maximum RTT setting" compares it with
  the maximum RTT set for the location in NSX. An NSX without that call answers 404, and the adapter then uses
  the round-trip time of the replication status, which NSX 9.1 leaves empty. NSX measures no latency between
  the two Global Managers. The alert fires above 150 ms, the limit Broadcom documents for locations that share
  stretched networks (500 ms otherwise), or above 80% of the setting (warning), and above 100% (immediate).
  With the default setting of 250 ms, 80% is 200 ms, so the 150 ms condition fires first. The same alert also
  fires when the round-trip time leaves the range VCF Operations learned for the location (dynamic threshold,
  three cycles), which reports a degraded WAN before it reaches 150 ms. The latency alarms of NSX itself
  (between Global Managers, and between a Global Manager and a location) raise their own alerts.
- Packet loss: for each RTEP tunnel and direction, the dropped packets of the interval are divided by the
  packets of the interval. NSX also counts a dropped packet in the packet total. In the lab, the received total
  of a tunnel matched the sent total of its peer exactly while one packet was counted as dropped. Only receive
  drops were seen; the send counters are assumed to work the same way. The percentage is sent once at least 50
  packets crossed the tunnel since the previous cycle, so an idle tunnel does not show 50% for one lost packet.
  The alert needs both more than 1% (5% for immediate) and more than 10 drops per minute, for two cycles.
- Sync queues: the same call gives the size and the maximum size of the sync queues of each flow. "Sync queue
  used" is the fullest queue of the location, in percent. It has no alert of its own: NSX raises the alarm
  queue_occupancy_threshold_exceeded, which the pack turns into an alert.
- Blocked packets: NSX counts them separately from drops. More than 10 per minute in one direction for two
  cycles raises an alert, since a firewall on the inter-site path is the usual cause.
- BGP flaps: the connection drop counter of each Tier-0 BGP neighbor, and of the inter-site session of each RTEP
  tunnel, gets a rate. The inter-site counter comes from `GET /api/v1/transport-nodes/<id>/inter-site/bgp/summary`
  on the Local Manager, one call per edge node. A drop since the previous cycle, with the session established in
  the current cycle, raises a warning that stays for two cycles. A session that is down in the current cycle
  raises no flap alert; the "not established" alert follows if it stays down. A restart of the edge resets the
  counter, which gives a rate of 0 for that cycle, so a restart is not reported as a flap.

## Edge capacity and Tier-0 service routers

One call per federation edge node, `GET /api/v1/transport-nodes/<id>/status` on its Local Manager, gives both:

- Capacity, on the edge node: in percent, the average usage of the datapath (DPDK) cores and of the busiest
  one, the average usage of the other cores (services), system memory, datapath memory, the fullest datapath
  memory pool and the fullest file system. NSX gives current values only; VCF Operations keeps the history, and
  the trend and the dynamic threshold come from there. The alerts fire after three cycles above 75% datapath CPU
  (85% immediate), 80% memory (90% immediate), 85% for a memory pool, and 80% for a file system (90%
  immediate). The status alert is quiet while the edge is in, entering or leaving maintenance mode, and for
  three cycles after. The status and threshold alerts are quiet for an edge whose Local Manager the built-in NSX
  integration monitors, since that integration raises the NSX alarms of the edge. The property "Edge status and
  capacity alerts raised by" on the edge node says which one raises them. This pack always raises the alert on
  the usual range of the datapath CPU.
- Service routers: one object "Tier-0 Service Router" per stretched Tier-0 gateway and edge node, keyed by the
  NSX IDs of both, with the HA state, the routing status and the BGP neighbors up and down that NSX reports for
  it. Tier-0 gateways local to one location are left out. On the stretched Tier-0 gateway, the adapter counts
  the service routers, the active ones and those with routing down, over the locations of its span. For an
  active-active gateway it also counts the locations of the span without an active service router. For an
  active-standby gateway, one active service router in the federation is the expected state, so only "no active
  service router" raises an alert. When the span is not known (realization not read), the locations where the
  gateway has service routers are used. When an edge of the span does not answer, the totals of that gateway are
  not sent for the cycle. A location whose edges all answered and that has no active service router is still
  counted, and HA state changes are still counted. A service router that a responding edge no longer reports
  reads ABSENT (for a day at most), so a failover through a rebooting edge is seen. The alert on a down service
  router is quiet while its edge is in, entering or leaving maintenance mode. A service router that changes HA
  state between two cycles (a failover) is a change in the timeline below and raises a warning on the gateway
  for two cycles.

## Change timeline

The adapter keeps what it saw in its cache and compares each cycle with the previous ones:

- the active Global Manager, and the role and the version of each Global Manager;
- the locations the active Global Manager lists (added, removed, renamed) and their versions;
- the edge nodes of the RTEP edge clusters of each location (added, removed, renamed);
- the HA state of each Tier-0 service router;
- the stretched gateways, segments and global security policies (created, changed, deleted). A change is a new
  revision in NSX, and the message names the user NSX recorded. The list is the inventory of the Global
  Manager, which the adapter reads again every 30 minutes, so such a change shows up within 30 minutes. A change
  of the primary location of a Tier-0 or Tier-1 gateway gets its own message.

Each change is an information event on the object it concerns (on the federation for an object that no longer
exists), at most 25 per cycle. VCF Operations shows it as a notification on that object, and it clears after a
cycle. On the federation, "Changes detected in this cycle" counts them. "Last change" and "Recent changes" (the
last ten, newest first, with the time in UTC) list them, so the property history of the federation is the
timeline. The first cycle after an empty cache (new container, new account) only records the state. A cycle in
which a call failed reports no removal, and an object is reported as added only against a complete earlier
read, so a gap in the data does not show up as a change.

## Clocks, configuration limits and overrides

- Clocks: in every cycle the adapter reads the system time of each manager (`GET /api/v1/node/status`) and
  subtracts the midpoint of the call, which gives the offset of the manager against the cloud proxy. The calls
  are timed on a monotonic clock from one reading of the cloud proxy clock per cycle. The difference between two
  managers read in the same cycle therefore does not depend on that clock, even when it steps during the cycle.
  Each Global Manager and location gets its difference with the active Global Manager ("Clock difference with
  the active Global Manager"). A property says ahead or behind, and the precision of the reading (half the
  duration of the two calls). A call that takes more than 3 seconds gives no reading. The alert fires above 5
  seconds (60 seconds immediate) for two cycles, or when the NTP service of the manager is not running. The NTP
  servers come from `GET /api/v1/node/services/ntp` once an hour, and the state of the service from its status
  call in every cycle. The node that answers behind the cluster address is the one read.
- Configuration limits: `GET /policy/api/v1/infra/capacity/dashboard/usage` on each Local Manager, every 30
  minutes. This is the data of the Capacity page of NSX. NSX rates each limit with its own thresholds (70% and
  100% by default, set in NSX). Its WARNING counts as warning; its CRITICAL or ERROR, or a limit reached (100% or
  more), counts as critical. A limit whose maximum is 1, such as the one compute manager of a Local Manager, is
  full by design and left out. The location gets the most used limit, the number of limits at warning and at
  critical, and a list of the limits above a threshold. The Global Manager has no such call. Global objects
  count at every location of their span, so the locations show them. For a Local Manager the built-in NSX
  integration monitors, the alert is left to that integration, because NSX raises its own capacity alarms and
  the integration forwards them (property "Configuration limit alerts raised by"). The setting for the
  federation alarms does not change this. The values are sent only when the Local Manager answers in the cycle,
  so a cached value never stands for a manager that is down.
- Overrides: a search for `overridden:true` on each Local Manager, every 30 minutes, lists the global objects
  that were changed locally. Each one counts on the location and is listed. A new override, or a removed one,
  is a change in the timeline.

## Password expiry, firewall rule review and availability

- Password expiry: once an hour the adapter reads `GET /api/v1/node/users` on each manager (the `audit` account
  can) and finds the account it collects with. NSX gives the days since the last change and the days a password
  is valid (90 by default). The difference is "Days until the password of the collection account expires". The
  alert fires below 30 days (warning), below 7 days (critical), and on the last day or once expired (immediate).
  An account whose password never expires reads 9999, so an alert raised before the expiry was turned off
  clears. An account that NSX does not hold locally (LDAP or VIDM) gets no value and a property saying its
  directory sets the expiry. When the password expires, NSX refuses the logins and the collection of that
  manager stops, so the alert comes first.
- Firewall rule review: the adapter checks three things in every global security policy.
  - Shadowed rules: an enabled rule placed after an enabled rule of the same policy that matches all its
    traffic, meaning the same or wider sources, destinations, services, context profiles and Applied To, and
    the same or both directions and IP versions. Group membership is not expanded, and negated source or
    destination lists must be identical, so the check reports only rules that can never be hit.
  - Rules that allow any traffic: an enabled ALLOW rule with any source, destination, service and profile, in a
    rule and a policy that apply everywhere.
  - Unused rules: the hit counts of each rule, summed over the locations of its span, are compared between
    cycles. A rule whose count has not grown for the unused-rule period (30 days by default) is unused. For a
    rule that never had a hit, the period starts the day the adapter first saw it, so after an install nothing
    is unused for 30 days. A counter reset (a smaller count) restarts the period. When a location does not
    answer, the number is not sent for that cycle and the period is kept. The dates live in the adapter cache,
    so a new adapter container (an upgrade of the pack) restarts the period.

  The system policies of NSX (`default-` IDs) are left out, as in the rest of the collection. The alert "Global
  security policy has rules that are never hit" is information; "Global security policy allows any traffic" is a
  warning. Both count against the Risk badge.
- Availability: in every cycle, a value of 0 or 100 for the federation (exactly one Global Manager answers in
  the active role), each Global Manager (reachable, no node offline), each location (healthy) and each stretched
  Tier-0 (an active service router where its HA mode needs one). Its average over a period is the share of that
  period the object was available. The report "NSX Federation - Availability report" shows it for the last 30
  days, with the lowest values, the inter-site BGP and RTT figures and the number of Tier-0 HA changes. Schedule
  it monthly on the federation object (Visualize, Reports). Cycles in which the adapter did not run send no
  value, so they count neither way.

## Groups per federation

The custom groups of the pack (All objects, Managers and locations, Inter-site connectivity, Stretched objects)
hold the objects of every federation. Every object of the pack also carries the property `federation_id`, and
`install/federation_groups.py` uses it to create one group per federation, "NSX Federation - <federation>"
(a federation name that already starts with "NSX Federation" does not get the prefix twice). Its rule is "object type of the pack and
`federation_id` equals the ID of the federation", so the group follows the objects as they come and go, and an
object of another federation never enters it. Objects that no longer get data (for example the ones keyed by
name before 1.9.0) do not carry the property and stay out.

```bash
export OPS_HOST=<vcf-operations-fqdn> OPS_PW='<admin password>'
python3 install/federation_groups.py                       # every federation
python3 install/federation_groups.py --federation '<federation>' --policy '<policy>'   # and assign a policy
```

The federation name is the name of the federation object in VCF Operations (the account setting "Federation
name", or else "NSX Federation" and the first 8 characters of its ID). The script needs one collection with 1.13.0 or later.
Running it again updates the group and keeps its ID, so assignments, notification rules and dashboards that use
the group stay in place. It never deletes a group. Renaming the federation creates a new group; delete the old
one by hand.

Use the group to assign a policy, to scope a notification rule (`--group` below) or to filter a dashboard or a
view to one federation.

## Sending the alerts

`install/notification_rules.py` creates a notification rule for the alerts of the pack. The outbound instance
(email server, webhook, ServiceNow, Slack, SNMP trap or log file) must exist first: Administration,
Configurations, Outbound Settings. The script does not handle mail or ticketing credentials.

```bash
python3 install/notification_rules.py --outbound '<outbound instance>'      # immediate and critical, every federation
python3 install/notification_rules.py --outbound '<outbound instance>' --min-criticality warning \
        --group 'NSX Federation - <federation>' --name 'NSX Federation - <federation> warnings'
```

| Option | Effect |
| --- | --- |
| `--min-criticality` | `immediate` (default): immediate and critical alerts. `warning` adds the warnings, `critical` keeps only the critical ones. Information alerts (firewall rule review, upgrade in progress) are never sent |
| `--group` | Only the alerts on the members of this custom group, for example the group of one federation |
| `--template` | A payload template of the plugin type; default: the built-in one ("Default Webhook Template", "Default Email Template", ...) |
| `--name` | The rule name; default "NSX Federation - <outbound instance>". The same name again updates the rule |

The rule takes alerts that are new, updated and cancelled, so a ticket or a chat thread also learns that the
alert cleared. It covers every object type of the pack, including the alerts raised from NSX alarms and events.
While a location is cut off from the Global Manager, the alerts that depend on it stay quiet (see "Alerts during a
location outage"), so the outage sends one alert instead of one per object.

## Changing a threshold

[FEATURES.md](FEATURES.md#thresholds) lists the thresholds of the pack. They are part of the symptom definitions
and the same for every site.

To use another value without rebuilding the pack, override the symptom in the policy of the objects:

1. Administration, Configurations, Policy Definition: select the policy and Edit.
2. Open Alerts and Symptoms, then the Symptom Definitions tab.
3. Pick NSX Federation in the object types, or type the name of the symptom in the filter.
4. Select the symptom, change the value under Override Symptoms, then Save.

To change the value for everyone, edit the table `S` in `tools/gen_alerts.py`, run the generators and build a
new version. Some symptom and alert names carry the value (for example "more than 1% of the received packets"):
change the name in the same table.

## Collection health

On the federation object:

| Metric or property | Meaning |
| --- | --- |
| NSX API calls without an answer | Calls that failed in the last cycle. The data behind them is unknown and is not sent |
| NSX API calls without an answer (property) | Manager, call and HTTP status of each failed call |
| NSX API calls retried after a timeout | A call is retried once after a timeout or a dropped connection |
| Global Managers not collected | Address and reason: not reachable, certificate not trusted, or another federation. A Global Manager known from an earlier cycle (or, after a restart of the adapter container, from VCF Operations) that does not answer, or that answers for another federation, reads 0 on its metric "Reachable" |
| Collection mode | `normal`, or `degraded` with the reason (no Global Manager answers, none is active, or the active one did not return its locations) and the source of the location list (the cache or VCF Operations) |

A location outside the known span of a policy is not asked for its statistics. When the span is not known yet,
an HTTP 404 from a Local Manager means the policy does not exist there, and it does not count as a failed call.
Where the policy is realized, a 404 is a failed call and the counters of the policy are not sent for that cycle.
A location that keeps answering 404 for a realized policy keeps the "data gaps" alert open.

A "full sync did not complete" or "not realized" condition has to last two cycles before its alert fires, so a
sync or a change that completes within a cycle or two raises nothing.

A sync channel for which the active Global Manager lists no connection detail is reported as unknown: no value is
sent, and VCF Operations keeps the last one.

HTTP 403 on a call points to missing rights of the account. A Local Manager is reported as not reached when its
cluster status call fails together with most of its other calls (HTTP 401, 403, 5xx or timeouts). A manager
whose requests time out or stay throttled (HTTP 429 or 503 after the retries) three times in a row is skipped
for the rest of the cycle.

One account stays with one federation. If the federation is rebuilt with a new ID, create a new account: when
every listed Global Manager answers for the new federation, the new account follows it. Remove the old account,
because after a restart of its container it can follow the rebuilt federation too, and two accounts would then
collect the same objects. The cache of the location list lives in the adapter container and is lost when the
container restarts.

## Troubleshooting

`install/diagnostics.py` reads, and never changes, what a support request needs: the VCF Operations and pack
versions, each account of the pack (settings, state, collector, last collection), the objects per type and those
without data, the collection health of each federation and the open alerts. It writes everything to one JSON file,
then prints the problems it found and what to check. It reads no credential, but the file holds host names and
addresses; read it before sending it outside the organization. A read-only VCF Operations user is enough.

```bash
python3 install/diagnostics.py            # nsxfederation-diagnostics-<date>-<time>.json
```

| Symptom | Check |
| --- | --- |
| The account stays `FAILED` with an empty message | The cloud proxy could not pull the adapter image: the registry certificate ([BUILD.md](BUILD.md), steps 2 and 4) and the registry log |
| The account test fails on a certificate | Accept the certificate, or use trust `ca` with the CA certificates (see "Certificate trust"). After a certificate change on a manager, accept it again |
| No objects, the account is "Collecting" | The federation object lists the reason under "Global Managers not collected": address, certificate or account |
| "NSX API calls without an answer" above 0 | The property of the same name: the manager, the call and the HTTP status. 403 is a missing right of the account, 429 or 503 is NSX throttling |
| "Managers that used their call budget" above 0 | Raise the API call budget, or leave "Policies collected rule by rule" empty (see "Sizing") |
| Collection mode `degraded` | The reason is in the property: no Global Manager answers, none is active, or the active one did not return its locations |
| "VCF Operations API from the adapter" not `ok` | See "What the adapter reads from VCF Operations" |
| Objects that receive no data | They left NSX (setting "Delete objects that left NSX after (days)"), or they are edge nodes, RTEP tunnels and sync channels keyed by name before 1.9.0 (`cleanup_content.py --legacy-objects`) |
| Alerts of an old version still open | `install/cleanup_content.py <pak> --delete` removes the definitions the new version no longer ships |
| Metrics missing right after an upgrade | Normal after the restart: metrics return after three cycles, rates after four |

The adapter log is on the cloud proxy that runs the account, in the log of the adapter container of the pack.
To see what a collection would return without VCF Operations, run `tools/localrun.py` against the managers from the
build host (README, Layout).

## Upgrade

1. Install the new `.pak` (see the README).
2. Stop and start the account. Check that it reaches "Collecting"; start it a second time if it stays stopped.
3. Metrics appear after three cycles, rates after four.

An upgrade replaces the alert content of the pack but keeps the alert, symptom and recommendation definitions
that the new version no longer ships. `install/cleanup_content.py` compares what VCF Operations holds with what
the PAK ships and deletes those orphans. It only touches the IDs the pack generates
(`AlertDefinition-NSXFederation-*`, `SymptomDefinition-NSXFederation-*`, `Recommendation-df-NSXFederation-*`),
never a definition made in the UI. `install/install_unsigned.sh` runs it after every install. By hand:

```bash
export OPS_HOST=<vcf-operations-fqdn> OPS_PW=<admin password>
python3 install/cleanup_content.py pack/build/NSXFederation_<version>.pak            # report only
python3 install/cleanup_content.py pack/build/NSXFederation_<version>.pak --delete   # delete the orphans
```

### Upgrade from a version before 1.9.0

Edge nodes and RTEP tunnels are keyed by the NSX transport node ID, and sync channels by the site IDs of both
ends. Before 1.9.0 they were keyed by name: a renamed edge node became a new object, and so did the sync
channels of a renamed location or Global Manager. Locations and Global Managers themselves were always keyed by
site ID. After the upgrade, every edge node, RTEP tunnel and sync channel is created again under its new key.
The old objects stop receiving data and keep their history. Delete them once that history is no longer needed:

```bash
python3 install/cleanup_content.py pack/build/NSXFederation_<version>.pak --legacy-objects            # list them
python3 install/cleanup_content.py pack/build/NSXFederation_<version>.pak --legacy-objects --delete   # delete them
```

"Delete objects that left NSX after (days)" also deletes them, after that many days.

## Objects that no longer exist

An object deleted in NSX stops receiving data and stays in the inventory. Delete it in the UI, or through the
suite API with `DELETE /api/resources/{id}`. Both also delete its history.

The account can do this itself. With "Delete objects that left NSX after (days)" above 0, the adapter deletes
the objects of its federation that NSX no longer returns and that have had no data for that many days (one day
at least). It checks once an hour and deletes at most 50 objects at a time. It deletes nothing after an
incomplete cycle: a Global Manager or a location not collected, a failed call, a call budget used up. The
federation object lists what was deleted.

## What the adapter reads from VCF Operations

The adapter uses the connection to VCF Operations that the collector gives it for three things:

- the list of NSX clusters the built-in NSX integration monitors (the `auto` settings above). The same list
  decides who raises the status and capacity alerts of an edge node;
- the location list of earlier cycles, when no Global Manager answers and the container cache is empty;
- the objects to delete, when that setting is on.

The property "VCF Operations API from the adapter" on the federation object says whether the connection works.
Without it the pack raises the alarm alerts and the edge alerts itself, links nothing in `auto`, and deletes
nothing.

## Tests

```bash
docker build -t fedmp-tooling tooling
docker run --rm -u $(id -u):$(id -g) -v $PWD:/work -w /work fedmp-tooling python -m pytest -q tests -p no:cacheprovider
```

The tests answer the adapter's calls from `tests/fixtures/federation.json`, an anonymised recording of a
federation with two Global Managers and two locations. They cover a normal cycle, failed calls, a location
outage, a Global Manager of another federation, the cached location list and the one from VCF Operations, the
call budget, certificate trust, versions and upgrades, tiered realization on 600 more segments, stale-object
deletion, edge capacity, Tier-0 service routers in both HA modes, the change timeline (first cycle, failover,
partial reads), clock differences, configuration limits and overrides, password expiry, the firewall rule review
(shadowed, any-allow and unused rules, partial reads, counter resets), availability, a federation of 1,310
stretched objects, the consistency of the alert content with the object model, the
federation ID on every object, the group, notification rule and diagnostics scripts, and the image packages
against the Dockerfile.

CI also checks the Python packages of the adapter image (`pack/image-requirements.txt`) against the known
vulnerabilities every week and builds an SBOM; see [SECURITY.md](../SECURITY.md).
