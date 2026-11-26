# Changelog

Each release has a tag `v<version>`; the commit messages give the details.

## Unreleased

Install guide only.

- The adapter image is public on `ghcr.io/sorinplaton/nsxfederation`, and release `v1.13.1` carries a ready-to-install
  `NSXFederation_1.13.0.pak` that pulls it. The install guide offers this as Option A (download the released pack),
  next to Option B (build your own, the previous steps). The network diagram shows both.
- The released pack and image are signed with cosign; `cosign.pub` is at the root of the repository. Option A
  gives the commands to install cosign and verify both signatures.
- Option A lists `pkg-containers.githubusercontent.com` next to `ghcr.io`: the image layers come from there.
- New variant of Option A: copy the released image by digest into an internal registry with `skopeo`, then point
  the pack at it with `tools/repack.py`. No build needed.

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
