# NSX Federation management pack for VCF Operations

A management pack for VCF Operations 9.1.1 that monitors NSX Federation (NSX 9.1). It is written in Python with
the [VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction).
Current version: 1.13.0.

This is a lab project, not a Broadcom product. The pack is unsigned, has no support, and only reads from NSX.

Two blog posts cover the design, the install and the tests:

- [Part 1: design](https://sorinplaton.github.io/posts/nsx-federation-management-pack-design/)
- [Part 2: building, installing and what the pack captures](https://sorinplaton.github.io/posts/nsx-federation-management-pack-install/)

## What it monitors

[docs/FEATURES.md](docs/FEATURES.md) (French: [docs/FEATURES.fr.md](docs/FEATURES.fr.md)) lists every object
type, metric, property and alert, with the condition and the recommendation of each alert. In short:

- 16 object types: the federation, Global Managers and their nodes, locations, sync channels, edge clusters,
  edge nodes, RTEP tunnels, Tier-0 service routers, Tier-0 BGP neighbors, stretched Tier-0 and Tier-1 gateways,
  segments, global security policies, and the realization of each stretched object at each location. Global
  firewall rules are opt-in per policy.
- Metrics in 25 groups, with rates computed from the NSX counters, federation rollups and 26 key indicators.
- 87 alert definitions, 153 symptoms and 53 recommendations. They include the 15 federation alarm types of
  NSX 9.1.1 as named alerts, the sync state of a standby Global Manager, and alerts on the collection itself.
- Latency and packet loss: round-trip time to the Global Manager against the maximum RTT set in NSX and against
  its usual range, drops of each RTEP tunnel as a share of its packets, blocked packets, and flaps of the Tier-0
  and inter-site BGP sessions. [docs/FEATURES.md](docs/FEATURES.md#thresholds) lists the thresholds.
- A failed NSX call is reported as unknown data, not as an empty answer. No alert fires on data that never
  arrived, and the federation object lists the calls without an answer.
- During a location outage, the alerts of the objects that depend on the location stay quiet behind the alert
  of the location.
- Versions and upgrades: Global Managers on different versions, a location ahead of the Global Manager, an
  upgrade in progress, paused or failed.
- Switchover readiness: each standby Global Manager reads ready, not ready with the reasons (sync with the
  active, cluster health, version, expired certificate, upgrade in progress or paused), or unknown when a value
  could not be read. The metric is 1 only when readiness is confirmed. The federation reads ready when one
  standby is.
- A healthy flag per location, from the same checks as the share of locations healthy.
- Edge capacity: datapath and services CPU, system and datapath memory, and the fullest memory pool and file
  system of each federation edge node. The status and threshold alerts stay quiet when the built-in NSX
  integration monitors the edge.
- Tier-0 failover: the HA state and routing of each stretched Tier-0 service router on each edge. One alert
  fires when a Tier-0 has no active service router (in the federation, or at a location for an active-active
  Tier-0), another when a service router changes HA state.
- Change timeline: each cycle compares the active Global Manager, Global Manager roles and versions, locations,
  edge nodes, service router HA state and stretched objects with the previous one. Each change is an information
  event on its object, and the federation lists the last ten.
- Clocks: the clock of each manager against the active Global Manager, and the NTP servers and service of each.
- Configuration limits: each Local Manager's usage of the NSX limits, as NSX rates it, and the global objects
  overridden at a location.
- Password expiry: the days until the password of the collection account expires on each manager, with an alert
  before the collection stops.
- Firewall rule review: rules of the global security policies that are shadowed by an earlier rule, unused for
  a period (30 days by default) at every location of their span, or allowing any traffic everywhere.
- Availability: a 0/100 value per cycle for the federation, the Global Managers, the locations and the stretched
  Tier-0 gateways, and a 30-day availability report.
- One custom group per federation, from the federation ID every object carries (`install/federation_groups.py`),
  to assign a policy, scope a notification rule or filter a dashboard.
- Alerts sent to an email, webhook, ServiceNow, Slack, SNMP or log file outbound instance when they are new,
  updated or cancelled (`install/notification_rules.py`).
- A diagnostics script for support requests (`install/diagnostics.py`), and troubleshooting in
  [docs/OPERATIONS.md](docs/OPERATIONS.md#troubleshooting).
- Replication flows, last-seen time and onboarding status of each location, as on the Location Manager page of
  NSX.
- No double alerts: the federation alarms of a Local Manager that the built-in NSX integration monitors are left
  to that integration.
- Four custom groups, five dashboards (Federation Overview, Location Matrix, Switchover Readiness, Inter-site
  Connectivity, Stretched Objects), thirteen list views, two reports (health, availability) and an inventory
  tree.
- Optional links from edge nodes and edge clusters to the objects of the built-in NSX integration.

The account takes one or two Global Manager addresses and one credential. The pack discovers the Local Managers
from the Global Manager and checks them against the certificate thumbprint the Global Manager holds for each.
With certificate trust `ca`, a certificate signed by a trusted CA and valid for the address is trusted too. A
read-only NSX account is enough; the built-in `audit` account was tested.

## Layout

| Path | Content |
| --- | --- |
| `pack/` | The SDK project: `app/adapter.py` (object model, collection, events), `app/nsxclient.py` (read-only NSX client), `content/` (alerts, dashboards, views, reports, custom groups, traversal spec) |
| `tools/gen_*.py` | Generators for the content and the icons. They read the object model from `adapter.py` and write into `pack/content` |
| `tools/localrun.py` | Runs a collection against live NSX managers without VCF Operations |
| `tools/test_gaps.py` | The same run with chosen NSX calls made to fail, to check that their data is reported as unknown |
| `tools/repack.py` | Rewrites the registry address inside a built `.pak` |
| `tools/image_lock.sh` | Writes `pack/image-requirements.txt`, the Python packages of a built adapter image, which CI audits |
| `tools/schemas/` | The SDK schema files for alert definitions and traversal specs. They must stay out of `pack/content` (see the blog) |
| `tooling/Dockerfile` | An image with the SDK tools (`mp-init`, `mp-test`, `mp-build`) |
| `install/` | Scripts for the scripted install of an unsigned pack, the per-federation groups, the notification rule and the diagnostics |
| `tests/` | Test suite with a mock NSX (an anonymised recording of a small federation) |
| `docs/FEATURES.md`, `docs/FEATURES.fr.md` | Every object type, metric, property and alert of the pack, in English and in French (generated) |
| `docs/INSTALL.md` | Install guide: checklist and the commands for each step, with the machine they run on ([French](docs/INSTALL.fr.md)) |
| `docs/OPERATIONS.md` | Account settings, certificate trust, sizing, alert behaviour, groups, notifications, troubleshooting, upgrade |
| `SECURITY.md`, `CHANGELOG.md` | How to report a vulnerability, what the pack can do, supply chain; the changes of each release |

## Build

The build needs Docker and an image registry the cloud proxy can reach over HTTPS.

```bash
docker build -t fedmp-tooling tooling

# regenerate the content after a change to the object model
for g in alerts dashboards groups views features; do python3 tools/gen_$g.py; done

# test a collection without VCF Operations
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> -e NSX_USER=audit -e NSX_PASSWORD=... \
  fedmp-tooling python tools/localrun.py

# build the pack and push the adapter image
docker run --rm --network host -u $(id -u):$(getent group docker | cut -d: -f3) -e HOME=$PWD/.sdkhome \
  -v /var/run/docker.sock:/var/run/docker.sock -v $(which docker):/usr/bin/docker -v $PWD:$PWD -w $PWD/pack \
  fedmp-tooling mp-build --no-ttl -r <registry>/<repository> --registry-username <user> --registry-password <password>
```

When the registry is given as `host:port` without a domain, `mp-build` writes Docker Hub into the pack.
`tools/repack.py <pak> <host:port> <repository>` corrects it.

After a change to the Dockerfile or to `pack/adapter_requirements.txt`, run `tools/image_lock.sh <built image>` and
commit `pack/image-requirements.txt`: CI checks those packages for known vulnerabilities (see
[SECURITY.md](SECURITY.md)).

## Install

[docs/INSTALL.md](docs/INSTALL.md) (French: [docs/INSTALL.fr.md](docs/INSTALL.fr.md)) gives each step with the
machine its commands run on.

The pack is not signed, so VCF Operations 9 refuses it until "Allow unsigned PAK installation" is turned on in
the admin console (Broadcom KB 415279). The cloud proxy must also trust the certificate of the registry. Part 2
of the blog shows both steps with screenshots.

By hand: turn the switch on, upload the `.pak` under Administration, Integrations, Repository with "Ignore the
PAK file signature checking", then turn the switch off.

Scripted, with `google-chrome`, `node` and Python `requests` on the host:

```bash
export OPS_HOST=<vcf-operations-fqdn> OPS_PW=<admin password>
install/upi.sh enableUPI
install/install_unsigned.sh pack/build/NSXFederation_<version>.pak   # turns the switch off at the end
```

The scripts use calls that are internal to the VCF Operations UI. They are unsupported and can change with any
release.

After an upgrade of the pack, stop and start the account. `install_unsigned.sh` also deletes the alert, symptom
and recommendation definitions the new version no longer ships (`install/cleanup_content.py`).

## Tested with

NSX 9.1.1 (two Global Managers, active and standby, and two locations), VCF Operations 9.1.1.0 and Integration
SDK 1.3.1, in a nested lab. Most alert definitions have never fired, because the lab has none of those faults.

## Licence

MIT, see [LICENSE](LICENSE). The files under `pack/conf` and `tools/schemas` come from the Integration SDK
project template.
