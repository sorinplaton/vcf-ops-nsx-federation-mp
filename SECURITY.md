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
  reads. `upi.sh`, `install_unsigned.sh` and `install_pak.py` install the pack. `federation_groups.py`
  and `notification_rules.py` create or update the per-federation groups (and their policy assignment) and a
  notification rule. `cleanup_content.py` deletes only definitions with the IDs the
  pack generates and, with `--legacy-objects`, the objects of the pack keyed by name before 1.9.0. No script reads
  or prints a credential of an account.

## Supply chain

- `pack/image-requirements.txt` lists the Python packages of the adapter image, written from a built image by
  `tools/image_lock.sh`. CI checks it against the known-vulnerability databases on every push and every Monday
  (`pip-audit`), and produces a CycloneDX SBOM as the build artifact `sbom`.
- The SDK library pins `cryptography` 44.0.0, which has known vulnerabilities. The Dockerfile replaces it with a
  fixed release; the SDK only uses it to load a PEM certificate. The base image's `setuptools` is upgraded to 84.2.1.
- The adapter image is built on the SDK's `base-adapter` image (Debian 12). A Trivy scan of the 1.13.0 image
  finds 101 HIGH or CRITICAL vulnerabilities (99 HIGH, 2 CRITICAL) in Debian packages that have no fixed version
  in Debian 12; they come with the base image and change only with a new SDK. In Python packages it finds one,
  a false positive: pip's own SBOM lists the `pkg_resources` copy pip bundles from setuptools 70.3.0, and that
  copy does not contain the affected code (`PackageIndex`, CVE-2025-47273).
- The PAK is not signed: installing it requires "Allow unsigned PAK installation" in VCF Operations, which the
  install script turns on for the upload and off again at the end.
