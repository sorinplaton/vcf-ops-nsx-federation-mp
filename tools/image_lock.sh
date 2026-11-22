#!/bin/sh
# Record the Python packages of a built adapter image in pack/image-requirements.txt, the list CI audits for known
# vulnerabilities and turns into an SBOM. Run it after each build that changes the Dockerfile or the requirements.
# usage: tools/image_lock.sh <image, e.g. isdk_nsxfederation-test:1.13.0>
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <image>" >&2; exit 2; }
out="$(dirname "$0")/../pack/image-requirements.txt"
{
  echo "# Python packages of the adapter image $1 (tools/image_lock.sh). Audited by CI; do not edit by hand."
  docker run --rm --entrypoint pip3 "$1" freeze --all
} > "$out"
echo "wrote $out: $(grep -c '==' "$out") packages"
