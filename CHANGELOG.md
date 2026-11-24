# Changelog

Each release has a tag `v<version>`; the commit messages give the details.

## Unreleased

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
