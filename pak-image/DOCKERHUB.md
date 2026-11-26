# NSX Federation management pack (.pak)

This repository holds the `.pak` file of the
[NSX Federation management pack for VCF Operations](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp),
packed in a `FROM scratch` image so that it can travel through a container registry such as JFrog Artifactory.

It is published by the project's release workflow from a Git tag, never pushed by hand. GitHub is the source of
truth: every tag here matches a [GitHub release](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/releases)
of the same version, with the same file.

The image cannot run. Take the file out:

```bash
docker pull <namespace>/nsxfederation-pak:<version>
docker create --name pak <namespace>/nsxfederation-pak:<version>
docker cp pak:/pak/. ./ && docker rm pak
sha256sum -c SHA256SUMS
```

The pack needs its adapter image, `<namespace>/nsxfederation` (here and on `ghcr.io/sorinplaton/nsxfederation`,
same digest). Both images are signed keyless with Sigstore by the release workflow. Install and verification
steps: [INSTALL-ENTERPRISE.md](https://github.com/sorinplaton/vcf-ops-nsx-federation-mp/blob/main/docs/INSTALL-ENTERPRISE.md).
