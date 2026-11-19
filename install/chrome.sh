#!/bin/bash
# Start the headless Chrome used for the UI calls and log in to VCF Operations. Only the Chrome this script
# started is ever stopped (its PID is kept in the work directory). The work directory is private (mode 700).
# Needs OPS_HOST and OPS_PW (admin password) in the environment. usage: chrome.sh [start|stop] [height]
D="$(cd "$(dirname "$0")" && pwd)"
W="${WORK_DIR:-$HOME/.fedmp-install}"; mkdir -p "$W" && chmod 700 "$W" || exit 1
stop() {
  # kill only a process that is still the Chrome started here: a stale PID file may name another process
  if [ -f "$W/chrome.pid" ]; then
    pid="$(cat "$W/chrome.pid")"
    if [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q -- "--user-data-dir=$W/chrome-prof"; then
      kill "$pid" 2>/dev/null
    fi
    rm -f "$W/chrome.pid"
  fi
  rm -rf "$W/chrome-prof"  # the profile holds the admin session: it does not outlive the browser
}
if [ "${1:-start}" = stop ]; then stop; exit 0; fi
: "${OPS_HOST:?set OPS_HOST}" "${OPS_PW:?set OPS_PW}"
stop; sleep 1
# The debugging port listens on 127.0.0.1 only. --ignore-certificate-errors accepts the certificate of
# VCF Operations without checking it: use this on a trusted network, or drop the flag when the certificate
# is signed by an authority this host trusts.
nohup google-chrome --headless=new --remote-debugging-address=127.0.0.1 --remote-debugging-port=${CDP_PORT:-9333} \
  --ignore-certificate-errors --window-size=1680,${2:-1050} --user-data-dir="$W/chrome-prof" about:blank \
  > "$W/chrome.log" 2>&1 &
echo $! > "$W/chrome.pid"
sleep 5
node "$D/uitoken.mjs" >/dev/null 2>&1; echo "login rc=$? (2 is expected)"
