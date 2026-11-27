#!/bin/bash
# Verify a released pack before it is installed: the keyless signature of the .pak (the .sigstore.json bundle of the
# GitHub release, next to the pack) and the keyless signature of the adapter image the pack names, both made by this
# project's release workflow from a v* tag. VCF Operations cannot check them itself (KB 415279), so
# install_unsigned.sh calls this first.
#
# FEDMP_VERIFY=auto (default): a pack with <pack>.sigstore.json next to it must verify, or the install stops; a pack
#                  without it (a build of your own, or a pack rewritten with repack.py) is installed with a notice.
# FEDMP_VERIFY=1   both checks are required; a missing bundle only skips the .pak check, the image check still runs.
# FEDMP_VERIFY=0   no check.
# FEDMP_IMAGE_REPO where to read the adapter image signature, default ghcr.io/sorinplaton/nsxfederation (the digest is
#                  the same on Docker Hub and in a registry that mirrors it, so any of them can be named here).
# FEDMP_PAK_SHA256 the SHA-256 the organisation recorded for this exact file (for a rewritten _site pack, the value
#                  of CUSTOMER-INSTALL.md step 7). When set, it is checked first, whatever FEDMP_VERIFY says, and a
#                  mismatch stops the install. It needs no network, so it also works on an air-gapped site.
# cosign from the PATH, or the cosign 2.4.1 image through Docker. Needs unzip.
# usage: verify_pak.sh <pak>
set -u
PAK="${1:?usage: verify_pak.sh <pak>}"
MODE="${FEDMP_VERIFY:-auto}"
REPO="${FEDMP_IMAGE_REPO:-ghcr.io/sorinplaton/nsxfederation}"
ID_RX='^https://github\.com/sorinplaton/vcf-ops-nsx-federation-mp/\.github/workflows/release\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$'
ISSUER=https://token.actions.githubusercontent.com
BUNDLE="$PAK.sigstore.json"

[ -f "$PAK" ] || { echo "no such pack: $PAK" >&2; exit 1; }
if [ -n "${FEDMP_PAK_SHA256:-}" ]; then
  want=$(printf '%s' "$FEDMP_PAK_SHA256" | tr 'A-F' 'a-f'); got=$(sha256sum "$PAK" | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then
    echo "pack checksum: matches the recorded value (FEDMP_PAK_SHA256)"
  else
    echo "pack checksum: MISMATCH: $PAK is $got, the recorded value is $want. Not installing." >&2; exit 1
  fi
fi
[ "$MODE" = 0 ] && { echo "signature check skipped (FEDMP_VERIFY=0)"; exit 0; }
if [ "$MODE" = auto ] && [ ! -f "$BUNDLE" ]; then
  echo "NOTICE: $PAK not verified: no $(basename "$BUNDLE") next to it (a build of your own, or a rewritten pack)."
  echo "        For a released pack, download its .sigstore.json from the GitHub release, or set FEDMP_VERIFY=1."
  exit 0
fi

if command -v cosign >/dev/null 2>&1; then
  cosign() { command cosign "$@"; }
elif command -v docker >/dev/null 2>&1; then
  DIR="$(cd "$(dirname "$PAK")" && pwd)"
  SUDO=""; docker info >/dev/null 2>&1 || SUDO=sudo
  cosign() { $SUDO docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$DIR:$DIR:ro" -w "$DIR" \
               ghcr.io/sigstore/cosign/cosign:v2.4.1 "$@"; }
else
  echo "cannot verify: neither cosign nor docker is installed (install cosign, or set FEDMP_VERIFY=0)" >&2; exit 1
fi

DIGEST=$(unzip -p "$PAK" adapter.zip 2>/dev/null | python3 -c 'import io,sys,zipfile
z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
c=[n for n in z.namelist() if n.endswith(".conf") and "/" not in n][0]
print([l[7:] for l in z.read(c).decode().splitlines() if l.startswith("DIGEST=")][0])' 2>/dev/null)
[ -n "$DIGEST" ] || { echo "cannot read the adapter image digest from $PAK" >&2; exit 1; }

rc=0
if [ -f "$BUNDLE" ]; then
  if cosign verify-blob --certificate-identity-regexp "$ID_RX" --certificate-oidc-issuer "$ISSUER" \
       --bundle "$(cd "$(dirname "$BUNDLE")" && pwd)/$(basename "$BUNDLE")" "$(cd "$(dirname "$PAK")" && pwd)/$(basename "$PAK")" >/dev/null 2>/tmp/fedmp-verify.err; then
    echo "pack signature: verified (release workflow of sorinplaton/vcf-ops-nsx-federation-mp)"
  else
    echo "pack signature: FAILED" >&2; tail -3 /tmp/fedmp-verify.err >&2; rc=1
  fi
else
  echo "pack signature: not checked, no $(basename "$BUNDLE") (the image check below still runs)"
fi
if cosign verify --certificate-identity-regexp "$ID_RX" --certificate-oidc-issuer "$ISSUER" "$REPO@$DIGEST" >/dev/null 2>/tmp/fedmp-verify.err; then
  echo "adapter image signature: verified ($REPO@$DIGEST)"
else
  echo "adapter image signature: FAILED ($REPO@$DIGEST)" >&2; tail -3 /tmp/fedmp-verify.err >&2; rc=1
fi
rm -f /tmp/fedmp-verify.err
[ $rc = 0 ] || echo "Not installing: the pack or its image is not the one the release workflow built. FEDMP_VERIFY=0 skips this check." >&2
exit $rc
