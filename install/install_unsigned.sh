#!/bin/bash
# Install an unsigned pack through the UI route. Run AFTER "unsigned PAK installation" was enabled
# (upi.sh enableUPI): the admin-console login made by upi.sh logs the product UI session out,
# so this script logs in again first. It switches the setting back off at the end, also on failure.
# Needs OPS_HOST and OPS_PW (admin password) in the environment, and python3 with "requests".
# usage: install_unsigned.sh <pak>
D="$(cd "$(dirname "$0")" && pwd)"; W="${WORK_DIR:-${TMPDIR:-/tmp}/fedmp-install}"; mkdir -p "$W"
: "${OPS_HOST:?set OPS_HOST}" "${OPS_PW:?set OPS_PW}" "${1:?usage: install_unsigned.sh <pak>}"
node "$D/uitoken.mjs" >/dev/null 2>&1; sleep 12
( umask 077; node "$D/uisess.mjs" > "$W/uisess.json" 2>/dev/null ) || { echo "no UI session"; exit 1; }
timeout 900 python3 "$D/install_pak.py" "$W/uisess.json" "$1"; rc=$?
rm -f "$W/uisess.json"
# the admin console refuses the change while the install is still running: retry until it is off
for i in $(seq 1 20); do
  out=$("$D/upi.sh" disableUPI 2>&1 | tail -2); echo "$out"
  case "$out" in *'"status":"false"'*) break;; esac
  sleep 30
done
exit $rc
