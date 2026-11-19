"""Read-only NSX REST client for the NSX Federation management pack.

Design points (from the enterprise review):
  * a call budget per manager and per cycle, so one large site cannot starve the cycle
  * back-off on HTTP 429/503 (NSX throttles each client: 100 req/s, 40 concurrent by default)
  * cursor paging for every list call
  * certificate trust by pinned certificate (VCF Operations trust store) or by the SHA-256
    thumbprint the Global Manager holds for a Local Manager; a changed certificate is
    reported as its own condition, not as a plain connection failure
  * a small on-disk cache for slow-moving data (inventory, certificates, backups)
"""
import hashlib
import json
import os
import socket
import ssl
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CACHE_DIR = os.environ.get("FEDMP_CACHE_DIR", "/tmp/fedmp-cache")


class BudgetExceeded(Exception):
    pass


class CertificateNotTrusted(Exception):
    pass


class NsxError(Exception):
    def __init__(self, status: int, path: str, text: str = ""):
        super().__init__(f"HTTP {status} on {path}: {text[:200]}")
        self.status = status
        self.path = path


def server_cert_sha256(host: str, port: int = 443, timeout: float = 10.0) -> str:
    """SHA-256 fingerprint (lowercase hex) of the certificate the server presents."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha256(der or b"").hexdigest()


def pem_sha256(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.strip() + "\n")).hexdigest()


class NsxClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        timeout: int = 30,
        call_budget: int = 2000,
        trusted_fingerprints: Optional[Iterable[str]] = None,
        enforce_trust: bool = False,
    ) -> None:
        self.host = host.strip()
        self.timeout = timeout
        self.call_budget = call_budget
        self.calls = 0
        self.throttled = 0
        self.errors = 0
        self.trusted = {f.lower().replace(":", "") for f in (trusted_fingerprints or [])}
        self.enforce_trust = enforce_trust
        self.fingerprint: Optional[str] = None
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.verify = False  # trust is decided by check_trust(), by fingerprint
        self._session.headers.update({"Accept": "application/json"})
        self._trust_checked = False

    # ---- trust -------------------------------------------------------------------
    def check_trust(self) -> None:
        """Raise CertificateNotTrusted if trust is enforced and the server certificate
        is not one of the pinned fingerprints."""
        if self._trust_checked:
            return
        host, _, port = self.host.partition(":")
        self.fingerprint = server_cert_sha256(host, int(port or 443), min(self.timeout, 10))
        if self.enforce_trust and self.trusted and self.fingerprint not in self.trusted:
            raise CertificateNotTrusted(
                f"{self.host}: certificate {self.fingerprint[:16]}... is not a trusted certificate"
            )
        self._trust_checked = True

    # ---- HTTP --------------------------------------------------------------------
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.check_trust()
        delay = 1.0
        for attempt in range(4):
            with self._lock:
                if self.calls >= self.call_budget:
                    raise BudgetExceeded(f"{self.host}: call budget {self.call_budget} used")
                self.calls += 1
            resp = self._session.get(
                f"https://{self.host}{path}", params=params, timeout=self.timeout
            )
            if resp.status_code in (429, 503) and attempt < 3:
                with self._lock:
                    self.throttled += 1
                retry_after = resp.headers.get("Retry-After")
                time.sleep(min(float(retry_after) if retry_after else delay, 15.0))
                delay *= 2
                continue
            if resp.status_code >= 400:
                with self._lock:
                    self.errors += 1
                raise NsxError(resp.status_code, path, resp.text)
            return resp.json() if resp.content else {}
        raise NsxError(429, path, "throttled after retries")

    def try_get(self, path: str, params: Optional[Dict[str, Any]] = None, default: Any = None) -> Any:
        """GET that returns `default` on an HTTP error (a missing API on this version)."""
        try:
            return self.get(path, params)
        except NsxError:
            return default

    def list(self, path: str, params: Optional[Dict[str, Any]] = None, key: str = "results",
             page_size: int = 500, max_items: int = 50000) -> List[dict]:
        """Follow NSX cursor paging and return every item."""
        items: List[dict] = []
        query = dict(params or {})
        query.setdefault("page_size", page_size)
        while True:
            data = self.get(path, query)
            items.extend(data.get(key, []) or [])
            cursor = data.get("cursor")
            if not cursor or len(items) >= max_items:
                return items
            query["cursor"] = cursor

    def try_list(self, path: str, params: Optional[Dict[str, Any]] = None, key: str = "results") -> List[dict]:
        try:
            return self.list(path, params, key)
        except NsxError:
            return []

    def close(self) -> None:
        self._session.close()


# ---- cache for slow-moving data ------------------------------------------------------
class Cache:
    """Best-effort JSON cache on the container file system. If the container restarts the
    cache is empty and everything is collected again; nothing depends on it for correctness."""

    def __init__(self, namespace: str) -> None:
        safe = "".join(c if c.isalnum() else "_" for c in namespace)
        self.path = os.path.join(CACHE_DIR, safe + ".json")
        self.data: Dict[str, Any] = {}
        try:
            with open(self.path) as fd:
                self.data = json.load(fd)
        except Exception:
            self.data = {}
        self._lock = threading.Lock()
        self.dirty = False

    def get(self, key: str, max_age_s: float) -> Any:
        entry = self.data.get(key)
        if entry and time.time() - entry.get("t", 0) <= max_age_s:
            return entry.get("v")
        return None

    def get_any_age(self, key: str) -> Any:
        entry = self.data.get(key)
        return entry.get("v") if entry else None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = {"t": time.time(), "v": value}
            self.dirty = True

    def cached(self, key: str, max_age_s: float, loader: Any) -> Any:
        value = self.get(key, max_age_s)
        if value is None:
            value = loader()
            self.put(key, value)
        return value

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fd:
                json.dump(self.data, fd)
            os.replace(tmp, self.path)
        except Exception:
            pass
