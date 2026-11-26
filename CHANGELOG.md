# Changelog

Each release has a tag `v<version>`; the commit messages give the details.

## 1.13.5 (2026-11-26)

Supply-chain hardening of the release path; the pack itself is unchanged. `v1.13.4` was tagged but never
published: its release job stopped at the verification step, because the Trivy cache mount had created `~/.cache`
as root and `gh attestation verify` could not start. Its images on `ghcr.io` belong to no release; use `v1.13.5`.

- Only a repository admin can push a `v*` tag, and `main` changes only through a pull request with green checks
  (rulesets). The release job runs in the environment `release`: it waits for the maintainer's approval and alone
  holds the Docker Hub token. Pull-request builds run in a separate job with a read-only token.
- Every GitHub Action is pinned by commit SHA; the tooling image's base by digest; every Python package of the
  build, the CI tools and the tests is locked with hashes. The adapter image installs its five added packages with
  `--no-deps --require-hashes` (`tools/hash_requirements.py`), and a test checks them against the image lock.
  Dependabot proposes updates monthly.
- Each release adds a SLSA build provenance attestation for the pack and both images, and a signed CycloneDX SBOM
  attestation on the adapter image (on `ghcr.io` and Docker Hub). The build scans the adapter image with Trivy,
  stops on a CRITICAL vulnerability with a fix, and attaches the report to the release.
- `install/install_unsigned.sh` checks the signatures first (`install/verify_pak.sh`): a released pack with its
  `.sigstore.json` must verify, or nothing is installed.
- Key signatures (`cosign.pub`) continue up to and including `v1.14.0`; later releases are keyless only.
- OpenSSF Scorecard runs weekly.

## 1.13.3 (2026-11-26)

The pack is unchanged and still reports 1.13.0; this release adds the release workflow, Docker Hub and the
enterprise install guide.

- Releases are built and published by a workflow (`.github/workflows/release.yml`) from a Git tag, which is the
  single source of truth: pack and adapter image built from the tag, pushed to `ghcr.io` and copied byte for
  byte to Docker Hub (`docker.io/sorinplaton/nsxfederation`), signed keyless with Sigstore, verified, and left as a
  draft release for the maintainer. The signature names the workflow and the tag; `docs/INSTALL.md` gives the
  keyless check. Releases up to `v1.13.2` keep their key signatures, and the next ones carry both for a while.
- New pack image `nsxfederation-pak`: the `.pak` and its checksum in a `FROM scratch` image, on `ghcr.io` and
  Docker Hub, for sites that receive files through a container registry. The Docker Hub description comes from
  `pak-image/DOCKERHUB.md`.
- New `docs/INSTALL-ENTERPRISE.md` (and the French copy): install through JFrog Artifactory with a Docker Hub
  remote repository: pull, verify by signer identity, scan, promote by digest to a repository of approved
  images, point the pack at it, cloud proxy pull without a login. Tested against JFrog Container Registry.
- The mirror variant copies with `skopeo copy --all`: without it, an image published as a list comes out with
  another digest than the one signed.
- The SBOM of the adapter image is attached to each release. The tooling image pins the SDK to 1.3.1.
- `docs/RELEASE.md` describes the workflow; the manual steps stay as the fallback.

- Option A of the install guide explains why the cloud proxy reaches the internet: it downloads the adapter
  image from `ghcr.io` when the account starts, and only that. The mirror variant avoids it.

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
