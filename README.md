# NSX Federation management pack for VCF Operations

A management pack that monitors NSX Federation (NSX 9.1) in VCF Operations 9.1.1. It is built with the
[VCF Operations Integration SDK](https://vmware.github.io/vmware-vcf-operations-integration-sdk/introduction)
in Python. Current version: 1.7.6.

This is a lab project, not a Broadcom product. The pack is unsigned and comes with no support. It only reads
from NSX.

The design, the install and the tests are described in two blog posts:

- [Part 1: design](https://sorinplaton.github.io/posts/nsx-federation-management-pack-design/)
- [Part 2: building, installing and what the pack captures](https://sorinplaton.github.io/posts/nsx-federation-management-pack-install/)

## What it monitors

The full list of object types, metrics, properties and alerts, with the condition and the recommendation of each
alert, is in [docs/FEATURES.md](docs/FEATURES.md). In short:

- 15 object types (the global firewall rule is opt-in): the federation, Global Managers and their nodes, locations, sync channels, edge clusters,
  edge nodes, RTEP tunnels, Tier-0 BGP neighbors, stretched Tier-0 and Tier-1 gateways, segments, global
  security policies, and the realization of each stretched object at each location. Global firewall rules
  are opt-in per policy.
- Metrics in 17 groups, with rates computed from the NSX counters, federation rollups and 18 key indicators.
- 63 alert definitions, 98 symptoms and 34 recommendations, including the 13 federation alarm types of
  NSX 9.1 as named alerts, the sync state of a standby Global Manager and alerts on the collection itself.
- A failed NSX call is reported as unknown data, not as an empty answer: no alert fires on data that was
  never received, and the federation object lists the calls without an answer.
- During a location outage, the alerts of the objects that depend on the location stay quiet behind the alert of
  the location.
- Versions and upgrades: Global Managers on different versions, a location ahead of the Global Manager, an
  upgrade in progress, paused or failed.
- Replication flows, last-seen time and onboarding status of each location, as on the Location Manager page of NSX.
- No double alerts: the federation alarms of a Local Manager that the built-in NSX integration monitors are left
  to that integration.
- Four custom groups, three dashboards, five list views, one report and an inventory tree.
- Optional links from edge nodes and edge clusters to the objects of the built-in NSX integration.

The account takes one or two Global Manager addresses and one credential. The Local Managers are discovered
from the Global Manager and checked against the certificate thumbprint it holds for them. A read-only NSX
account is enough: the built-in `audit` account was tested.

## Layout

| Path | Content |
| --- | --- |
| `pack/` | The SDK project: `app/adapter.py` (object model, collection, events), `app/nsxclient.py` (read-only NSX client), `content/` (alerts, dashboards, views, report, custom groups, traversal spec) |
| `tools/gen_*.py` | Generators for the content and the icons. They read the object model from `adapter.py` and write into `pack/content` |
| `tools/localrun.py` | Runs a collection against live NSX managers without VCF Operations |
| `tools/test_gaps.py` | Same run with chosen NSX calls made to fail, to check that their data is reported as unknown |
| `tools/repack.py` | Rewrites the registry address inside a built `.pak` |
| `tools/schemas/` | The SDK schema files for alert definitions and traversal specs. They must stay out of `pack/content` (see the blog) |
| `tooling/Dockerfile` | An image with the SDK tools (`mp-init`, `mp-test`, `mp-build`) |
| `install/` | Scripts for the scripted install of an unsigned pack |
| `tests/` | Test suite with a mock NSX (an anonymised recording of a small federation) |
| `docs/FEATURES.md` | Every object type, metric, property and alert of the pack (generated) |
| `docs/OPERATIONS.md` | Account settings, certificate trust, sizing, alert behaviour, upgrade |

## Build

The build needs Docker and an image registry the cloud proxy can reach over HTTPS.

```bash
docker build -t fedmp-tooling tooling

# regenerate the content after a change to the object model
for g in alerts dashboards groups views features; do python3 tools/gen_$g.py; done

# test a collection without VCF Operations
docker run --rm --network host -v $PWD:/work -e GM_HOSTS=<gm1>,<gm2> -e NSX_USER=admin -e NSX_PASSWORD=... \
  fedmp-tooling python tools/localrun.py

# build the pack and push the adapter image
docker run --rm --network host -u $(id -u):$(getent group docker | cut -d: -f3) -e HOME=$PWD/.sdkhome \
  -v /var/run/docker.sock:/var/run/docker.sock -v $(which docker):/usr/bin/docker -v $PWD:$PWD -w $PWD/pack \
  fedmp-tooling mp-build --no-ttl -r <registry>/<repository> --registry-username <user> --registry-password <password>
```

`mp-build` writes Docker Hub into the pack when the registry is given as `host:port` without a domain.
`tools/repack.py <pak> <host:port> <repository>` corrects it.

## Install

The pack is not signed, so VCF Operations 9 refuses it until "Allow unsigned PAK installation" is turned on
in the admin console (Broadcom KB 415279). The cloud proxy must also trust the certificate of the registry.
Both steps are described with screenshots in part 2 of the blog.

By hand: turn the switch on, upload the `.pak` under Administration, Integrations, Repository with
"Ignore the PAK file signature checking", then turn the switch off.

Scripted, with `google-chrome`, `node` and Python `requests` on the host:

```bash
export OPS_HOST=<vcf-operations-fqdn> OPS_PW=<admin password>
install/upi.sh enableUPI
install/install_unsigned.sh pack/build/NSXFederation_1.7.6.pak   # turns the switch off at the end
```

The scripted route uses calls that are internal to the VCF Operations UI. They are unsupported and can
change with any release.

After an upgrade of the pack, stop and start the account.

## Tested with

NSX 9.1.1 (two Global Managers, active and standby, and two locations), VCF Operations 9.1.1.0, Integration
SDK 1.3.1, in a nested lab. Most alert definitions have not fired, because the lab has no such fault.

## Licence

MIT, see [LICENSE](LICENSE). The files under `pack/conf` and `tools/schemas` come from the Integration SDK
project template.
