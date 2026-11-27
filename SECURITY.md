# Security

## Reporting a vulnerability

Report a vulnerability through the repository's Security tab ("Report a vulnerability"), not in a public issue.
Give the version of the pack, the versions of NSX and VCF Operations, and the steps to reproduce.

## What the pack can do

- **NSX:** the adapter only sends GET requests (`pack/app/nsxclient.py`). A read-only account is enough; the
  built-in `audit` account was tested. With certificate trust `enforce` or `ca`, each session is bound to the
  certificate that was checked.
- **VCF Operations:** the adapter uses the connection the collector gives it. It reads the objects of its own
  adapter kind and the NSX clusters of the built-in NSX integration. It deletes objects of its own account only
  when "Delete objects that left NSX after (days)" is above 0 (default 0).
- **Install scripts:** `install/` holds scripts run by an administrator from the build host. `diagnostics.py` only
  reads. `upi.sh`, `install_unsigned.sh` and `install_pak.py` install the pack. `federation_groups.py` and
  `notification_rules.py` create or update the per-federation groups (and their policy assignment) and a
  notification rule. `cleanup_content.py` deletes only definitions with the IDs the pack generates and, with
  `--legacy-objects`, the objects of the pack keyed by name before 1.9.0. No script reads or prints a credential
  of an account.

## Supply chain

Releases come from Git only. A `v*` tag runs `.github/workflows/release.yml`, which builds the pack and both images
from that tag and publishes them; nobody pushes an image by hand. That path is protected as follows:

- **Who can release.** Only a repository admin can create, move or delete a `v*` tag (ruleset "release tags").
  The release job runs in the environment `release`, which waits for the maintainer's approval and alone holds
  the Docker Hub token. `main` changes only through a pull request with green checks (ruleset "main").
- **Least privilege.** Pull-request builds run with a read-only token, publish nothing and sign nothing. Only the
  release job can write packages or create a release. Two jobs can request an OIDC token: the release job, for
  signing, and the Scorecard job, to publish its results. Checkouts keep no credentials
  (`persist-credentials: false`).
- **Pinned inputs.** Every GitHub Action is pinned by commit SHA. The tooling image's base is pinned by digest,
  and every Python package of the build, of the CI tools and of the test tools is locked with its hashes
  (`--require-hashes`). The test job also installs the package list of the adapter image
  (`pack/image-requirements.txt`), which gives exact versions without hashes. The adapter image adds exactly
  five packages to the SDK base image, installed with `--no-deps --require-hashes`
  (`pack/adapter_requirements.txt`, written by `tools/hash_requirements.py`); a test checks that their versions
  equal the image lock. The SDK's own `base-adapter` image is built by `mp-build` from a template inside the SDK
  and cannot be pinned from this repository; it is only a build stage. The image that ships is built on a Wolfi
  base pinned by digest (`pack/Dockerfile`), whose `python-3.11` package is the current one at build time.
  Dependabot proposes updates monthly, except for that base, which is moved by hand.
- **Signatures and attestations.** Each release signs the pack and both images keyless with Sigstore: the
  certificate names this repository, `release.yml` and the tag. All three also get a SLSA build provenance
  attestation (`gh attestation verify`), and the adapter image a signed CycloneDX SBOM attestation
  (`cosign verify-attestation --type cyclonedx`), on `ghcr.io` and on Docker Hub. The release job verifies them
  before it creates the draft release, except the SBOM attestation of the Docker Hub copy.
- **Scanning.** The release and pull-request builds scan the adapter image with Trivy and stop on a CRITICAL
  vulnerability that has a fixed version. The full report is a release asset (`NSXFederation_<version>.trivy.txt`).
- **Install-time check.** VCF Operations cannot read these signatures (KB 415279). `install/install_unsigned.sh`
  runs `install/verify_pak.sh` first: a released pack whose `.sigstore.json` is next to it must verify, or nothing
  is installed.
- **Scorecard.** OpenSSF Scorecard scores the repository weekly; the results are in the Security tab and on
  scorecard.dev.
- **Key signatures end with v1.14.0.** Releases up to `v1.13.2` were signed with the project key (`cosign.pub`).
  From `v1.13.3` to `v1.14.0` the maintainer adds key signatures next to the keyless ones; later releases are
  keyless only and the key is retired.
- `pack/image-requirements.txt` lists the Python packages of the adapter image, written from a built image by
  `tools/image_lock.sh`. CI checks it against the known-vulnerability databases on every push and every Monday
  (`pip-audit`), and produces a CycloneDX SBOM as the build artifact `sbom`.
- The SDK library pins `cryptography` 44.0.0, which has known vulnerabilities. The image installs 50.0.2 in its
  place; the SDK only uses it to load a PEM certificate. The base image's `setuptools` is upgraded to 84.2.1, and
  `pip` is removed from the image.
- From release 1.13.7 the adapter image is built in two stages: the SDK's `base-adapter` image (Debian 12) is the
  build stage, and its Python files are copied onto a Wolfi base with Python 3.11. The Trivy report of release
  1.13.7 lists no vulnerability. The image ships OpenSSL 4 where the Debian base had OpenSSL 3.
  [docs/BASE-IMAGE.md](docs/BASE-IMAGE.md) compares the two bases and explains the two stages.
- Releases up to 1.13.6 ship the Debian 12 base itself. The Trivy report of release 1.13.5
  (`NSXFederation_1.13.0.trivy.json`) lists 102 HIGH or CRITICAL vulnerabilities (100 HIGH, 2 CRITICAL) in Debian
  packages that have no fixed version in Debian 12, and three on setuptools 70.3.0, the `pkg_resources` copy that
  pip bundles.
- The pack is not signed by Broadcom: installing it requires "Allow unsigned PAK installation" in VCF Operations.
  `install/upi.sh enableUPI` turns it on before the upload, and `install/install_unsigned.sh` turns it off again at
  the end.
