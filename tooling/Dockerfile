# Build tooling (SDK mp-build, pytest). Base image pinned by digest; Python packages locked with hashes in
# requirements.txt (pip-compile --generate-hashes from requirements.in). Dependabot proposes updates to both.
FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c
RUN apt-get update && apt-get install -y --no-install-recommends docker.io git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt
WORKDIR /work
