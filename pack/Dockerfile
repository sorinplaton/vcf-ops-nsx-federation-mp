# The base-adapter image is built automatically by the integration SDK.
FROM base-adapter:python-1.2.0
COPY commands.cfg .
COPY adapter_requirements.txt .
RUN pip3 install -r adapter_requirements.txt
# The SDK library pins cryptography 44.0.0, which has known vulnerabilities. It only uses it to load a PEM
# certificate (aria/ops/certificate_info.py), so a fixed release replaces it. Keep image-requirements.txt in step.
RUN pip3 install --no-deps cryptography==50.0.2
# setuptools of the base image (70.3.0) has a known vulnerability; it is not used at run time, but scanners flag it.
USER root
RUN pip3 install --no-cache-dir --upgrade "setuptools==84.2.1"
USER aria-ops-adapter-user
COPY app app
