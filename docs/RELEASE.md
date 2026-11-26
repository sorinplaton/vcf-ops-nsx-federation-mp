# Release guide (maintainer)

How a release is built, signed and published: the side of [Option A](INSTALL.md#option-a-download-the-released-pack)
that users never see. Users download the `.pak` from a GitHub release; the cloud proxy pulls the adapter image
from `ghcr.io/sorinplaton/nsxfederation` by digest; the pack and the image are signed with cosign, and users
verify both with [`cosign.pub`](../cosign.pub).

## What a release contains

| Item | Where | Made by |
| --- | --- | --- |
| Adapter image | `ghcr.io/sorinplaton/nsxfederation@sha256:<digest>`, public | `mp-build` (step 3) |
| Image signature | `ghcr.io`, next to the image (tag `sha256-<digest>.sig`), and the Sigstore transparency log | `cosign sign` (step 5) |
| `NSXFederation_<version>.pak` | GitHub release assets | `mp-build` (step 3) |
| `NSXFederation_<version>.pak.sha256` | GitHub release assets | `sha256sum` (step 6) |
| `NSXFederation_<version>.pak.bundle` | GitHub release assets | `cosign sign-blob --bundle` (step 6) |
| `cosign.pub` | Repository root and GitHub release assets | Once, with the key pair |

## 1. Once per maintainer machine

*Run on: the build host*, with Docker and the GitHub CLI `gh`.

- [ ] `gh` logged in as the repository owner, with the `write:packages` scope. `gh auth status` lists the scopes;
      add the scope with `gh auth refresh -h github.com -s write:packages`.
- [ ] The tooling image: `sudo docker build -t fedmp-tooling tooling` from a clone of the repository.
- [ ] The signing key in `~/.config/fedmp-signing/`: `cosign.key` (the private key, encrypted with a password),
      `password`, and `cosign.pub`. Folder mode 700, files 600 except `cosign.pub`. The private key and the
      password never go into Git, a release or a chat.
- [ ] A backup of that folder, kept apart from the build host (a password manager or an encrypted vault).
      Without the private key, a new release cannot be signed with the key users already trust.

Every cosign command below runs from the official image `ghcr.io/sigstore/cosign/cosign:v2.4.1`, so nothing is
installed on the host. `-u` and `HOME=/tmp` let it write its cache as the current user.

### Creating the key pair (first release only, or after a key change)

```bash
mkdir -p ~/.config/fedmp-signing && chmod 700 ~/.config/fedmp-signing && cd ~/.config/fedmp-signing
openssl rand -base64 33 > password && chmod 600 password
sudo docker run --rm -u $(id -u):$(id -g) -v $PWD:/k -w /k -e COSIGN_PASSWORD="$(cat password)" \
  ghcr.io/sigstore/cosign/cosign:v2.4.1 generate-key-pair
chmod 600 cosign.key
```

Copy `cosign.pub` to the root of the repository and commit it.

## 2. Prepare the release

- [ ] The version in `pack/manifest.txt` and the `CHANGELOG.md` entry are committed on `main`.
- [ ] Tag the commit: `git tag v<version> && git push origin v<version>`.
- [ ] Build from a clean checkout of the tag, never from a working tree with local changes:

```bash
git worktree add --detach ~/fedmp-release v<version>
cd ~/fedmp-release
```

## 3. Build the pack and push the image

```bash
cd ~/fedmp-release
sudo docker run --rm --network host -u $(id -u):$(getent group docker | cut -d: -f3) -e HOME=$PWD/.sdkhome \
  -v /var/run/docker.sock:/var/run/docker.sock -v $(which docker):/usr/bin/docker -v $PWD:$PWD -w $PWD/pack \
  fedmp-tooling mp-build --no-ttl -P 18080 -r ghcr.io/sorinplaton/nsxfederation \
  --registry-username sorinplaton --registry-password "$(gh auth token)"

rm -f .sdkhome/.docker/config.json      # mp-build leaves the GitHub token there in clear text
```

`-P 18080` moves the port of the test container that `mp-build` starts during the build. The default, 8080, is
taken on the lab jump host (Guacamole). It does not change the pack, which still says `API_PORT=443`.

Read the image address and digest from the pack:

```bash
VERSION=$(sed -n 's/.*"version": "\(.*\)".*/\1/p' pack/manifest.txt)
PAK=pack/build/NSXFederation_$VERSION.pak
unzip -p $PAK adapter.zip > /tmp/adapter.zip
unzip -p /tmp/adapter.zip '*.conf' | grep -E '^(REGISTRY|REPOSITORY|DIGEST)='
DIGEST=$(unzip -p /tmp/adapter.zip '*.conf' | sed -n 's/^DIGEST=//p')
```

Expected: `REGISTRY=ghcr.io`, `REPOSITORY=/sorinplaton/nsxfederation`, and a `DIGEST`.

## 4. First release only: make the image public

GitHub creates a new package as private, and its API cannot change the visibility of a package owned by a
personal account. Once, in a browser logged in as the owner:

1. https://github.com/users/sorinplaton/packages/container/nsxfederation/settings
2. Danger Zone, Change visibility, Public, confirm with the package name.
3. Optional: connect the package to the repository, so that it shows on the repository page.

Later releases push new versions into the same package and stay public. Check that the image pulls without a
login (expected: `200`):

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:sorinplaton/nsxfederation:pull" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json' \
  https://ghcr.io/v2/sorinplaton/nsxfederation/manifests/$DIGEST
```

## 5. Sign the image

cosign needs a registry login to push the signature next to the image. Give it a login file of its own, and
delete the file right after:

```bash
AUTH=$(mktemp -d) && chmod 700 $AUTH
printf '{"auths":{"ghcr.io":{"auth":"%s"}}}' "$(printf 'sorinplaton:%s' "$(gh auth token)" | base64 -w0)" > $AUTH/config.json

sudo docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -e DOCKER_CONFIG=/auth \
  -e COSIGN_PASSWORD="$(cat ~/.config/fedmp-signing/password)" \
  -v ~/.config/fedmp-signing:/k:ro -v $AUTH:/auth:ro ghcr.io/sigstore/cosign/cosign:v2.4.1 \
  sign --yes --key /k/cosign.key ghcr.io/sorinplaton/nsxfederation@$DIGEST

rm -rf $AUTH
```

Sign the digest, never a tag: a tag can be moved to another image, a digest cannot. The signature is also
written to the public Sigstore transparency log (`tlog entry created with index ...`). The entry shows the
digest and the public key, nothing secret.

## 6. Checksum and sign the pack

```bash
cd ~/fedmp-release/pack/build
sha256sum NSXFederation_$VERSION.pak > NSXFederation_$VERSION.pak.sha256

sudo docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp \
  -e COSIGN_PASSWORD="$(cat ~/.config/fedmp-signing/password)" \
  -v ~/.config/fedmp-signing:/k:ro -v $PWD:/w -w /w ghcr.io/sigstore/cosign/cosign:v2.4.1 \
  sign-blob --yes --key /k/cosign.key --bundle NSXFederation_$VERSION.pak.bundle NSXFederation_$VERSION.pak
```

Sign the final `.pak`. Any later change to the file, including `tools/repack.py`, needs a new checksum and a new
signature.

## 7. Verify as a user would

With the public key only, the way [Option A](INSTALL.md#verify-the-signatures-recommended) tells users to do it.
Both must pass before the release is published:

```bash
cd ~/fedmp-release/pack/build
cp ~/.config/fedmp-signing/cosign.pub .
V="sudo docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/w -w /w ghcr.io/sigstore/cosign/cosign:v2.4.1"
$V verify-blob --key cosign.pub --bundle NSXFederation_$VERSION.pak.bundle NSXFederation_$VERSION.pak   # Verified OK
$V verify --key cosign.pub ghcr.io/sorinplaton/nsxfederation@$DIGEST                                   # The signatures were verified ...
sha256sum -c NSXFederation_$VERSION.pak.sha256                                                         # OK
```

## 8. Publish the GitHub release

Create it as a draft, check it, then publish:

```bash
cd ~/fedmp-release/pack/build
gh release create v<version> --draft --verify-tag --title "<version>" --notes-file <notes.md> \
  NSXFederation_$VERSION.pak NSXFederation_$VERSION.pak.sha256 NSXFederation_$VERSION.pak.bundle cosign.pub
gh release edit v<version> --draft=false --latest
```

The notes name the image digest, the requirements (cloud proxy to `ghcr.io` and
`pkg-containers.githubusercontent.com` on TCP 443, unsigned-pack switch) and link to Option A. The release of
`v1.13.1` is the model.

Then:

- [ ] Download the assets from the published release and run step 7 on them once more.
- [ ] Update the version strings in Option A of `docs/INSTALL.md` and `docs/INSTALL.fr.md` (release tag, file
      names) and commit.
- [ ] Remove the build checkout: `git worktree remove ~/fedmp-release`.

## If the private key is lost or exposed

1. Create a new key pair (step 1) and commit the new `cosign.pub`, with a `CHANGELOG.md` entry that says the key
   changed and from which release.
2. Sign the current release again with the new key: steps 5 and 6, then replace the `.bundle` and `cosign.pub`
   assets of the release with `gh release upload --clobber`.
3. If the key was exposed, say so in the release notes. Signatures made with the old key stay in the
   transparency log; users must stop trusting the old `cosign.pub`.
