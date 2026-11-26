#!/bin/bash
# Install an unsigned pack through the UI route. Run AFTER "unsigned PAK installation" was enabled
# (upi.sh enableUPI): the admin-console login made by upi.sh logs the product UI session out, so this script
# logs in again first. However it ends (success, failure, Ctrl-C, a dropped terminal), it switches the setting
# back off and stops the headless Chrome it used. Exit code: 0 only when the install completed AND the setting
# is off.
# After the install it deletes the alert and symptom definitions an older version left behind (cleanup_content.py).
# Before anything else it checks the signatures with verify_pak.sh: a released pack (its .sigstore.json next to it)
# must verify or nothing is installed; FEDMP_VERIFY=0 skips the check, FEDMP_VERIFY=1 requires it for any pack.
# Needs OPS_HOST and OPS_PW (admin password) in the environment, and python3 with "requests". OPS_INSECURE=1 skips
# the certificate check of the suite API for that step (lab only); OPS_CA can name a CA file instead.
# usage: install_unsigned.sh <pak>
D="$(cd "$(dirname "$0")" && pwd)"
W="${WORK_DIR:-$HOME/.fedmp-install}"; mkdir -p "$W" && chmod 700 "$W" || exit 1
: "${OPS_HOST:?set OPS_HOST}" "${OPS_PW:?set OPS_PW}" "${1:?usage: install_unsigned.sh <pak>}"
rc=1; off=1; done=0
finish() {
  [ $done = 1 ] && return; done=1
  trap '' INT TERM HUP  # nothing interrupts the clean-up
  rm -f "$W/uisess.json"
  # the admin console refuses the change while the install is still running: retry until it reports "false"
  for i in $(seq 1 20); do
    out=$("$D/upi.sh" disableUPI 2>&1 | tail -2); echo "$out"
    if echo "$out" | grep -q '^status -> .*"status":"false"'; then off=0; break; fi
    sleep 30
  done
  [ $off = 0 ] || echo "WARNING: unsigned PAK installation is still enabled. Switch it off in the admin console." >&2
  "$D/chrome.sh" stop
  [ $rc = 0 ] && [ $off = 0 ]; exit $?
}
trap finish EXIT
trap 'rc=130; exit 130' INT TERM HUP
"$D/verify_pak.sh" "$1" || exit 1   # finish() still switches the setting off
# Right after the admin console login of upi.sh the product UI can take a few minutes to accept a login again:
# up to three logins, each followed by a minute of tries to read the session
got=1; for round in 1 2 3; do
  node "$D/uitoken.mjs" >/dev/null 2>&1; sleep 12
  for i in 1 2 3 4 5 6; do  # the token frame can take a while to load after the login
    ( umask 077; node "$D/uisess.mjs" > "$W/uisess.json" 2>/dev/null ) && { got=0; break; }; sleep 10
  done
  [ $got = 0 ] && break
  echo "no UI session yet (login $round of 3)" >&2; sleep 30; "$D/chrome.sh" start >/dev/null 2>&1  # fresh browser and login
done
if [ $got = 0 ]; then
  timeout 2100 python3 "$D/install_pak.py" "$W/uisess.json" "$1"; rc=$?
  # an upgrade keeps the definitions the new version no longer ships: delete those orphans (pack IDs only)
  if [ $rc = 0 ]; then
    python3 "$D/cleanup_content.py" "$1" --delete $([ "${OPS_INSECURE:-0}" = 1 ] && echo --insecure) \
      || echo "WARNING: orphan definitions were not all removed; run cleanup_content.py again" >&2
  fi
else
  echo "no UI session" >&2
fi
