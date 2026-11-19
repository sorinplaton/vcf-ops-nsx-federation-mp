"""Fault injection: make chosen NSX calls fail and check that their data is reported as unknown, not as empty.
Run like localrun.py. FAIL = comma-separated path fragments that answer HTTP 500."""
import os, sys, runpy
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pack', 'app'))
import nsxclient
FAIL = [f for f in os.environ.get("FAIL", "/sites/status,/backups/overview,/alarms,/cluster/status").split(",") if f]
_get = nsxclient.NsxClient.get
def get(self, path, params=None):
    if any(f in path for f in FAIL):
        self.check_trust()
        with self._lock:
            self.errors += 1
        raise nsxclient.NsxError(500, path, "injected")
    return _get(self, path, params)
nsxclient.NsxClient.get = get
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "localrun.py"), run_name="__main__")
