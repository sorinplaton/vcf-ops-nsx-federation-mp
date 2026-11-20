# Operating the NSX Federation pack

## Account

| Setting | Value |
| --- | --- |
| Global Manager addresses | The cluster address of every Global Manager cluster of one federation, active and standby. One account monitors one federation; a second federation needs a second account |
| Credential | A read-only NSX account. The built-in `audit` account was tested. A separate account for the Local Managers is optional |
| Certificate trust | `enforce` (default) or `report`, see below |
| Federation name | Optional name of the federation object. Set it when VCF Operations monitors several federations |
| Link to the objects of the NSX integration | `auto` (default): edge nodes and edge clusters are linked to the objects of the built-in NSX integration for a Local Manager that integration monitors. `on`: always. `off`: never |
| Alerts for the federation alarms of a Local Manager | `auto` (default): raised by this pack only for a Local Manager the built-in NSX integration does not monitor. `always`, `never` |
| Delete objects that left NSX after (days) | `0` (default): never. See "Objects that no longer exist" |
| Policies collected rule by rule | Empty by default. Rules become objects only for the listed policies |
| API call budget per manager and cycle | 2000 by default, see Sizing |

## Certificate trust

The adapter reads the certificate a manager presents, compares its SHA-256 fingerprint with the trusted ones, and
then binds every connection of the cycle to that fingerprint. The credentials are not sent to a server that
presents another certificate.

- A Global Manager is trusted by the certificate accepted on the account in VCF Operations.
- A Local Manager is trusted by the thumbprint the Global Manager holds for it.
- `enforce`: a manager whose certificate is not a trusted one, or that has no trusted certificate on record, is
  not collected. The alert names the manager.
- `report`: the manager is collected and the mismatch raises an alert on the location.

An account created through the API has no accepted certificate. It collects in `report` mode only, until the
certificates are accepted in the UI (edit the account, validate the connection, accept).

There is no mode that validates a certificate chain and a host name.

## Sizing

Calls per collection cycle, as measured with the test suite:

- Active Global Manager: about 20, plus one per location and one per stretched object (Tier-0, Tier-1, segment,
  global security policy). Above 200 stretched objects the realization of an object is read again only when the
  object is new, changed in NSX, was not fully realized, or once in six cycles; the last state is used otherwise.
- Standby Global Manager: 5.
- Each Local Manager: about 10, plus one per edge cluster, two per edge node, one per Tier-0 locale and one per
  collected policy.

The first cycle after a start reads every object, so the budget of 2000 calls per manager covers about 1,900
  stretched objects in that cycle; the following cycles need about a sixth. Above that, raise the budget and check
the metric "Collection duration": a cycle has to finish inside the collection interval (5 minutes by default).
The alert "NSX Federation collection is slow or throttled" fires above 4 minutes or when NSX answers HTTP 429.
NSX limits each client to 100 requests per second by default.

Only a two-location federation was measured on real managers. Larger sizes were run against the mock NSX of the
test suite.

## Alerts during a location outage

When the active Global Manager reports a location as not connected, the adapter writes the name of that location
to the property "Locations in outage" of the objects that depend on it: the location itself, its edge clusters,
edge nodes and BGP neighbors, the tunnels and sync channels with one end at that location, the stretched objects
whose span includes it and their realization there, and the Global Managers. The alerts of these objects require
that the property reads "none", so they stay quiet and the alert of the location itself is the one to act on.
If the fault is still there, they fire once the outage symptom has cleared, three cycles after the location is
back. Objects of the other locations are not affected: a fault
there raises its alert.

The alarm alerts of the Global Manager and of the locations are quiet in the same way, except the alarms between
Global Managers. A stretched object is quiet while any location of its span is in outage; its realization at each
of the other locations still raises its own alert. The outage symptom clears three cycles after the location is
back, one cycle after the symptoms it keeps quiet, so the end of an outage raises no alert either. When a Global Manager does not answer, the alerts of its own sync channels are quiet behind the
alert on the federation; when none is active, those of every channel with a Global Manager at one end. Channels
between two locations are never quiet for that reason.

A second, unrelated fault on the objects of the location in outage is not alerted on while the outage lasts. Its metrics and properties are
still collected.

For planned work, use a maintenance schedule of VCF Operations on the group "NSX Federation - All objects".

## Latency, packet loss and BGP flaps

- Latency: the adapter reads the latency NSX measures on each flow between the Global Manager and a location, from
  `GET /global-manager/api/v1/observability/flow-details` on the active Global Manager, the call behind the "Latest
  Latency" column of the Location Manager page. The metric "Round-trip time to the Global Manager" is the highest of the
  two flows of the location, and "% of the maximum RTT setting" compares it with the maximum RTT set for the location
  in NSX. An NSX without that call answers 404; the adapter then uses the round-trip time of the replication status,
  which NSX 9.1 leaves empty. NSX measures no latency between the two Global Managers. The same alert also fires when
  the round-trip time leaves the range VCF Operations learned for the location (dynamic threshold, three cycles), which
  catches a WAN that degraded long before it reaches 150 ms. The alert fires above 150 ms, the limit NSX Federation supports, or
  above 80% of the setting (warning), and above 100% (immediate). With the default setting of 250 ms, 80% is 200 ms,
  so the 150 ms condition is the one that fires first. The
  latency alarms of NSX itself (between Global Managers, and between a Global Manager and a location) raise their own
  alerts.
- Packet loss: for each RTEP tunnel and direction, the dropped packets of the interval are divided by the packets of the
  interval. NSX counts a dropped packet in the packet total too: in the lab the received total of a tunnel matched the
  sent total of its peer exactly while one packet was counted as dropped (only receive drops were seen; the send
  counters are taken to work the same way). The percentage is sent once at least 50 packets crossed the tunnel since
  the previous cycle, so an idle tunnel does not show 50% for one lost packet. The alert needs both more than 1% (5% for immediate) and
  more than 10 drops per minute, for two cycles.
- Sync queues: the same call gives the size and the maximum size of the sync queues of each flow. "Sync queue used"
  is the fullest queue of the location, in percent. It has no alert of its own: NSX raises the alarm
  queue_occupancy_threshold_exceeded, which the pack turns into an alert.
- Blocked packets: NSX counts them apart from drops. More than 10 per minute in one direction for two cycles raises an
  alert, since a firewall on the inter-site path usually explains it.
- BGP flaps: the connection drop counter of each Tier-0 BGP neighbor, and of the inter-site session of each RTEP tunnel
  (from `GET /api/v1/transport-nodes/<id>/inter-site/bgp/summary` on the Local Manager, one call per edge node), gets a rate. A drop since the previous cycle while
  the session is established in the current cycle raises a warning that stays for two cycles. A session that is down
  in the current cycle raises no flap alert, and the "not established" alert follows if it stays down. A restart of
  the edge resets the counter, which gives no rate for that cycle, so a restart is not reported as a flap.

## Changing a threshold

The thresholds of the pack are listed in [FEATURES.md](FEATURES.md#thresholds). They are part of the symptom
definitions and the same for every site. To use another value without rebuilding the pack, override the symptom in
the policy of the objects: Operate, Administration, Configurations, Policy Definition, select the policy and Edit, open
Alerts and Symptoms, then the Symptom Definitions tab, pick NSX Federation in the object types (or type the name of the
symptom in the filter), select the symptom and change the value under Override Symptoms, then Save. To change the value for everyone, edit the table `S` in `tools/gen_alerts.py`, run the
generators and build a new version. The names of some symptoms and alerts carry the value (for example "more than 1%
of the received packets"): change the name in the same table.

## Collection health

On the federation object:

| Metric or property | Meaning |
| --- | --- |
| NSX API calls without an answer | Calls that failed in the last cycle. The data behind them is unknown and is not sent |
| NSX API calls without an answer (property) | Manager, call and HTTP status of each failed call |
| NSX API calls retried after a timeout | A call is retried once after a timeout or a dropped connection |
| Global Managers not collected | Address and reason: not reachable, certificate not trusted, or another federation. A Global Manager known from an earlier cycle (or, after a restart of the adapter container, from VCF Operations) that does not answer, or that answers for another federation, reads 0 on its metric "Reachable" |
| Collection mode | `normal`, or `degraded` with the reason (no Global Manager answers, none is active, or the active one did not return its locations) and where the location list comes from (the cache or VCF Operations) |

A location outside the known span of a policy is not asked for its statistics. When the span is not known yet,
an HTTP 404 from a Local Manager means the policy does not exist there, and it is not counted as a failed call.
Where the policy is realized, a 404 is a failed call and the counters of the policy are not sent for that cycle;
a location that keeps answering 404 for a realized policy keeps the "data gaps" alert open. A "full sync did not complete" or "not realized" condition has to last two cycles before its
alert fires, so a sync or a change that completes within a cycle or two raises nothing.

A sync channel for which the active Global Manager lists no connection detail is reported as unknown: no value is
sent, and VCF Operations keeps the last one.

HTTP 403 on a call points to missing rights of the account. A Local Manager whose cluster status call fails, together with
most of its other calls (HTTP 401, 403, 5xx or timeouts), is reported as not reached. A manager whose requests
time out or stay throttled (HTTP 429 or 503 after the retries) three times in a row is skipped for the rest of
the cycle.

One account stays with one federation. If the federation is rebuilt with a new ID, create a new account: when
every listed Global Manager answers for the new federation, the new account follows it. Remove the old account:
after a restart of its container it can follow the rebuilt federation too, and two accounts would then collect
the same objects. The cache of the location list lives in the adapter
container and is lost when the container restarts.

## Upgrade

1. Install the new `.pak` (see the README).
2. Stop and start the account. Check that it reaches "Collecting"; start it a second time if it stays stopped.
3. Metrics appear after three cycles, rates after four.

An upgrade replaces the alert content of the pack. A symptom definition that a new version no longer ships can
stay in VCF Operations, unused by any alert; delete it under Operations, Configurations, Symptom Definitions, or
with `DELETE /api/symptomdefinitions/{id}`.

## Objects that no longer exist

An object deleted in NSX stops receiving data and stays in the inventory. Delete it in the UI, or through the
suite API with `DELETE /api/resources/{id}`. Both also delete its history.

The account can do it: with "Delete objects that left NSX after (days)" above 0, the adapter deletes the objects of
its federation that NSX no longer returns and that have had no data for that many days (one day at least). It
checks once an hour and deletes at most 50 objects at a time. It deletes nothing in a cycle that is not complete:
a Global Manager or a location not collected, a failed call, a call budget used up. The federation object lists
what was deleted.

## What the adapter reads from VCF Operations

The adapter uses the connection the collector gives it to VCF Operations for three things: the list of NSX
clusters the built-in NSX integration monitors (settings `auto` above), the location list of earlier cycles when
no Global Manager answers and the container cache is empty, and the objects to delete when that setting is on.
The property "VCF Operations API from the adapter" on the federation object says whether it works. Without it the
pack raises the alarm alerts itself, links nothing in `auto`, and deletes nothing.

## Tests

```bash
docker build -t fedmp-tooling tooling
docker run --rm -v $PWD:/work -w /work fedmp-tooling python -m pytest -q tests
```

The tests answer the adapter's calls from `tests/fixtures/federation.json`, an anonymised recording of a federation
with two Global Managers and two locations. They cover a normal cycle, failed calls, a location outage, a Global
Manager of another federation, the cached location list and the one from VCF Operations, the call budget,
certificate trust, versions and upgrades, tiered realization on 600 more segments, stale-object deletion, and the
consistency of the alert content with the object model.
