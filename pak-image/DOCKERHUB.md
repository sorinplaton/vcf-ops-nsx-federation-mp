# NSX Federation management pack (.pak)

This repository holds the `.pak` file of the
[NSX Federation management pack for VCF Operations](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp),
packed in a `FROM scratch` image so that it can be distributed through a container registry such as JFrog
Artifactory.

The project's release workflow publishes the image from a Git tag; nobody pushes it by hand. GitHub is the source
of truth: every tag here matches a
[GitHub release](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/releases) of the same version, with the
same file.

The image cannot run. Take the file out:

```bash
docker pull <namespace>/nsxfederation-pak:<version>
docker create --name pak <namespace>/nsxfederation-pak:<version>
docker cp pak:/pak/. ./ && docker rm pak
sha256sum -c SHA256SUMS
```

The pack needs its adapter image, `<namespace>/nsxfederation` (here and on `ghcr.io/sorinplaton/nsxfederation`,
same digest). Both images are signed keyless with Sigstore by the release workflow. The install and verification
steps are in
[INSTALL-ENTERPRISE.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL-ENTERPRISE.md).
Once the images are in your own registry, the short version is
[INSTALL-QUICK.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL-QUICK.md).
