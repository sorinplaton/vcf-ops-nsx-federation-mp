#!/bin/sh
# Record the Python packages of a built adapter image in pack/image-requirements.txt, the list CI audits for known
# vulnerabilities and turns into an SBOM. Run it after each build that changes the Dockerfile or the requirements.
# The image has no pip (pack/Dockerfile removes it), so the list is read from the package metadata.
# usage: tools/image_lock.sh <image, e.g. isdk_nsxfederation-test:1.13.0>
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <image>" >&2; exit 2; }
out="$(dirname "$0")/../pack/image-requirements.txt"
{
  echo "# Python packages of the adapter image $1 (tools/image_lock.sh). Audited by CI; do not edit by hand."
  docker run --rm --entrypoint python3 "$1" -c '
import importlib.metadata as m
for line in sorted({d.metadata["Name"] + "==" + d.version for d in m.distributions()}, key=str.lower): print(line)'
} > "$out"
echo "wrote $out: $(grep -c '==' "$out") packages"
