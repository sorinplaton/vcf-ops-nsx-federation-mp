# The .pak in an image, for sites that distribute files through a container registry (docs/INSTALL-ENTERPRISE.md).
# FROM scratch: no operating system, no shell, nothing to scan or patch. The image cannot run; take the file out with
#   docker create --name pak <image> && docker cp pak:/pak/. ./ && docker rm pak && sha256sum -c SHA256SUMS
# Built and pushed by .github/workflows/release.yml, which copies the pack and writes SHA256SUMS next to this file.
FROM scratch
ARG VERSION
ARG RELEASE
ARG ADAPTER_DIGEST
LABEL org.opencontainers.image.title="NSX Federation management pack (.pak)" \
      org.opencontainers.image.version="${RELEASE}" \
      org.opencontainers.image.source="https://github.com/sorinplaton/vcf-ops-nsx-federation-mp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="NSXFederation_${VERSION}.pak for VCF Operations. The adapter runs from ghcr.io/sorinplaton/nsxfederation@${ADAPTER_DIGEST}, which the pack names." \
      io.github.sorinplaton.fedmp.pack-version="${VERSION}" \
      io.github.sorinplaton.fedmp.adapter-digest="${ADAPTER_DIGEST}"
COPY NSXFederation_${VERSION}.pak SHA256SUMS /pak/
CMD ["/pak/none"]
