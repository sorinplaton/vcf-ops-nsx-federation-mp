# Two stages. The first is the build the SDK expects: its base-adapter image (Debian 12, built by mp-build from a
# template inside the SDK, so it cannot be pinned by digest here) with the SDK server, the adapter's Python packages
# and the adapter code. The second is the image that ships: the same Python files on a Wolfi base, because Debian 12
# carries about a hundred HIGH or CRITICAL findings that have no fixed version there (SECURITY.md).
FROM base-adapter:python-1.2.0 AS sdk
COPY commands.cfg .
# Exact versions with their SHA-256 (tools/hash_requirements.py), no dependency resolution: the image gets these
# files and no others. cryptography 50.0.2 replaces the 44.0.0 the SDK library pins (known vulnerabilities; the SDK
# only uses it to load a PEM certificate). Keep image-requirements.txt in step (tools/image_lock.sh).
COPY adapter_requirements.txt root_requirements.txt ./
RUN pip3 install --no-cache-dir --no-deps --require-hashes -r adapter_requirements.txt
# setuptools of the base image (70.3.0) has a known vulnerability; it is not used at run time, but scanners flag it.
USER root
RUN pip3 install --no-cache-dir --no-deps --require-hashes -r root_requirements.txt && rm root_requirements.txt
# pip is not needed at run time, and its bundled copy of setuptools 70.3.0 is what scanners report.
RUN rm -rf /usr/local/lib/python3.11/site-packages/pip /usr/local/lib/python3.11/site-packages/pip-*.dist-info
USER aria-ops-adapter-user
COPY app app

# Wolfi base pinned by digest; move the digest by hand before a release (docs/RELEASE.md). Wolfi is a rolling
# distribution: python-3.11 is the
# current 3.11 build at the time of the image build, the same minor version as the SDK stage, so the compiled
# packages copied from it load unchanged.
FROM cgr.dev/chainguard/wolfi-base@sha256:4cf5932a0f95a88af352f77e4efa67480cab0e1f7a48f0eddca3cce003a7c0fb
# commands.cfg calls /usr/local/bin/python, where the SDK image has it.
RUN apk add --no-cache python-3.11 \
    && mkdir -p /usr/local/bin \
    && ln -s /usr/bin/python3.11 /usr/local/bin/python3 && ln -s /usr/bin/python3.11 /usr/local/bin/python \
    && adduser -D -u 1000 -s /bin/sh aria-ops-adapter-user \
    && chmod 777 /var/log
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8
COPY --from=sdk /usr/local/lib/python3.11/site-packages /usr/lib/python3.11/site-packages
COPY --from=sdk --chown=aria-ops-adapter-user:aria-ops-adapter-user /home/aria-ops-adapter-user /home/aria-ops-adapter-user
USER aria-ops-adapter-user
WORKDIR /home/aria-ops-adapter-user/src/app
EXPOSE 443 8080
ENTRYPOINT ["python3"]
CMD ["-m", "swagger_server"]
