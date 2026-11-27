# Quick install, once the images are in your Artifactory

The short version of the [enterprise install guide](INSTALL-ENTERPRISE.md), for the person who installs once the
images are approved. Version française : [INSTALL-QUICK.fr.md](INSTALL-QUICK.fr.md).

This guide assumes that the two images of the release you chose are in your approved repository, under the
digests given in the notes of that [release](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/releases):

- `artifactory.example.com/vcfops-approved/nsxfederation-pak`, which carries the pack file;
- `artifactory.example.com/vcfops-approved/nsxfederation`, the adapter image the cloud proxy runs.

Replace `artifactory.example.com` and `vcfops-approved` with your own names in every command.

## What you need

- A Linux host with Docker, `python3`, `unzip` and `curl`, logged in to Artifactory.
- Root access to each cloud proxy that will run the account.
- The approved change for the unsigned-pack setting in VCF Operations.
- A read-only NSX account that works on the Global Managers and the Local Managers.

## 1. Take the pack out of its image

*Where: the Linux host.*

```bash
ART=artifactory.example.com
APPROVED=vcfops-approved
RELEASE=1.13.7                       # or 1.13.6

docker pull $ART/$APPROVED/nsxfederation-pak:$RELEASE
docker create --name pak $ART/$APPROVED/nsxfederation-pak:$RELEASE
rm -rf pak && docker cp pak:/pak/. ./pak && docker rm pak
(cd pak && sha256sum -c SHA256SUMS)
```

Expected: `NSXFederation_1.13.0.pak: OK`. The digest that `docker pull` prints must be the pack image digest of
the release notes.

## 2. Point the pack at your Artifactory

*Where: the Linux host.*

The released pack tells the cloud proxy to pull the adapter image from `ghcr.io`. `repack.py` changes that
address to your repository and leaves the image digest as it is.

```bash
curl -LO https://raw.githubusercontent.com/sorinplaton/vcf-ops-nsx-federation-mp/v1.13.7/tools/repack.py
cp pak/NSXFederation_1.13.0.pak NSXFederation_1.13.0_site.pak
python3 repack.py NSXFederation_1.13.0_site.pak $ART $APPROVED/nsxfederation
sha256sum NSXFederation_1.13.0_site.pak      # record this value with the change
```

Expected: `REGISTRY=artifactory.example.com`, `REPOSITORY=/vcfops-approved/nsxfederation`, and a `DIGEST` line
equal to the adapter image digest of the release notes. `NSXFederation_1.13.0_site.pak` is the file to install.

## 3. Let the cloud proxy pull the image

*Where: each cloud proxy that will run the account, as root.*

```bash
mkdir -p /etc/docker/certs.d/artifactory.example.com
cp internal-ca.crt /etc/docker/certs.d/artifactory.example.com/ca.crt     # only if Artifactory uses an internal CA
docker pull artifactory.example.com/vcfops-approved/nsxfederation@sha256:...   # the adapter image digest
```

Expected: `Status: Downloaded newer image`.

The cloud proxy pulls without a login, so the user `anonymous` needs the Read permission on `vcfops-approved`.
If your site does not allow anonymous access, run `docker login artifactory.example.com` before the pull and
`docker logout` after it. The account then starts from the copy on the cloud proxy. Pull again for every new
release.

A cloud proxy upgrade or redeploy removes the certificate folder and the image. Repeat this step after each one.

## 4. Install the pack

*Where: a browser.*

1. Open the admin console, `https://<vcf-operations>/admin`. Under Administrator Settings, Security Settings,
   activate "Allow unsigned PAK installation".
2. Open the product UI. Under Administration, Integrations, Repository, add a pack.
3. Run `sha256sum` on `NSXFederation_1.13.0_site.pak` and compare the result with the value recorded in step 2.
   Then select that file. Tick "install even if already installed" and "Ignore the PAK file
   signature checking".
4. Upload the file, accept the warning about the unsigned file, and wait until the install has finished.
5. Go back to the admin console and deactivate "Allow unsigned PAK installation". The console refuses the change
   while the install is still running, so try again once it is done.

## 5. Create the account

*Where: the product UI.*

1. Under Administration, Integrations, Accounts, add an account of type NSX Federation.
2. Enter the cluster address of the active Global Manager and of the standby, separated by a comma.
3. Enter the read-only NSX credential, and select the cloud proxy of step 3 as the collector.
4. Validate the connection, accept the Global Manager certificates, and save. With the default certificate
   trust, the account collects nothing until the certificates are accepted.

## 6. Check the collection

The account reaches "Collecting". Objects appear first and metrics follow after about ten minutes. The NSX
Federation dashboards then show data.

If the account stays failed with an empty message, the cloud proxy could not pull the image. Run the pull of
step 3 again and read its error.

## Upgrades and rollback

For a new release, repeat steps 1 to 4 with the new release number, then stop and start the account. To go
back, install the previous `_site` file the same way. Keep the previous images in `vcfops-approved` until the
new release is running.
