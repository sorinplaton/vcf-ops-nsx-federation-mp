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
- Each Local Manager: about 10, plus one per edge cluster, one per edge node, one per Tier-0 locale and one per
  collected policy.

The first cycle after a start reads every object, so the budget of 2000 calls per manager covers about 1,900
  stretched objects in that cycle; the following cycles need about a sixth. Above that, raise the budget and check
the metric "Collection duration (s)": a cycle has to finish inside the collection interval (5 minutes by default).
The alert "NSX Federation collection is slow or throttled" fires above 4 minutes or when NSX answers HTTP 429.
NSX limits each client to 100 requests per second by default.

Only a two-location federation was measured on real managers. Larger sizes were run against the mock NSX of the
test suite.

## Alerts during a location outage

When the active Global Manager reports a location as not connected, the adapter writes the name of that location
to the property "Locations in outage" of every location, sync channel, edge, tunnel, BGP neighbor and stretched
object of the federation. The alerts of these objects require that the property reads "none", so they stay quiet
and the alert of the location itself is the one to act on. They fire again in the cycle after the location is back.

The alarm alerts of the Global Manager and of the locations are quiet in the same way, except the alarms between
Global Managers. When a Global Manager does not answer or none is active, the sync channel alerts are quiet behind
the alert on the federation.

A second, unrelated fault on these objects is not alerted on while the outage lasts. Its metrics and properties are
still collected.

For planned work, use a maintenance schedule of VCF Operations on the group "NSX Federation - All objects".

## Collection health

On the federation object:

| Metric or property | Meaning |
| --- | --- |
| NSX API calls without an answer | Calls that failed in the last cycle. The data behind them is unknown and is not sent |
| NSX API calls without an answer (property) | Manager, call and HTTP status of each failed call |
| NSX API calls retried after a timeout | A call is retried once after a timeout or a dropped connection |
| Global Managers not collected | Address and reason: not reachable, certificate not trusted, or another federation |
| Collection mode | `normal`, or `degraded` when no Global Manager answers and the location list comes from the cache |

HTTP 403 on a call points to missing rights of the account. The cache of the location list lives in the adapter
container and is lost when the container restarts.

## Upgrade

1. Install the new `.pak` (see the README).
2. Stop and start the account. Check that it reaches "Collecting"; start it a second time if it stays stopped.
3. Metrics appear after three cycles, rates after four.

## Objects that no longer exist

An object deleted in NSX stops receiving data and stays in the inventory. Delete it in the UI, or through the
suite API with `DELETE /api/resources/{id}`. Both also delete its history.

The account can do it: with "Delete objects that left NSX after (days)" above 0, the adapter deletes the objects of
its federation that NSX no longer returns and that have had no data for that many days. It checks once an hour,
only in a complete cycle, and deletes at most 50 objects at a time. The federation object lists what was deleted.

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
