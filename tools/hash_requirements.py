#!/usr/bin/env python3
"""Write pack/adapter_requirements.txt and pack/root_requirements.txt: exact versions with the SHA-256 of every
file PyPI publishes for them, for pip install --no-deps --require-hashes in pack/Dockerfile.
Edit the PINS below (keep them equal to pack/image-requirements.txt), run it, and rebuild.
usage: python3 tools/hash_requirements.py"""
import json, os, urllib.request

PINS = [("vmware-aria-operations-integration-sdk-lib", "1.1.0"), ("aenum", "3.1.11"), ("cffi", "2.1.1"),
        ("cryptography", "50.0.2"), ("pycparser", "3.12")]
ROOT_PINS = [("setuptools", "84.2.1")]
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pack")


def block(pkgs):
    out = []
    for name, ver in pkgs:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=30) as r:
            hashes = sorted({u["digests"]["sha256"] for u in json.load(r)["urls"]})
        out.append(f"{name}=={ver} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes))
    return "\n".join(out) + "\n"


HEADER = """# Python packages the adapter image adds to the SDK base image, installed with --no-deps --require-hashes
# (pack/Dockerfile): exact versions, every file hashed. The SDK library pins cryptography 44.0.0, which has
# known vulnerabilities; 50.0.2 replaces it (the SDK only uses it to load a PEM certificate). Keep
# pack/image-requirements.txt in step (tools/image_lock.sh). Hashes from PyPI: tools/hash_requirements.py.
"""
ROOT_HEADER = """# Installed as root over the base image (pack/Dockerfile): setuptools of the base image (70.3.0) has a known
# vulnerability; not used at run time, but scanners flag it.
"""
open(os.path.join(HERE, "adapter_requirements.txt"), "w").write(HEADER + block(PINS))
open(os.path.join(HERE, "root_requirements.txt"), "w").write(ROOT_HEADER + block(ROOT_PINS))
print("wrote pack/adapter_requirements.txt and pack/root_requirements.txt")
