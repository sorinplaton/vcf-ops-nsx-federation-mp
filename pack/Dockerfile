# The base-adapter image is built automatically by the integration SDK (mp-build), from a template inside the SDK;
# it cannot be pinned by digest here.
FROM base-adapter:python-1.2.0
COPY commands.cfg .
# Exact versions with their SHA-256 (tools/hash_requirements.py), no dependency resolution: the image gets these
# files and no others. cryptography 50.0.2 replaces the 44.0.0 the SDK library pins (known vulnerabilities; the SDK
# only uses it to load a PEM certificate). Keep image-requirements.txt in step (tools/image_lock.sh).
COPY adapter_requirements.txt root_requirements.txt ./
RUN pip3 install --no-cache-dir --no-deps --require-hashes -r adapter_requirements.txt
# setuptools of the base image (70.3.0) has a known vulnerability; it is not used at run time, but scanners flag it.
USER root
RUN pip3 install --no-cache-dir --no-deps --require-hashes -r root_requirements.txt && rm root_requirements.txt
USER aria-ops-adapter-user
COPY app app
