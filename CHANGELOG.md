# Changelog

Each release has a tag `v<version>`; the commit messages give the details.

## 1.13.2 (2026-11-26)

Install guide and install scripts only: the pack is unchanged and still reports version 1.13.0. The released
pack of `v1.13.1` stays the one to download.

- The adapter image is public on `ghcr.io/sorinplaton/nsxfederation`, and release `v1.13.1` carries a ready-to-install
  `NSXFederation_1.13.0.pak` that pulls it. The install guide offers this as Option A (download the released pack),
  next to Option B (build your own, the previous steps). The network diagram shows both.
- The released pack and image are signed with cosign; `cosign.pub` is at the root of the repository. Option A
  gives the commands to install cosign and verify both signatures.
- Option A lists `pkg-containers.githubusercontent.com` next to `ghcr.io`: the image layers come from there.
- New variant of Option A: copy the released image by digest into an internal registry with `skopeo`, then point
  the pack at it with `tools/repack.py`. No build needed.
- New `docs/RELEASE.md` for the maintainer: build from the tag, push to `ghcr.io`, sign the image and the pack
  with cosign, verify as a user would, publish the GitHub release, and recover from a lost or exposed key.

- `docs/INSTALL.md` (and the French copy) follows the order of the blog post: registry, build, then the
  certificate on the cloud proxy. Steps 3 and 4 swapped places.
- The `docker` commands carry `sudo`, and the address of the build host is read from one variable, `IP`.
- The build pushes the image to `localhost:5000`, so the build host needs no certificate folder. The registry
  certificate also names `127.0.0.1` and `localhost`.
- The Build section of `README.md` uses the same commands.
- New variant in the install guide for an environment that already has a Harbor registry. It replaces steps
  2 to 4 and was not tested in the lab.
- The install guide reads every address, name and password from one settings file, `~/fedmp.env`, made from
  the new `install/fedmp.env.example`. The commands carry no address and no password.
- The install guide opens with the four install paths and what each is for: Option A, Option A with the mirror
  variant, Option B, and the Harbor variant of Option B.
- Two more network drawings: the flows of Option A and of the mirror variant. The port table lists them.
- A flow check of the guide fixed the places where a reader was stopped: the test of step 5 lost its NSX
  settings under `sudo`, the scripted install had no folder and a pack path that only fitted Option B, the
  mirror variant used the settings file, the Harbor values and the digest before they existed, and a site
  without internet access on the cloud proxy was sent to Option B only.
- Each step now says what it is for, what the command does and what a good result looks like.
- `install/federation_groups.py`, `notification_rules.py`, `cleanup_content.py` and `diagnostics.py` honour
  `OPS_INSECURE=1`, like `install_unsigned.sh`. The guide has a section for the tools some steps need: `govc`,
  Node.js 22 and Google Chrome.

## 1.13.1 (2026-11-26)

Install guide and install scripts only: the pack is unchanged and still reports version 1.13.0.

- `docs/INSTALL.md` (and the French copy) gives the commands to install Docker Engine, `git` and Python 3 with
  `requests` on Ubuntu/Debian and RHEL-family build hosts, and a diagram with the ports the build host and the
  cloud proxy must reach.
- The "NSX Federation - Non-production" policy, `install/import_policy.py` and `tools/gen_policy.py` are removed.
  `install/federation_groups.py --policy` assigns any existing policy.

## 1.13.0 (2026-11-22)

- Every object carries the property `federation_id`. `install/federation_groups.py` creates one custom group per
  federation from it, and can assign a policy to the group.
- `install/notification_rules.py` sends the alerts of the pack to an email, webhook, ServiceNow, Slack, SNMP or
  log file outbound instance, optionally for one group only.
- `install/diagnostics.py` collects the state of the accounts, collectors and collection into one JSON file for
  a support request, and lists the problems it finds.
- The adapter image replaces the `cryptography` pinned by the SDK (44.0.0, known vulnerabilities) with 50.0.2,
  and upgrades `setuptools`. CI audits the image's Python packages every week and produces an SBOM.
- `SECURITY.md`, this changelog, Dependabot for the GitHub Actions, troubleshooting in
  `docs/OPERATIONS.md`.

## 1.12.0 (2026-11-22)

Password expiry of the collection account, firewall rule review (unused, shadowed, any-allow), availability and
the Availability report, the "NSX Federation - Non-production" policy, scale test.

## 1.11.0 (2026-11-21)

Clock difference between managers, NSX configuration limits, global objects overridden at a location.

## 1.10.0 (2026-11-21)

Edge capacity, Tier-0 service routers and failover, change timeline.

## 1.9.0 (2026-11-21)

CA-chain trust, objects keyed by NSX ID, cleanup of orphan definitions on upgrade.

## 1.8.0 (2026-11-21)

Switchover readiness, location health, Location Matrix and Switchover Readiness dashboards.

## 1.7.x (2026-11-19 to 2026-11-20)

No double alarm alerts with the built-in NSX integration, versions and upgrades, replication flows, tiered
realization, icons, optional stale-object deletion; latency, packet loss and BGP flaps; all 15 federation alarm
types; many fixes from reviews.

## 1.6.0 and earlier (2026-11-19)

Certificate-bound sessions, quiet alerts during a location outage, test suite with a mock NSX, failed calls
reported as unknown data, self-monitoring alerts, first release.
