# Enterprise install through Artifactory: maintainer notes

The install procedure for organisations that use JFrog Artifactory is
[CUSTOMER-INSTALL.md](CUSTOMER-INSTALL.md) (French: [CUSTOMER-INSTALL.fr.md](CUSTOMER-INSTALL.fr.md)). This page
keeps what the customer guide leaves out: the scripted install of step 9, a note on Artifactory Pro, and the full
record of the lab tests. Step numbers below are those of the customer guide up to step 8.

Version française : [INSTALL-ENTERPRISE.fr.md](INSTALL-ENTERPRISE.fr.md).

## Scripted install instead of the manual steps

The customer guide installs through the VCF Operations UI. The install scripts of this repository do the same with
a headless Chrome and calls that are internal to the VCF Operations UI, unsupported and liable to change with any
release ([INSTALL.md](INSTALL.md)). They replace step 9 of the customer guide; steps 10 and 11 stay. VCF
Operations does not read the cosign signatures of step 4, so the install script checks them instead
(`install/verify_pak.sh`).

Give the script the SHA-256 recorded at step 7. It then refuses any other file. To make it also check the adapter
image signature through Artifactory, set `FEDMP_VERIFY=1` and `FEDMP_IMAGE_REPO`, after copying the signatures
there (`cosign copy`, step 6). Run the check on its own first, before the switch goes on:

```bash
export FEDMP_PAK_SHA256=<the SHA-256 of step 7>
export FEDMP_VERIFY=1 FEDMP_IMAGE_REPO=$ART/$APPROVED/nsxfederation    # only after the cosign copy of step 6
install/verify_pak.sh NSXFederation_${V}_site.pak                     # must end without an error
install/upi.sh enableUPI
install/install_unsigned.sh NSXFederation_${V}_site.pak               # checks again, installs, switches off
```

Without `FEDMP_PAK_SHA256` and `FEDMP_VERIFY=1`, the rewritten pack has no bundle and is installed with a notice.

## Artifactory Pro

With Artifactory Pro, the Docker promotion API or `jf rt docker-promote` can do the copy of step 6 inside Artifactory.
That was not tested here; check the digests afterwards the same way.

## What was tested

On 2026-11-26, on a JFrog Container Registry 7.161.26 with PostgreSQL, behind nginx with a certificate from a
test CA, with skopeo 1.25.1 and cosign 2.4.1:

- A Docker Hub image pulled through a Docker Hub remote repository: same digest as on Docker Hub.
- The released adapter image `v1.13.1` pulled through a remote repository (of `ghcr.io`, the only place it was
  published then), and its signature verified through Artifactory, with the key of that release.
- Release `v1.13.3`, the first one on Docker Hub, run through steps 3, 4, 6 and 7 as written: pack image pulled
  through the Docker Hub remote repository and pack taken out (checksum OK), keyless signatures of the pack image
  and of the adapter image verified through Artifactory, both images promoted with the same digests
  (`nsxfederation-pak` `sha256:1ff264cf...`, `nsxfederation` `sha256:bd533626...`), pack rewritten to the
  approved repository with the adapter digest unchanged.
- Both images copied into a local repository with `skopeo copy --all --preserve-digests`: same digests. Without
  `--all`, the pack image came out with another digest; that is why step 6 uses it.
- The pack taken out of the pack image in Artifactory, checksum OK, and rewritten with `repack.py` to the local
  repository.

- Release `v1.13.5`, the first with attestations, through the lab Artifactory (JFrog Container Registry
  7.161.26 at `https://10.0.6.28`, Docker Hub remote repository): keyless signatures of both images and the
  signed SBOM attestation (46 components) verified through Artifactory, and the build provenance of the Docker Hub
  copy verified with `gh attestation verify`.

- Docker only, with Docker Engine 29.6.2 and the containerd image store: both `v1.13.5` images pulled from Docker
  Hub by digest, tagged and pushed to a test registry kept their digests. The pack taken out with
  `docker create` and `docker cp` passed its checksum.
- The air-gapped transfer: both `v1.13.5` images through `dir:` folders and a tar file into a test registry, same
  digests. Through `oci-archive:`, the pack image came out with another digest.
- `verify_pak.sh` with `FEDMP_PAK_SHA256`: the right value passes, any other value stops the install, with and
  without `FEDMP_VERIFY`.

- Release `v1.13.6` through the lab Artifactory, steps 2 to 7 as written: pack image `sha256:e241b3f2...` and
  adapter image `sha256:15cd0535...`, both keyless signatures, the SBOM attestation and the provenance verified,
  both images promoted with the same digests, pack rewritten with the adapter digest unchanged. Then the check of
  step 9 before the switch: signatures copied into `vcfops-approved` with `cosign copy`, and `verify_pak.sh` with
  `FEDMP_PAK_SHA256`, `FEDMP_VERIFY=1` and `FEDMP_IMAGE_REPO` pointing at `vcfops-approved` passed; with the
  checksum of another file it refused the pack.

Not tested yet, so the path has not run end to end:

- Anonymous read on `vcfops-approved` (step 1, item 3) and the pull by a cloud proxy from Artifactory (step 8).
  The free edition only sets permissions in its UI. The same pull from a registry of the lab works, and a pull
  without the permission was refused (`unauthorized`), as expected.
- The install of the `_site` pack from Artifactory and the account (step 9), the rollback and the uninstall.
- JFrog Xray, the Artifactory Pro promotion API, and the Subdomain access method.
