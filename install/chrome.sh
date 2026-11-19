#!/bin/bash
# (Re)start the headless Chrome used for the UI calls and log in to VCF Operations.
# Needs OPS_HOST and OPS_PW (admin password) in the environment. usage: chrome.sh [height]
D="$(cd "$(dirname "$0")" && pwd)"; W="${WORK_DIR:-${TMPDIR:-/tmp}/fedmp-install}"; mkdir -p "$W"
: "${OPS_HOST:?set OPS_HOST}" "${OPS_PW:?set OPS_PW}"
pkill -x chrome; sleep 2
(nohup google-chrome --headless=new --remote-debugging-port=${CDP_PORT:-9333} --ignore-certificate-errors \
  --window-size=1680,${1:-1050} --user-data-dir="$W/chrome-prof" about:blank > "$W/chrome.log" 2>&1 &)
sleep 5
node "$D/uitoken.mjs" >/dev/null 2>&1; echo "login rc=$? (2 is expected)"
