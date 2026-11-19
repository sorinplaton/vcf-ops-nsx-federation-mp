#!/bin/bash
# Read or switch "unsigned PAK installation" in the VCF Operations admin console.
# Needs OPS_HOST and OPS_PW (admin password) in the environment, and google-chrome and node on the host.
# usage: upi.sh getUPIStatus | enableUPI | disableUPI
D="$(cd "$(dirname "$0")" && pwd)"
: "${OPS_HOST:?set OPS_HOST}" "${OPS_PW:?set OPS_PW}"
# start the headless Chrome if it is not running
curl -s -o /dev/null http://127.0.0.1:${CDP_PORT:-9333}/json || "$D/chrome.sh" 1050 >/dev/null 2>&1
node "$D/adminupi.mjs" "${1:-getUPIStatus}"
