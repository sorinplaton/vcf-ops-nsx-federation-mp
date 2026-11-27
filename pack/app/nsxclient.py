"""Read-only NSX REST client for the NSX Federation management pack.

Design points (from the enterprise review):
  * a call budget per manager and per cycle, so one large site cannot starve the cycle
  * back-off on HTTP 429/503 (NSX throttles each client: 100 req/s, 40 concurrent by default)
  * cursor paging for every list call
  * certificate trust by pinned certificate (VCF Operations trust store), by the SHA-256
    thumbprint the Global Manager holds for a Local Manager, or by a CA chain with a name check
    (trust mode "ca": the CA certificates of the account and the system store); a changed certificate is
    reported as its own condition, not as a plain connection failure. Every connection of the
    session is bound to the fingerprint that was checked (urllib3 assert_fingerprint), so the
    credentials never travel to a server that presents another certificate
  * a small on-disk cache for slow-moving data (inventory, certificates, backups)
"""
import hashlib
import json
import math
import os
import re
import socket
import ssl
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _PinnedAdapter(requests.adapters.HTTPAdapter):
    """Every TLS connection of the session must present the certificate with this SHA-256 fingerprint."""

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(*args, **kwargs)

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


_PEM = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)


def split_pem(text: str) -> Tuple[List[str], int]:
    """(valid PEM certificates, number of blocks that are not certificates) found in `text`. A setting field
    keeps one line, so a pasted PEM arrives with spaces for line breaks: the base64 body is rewrapped."""
    good: List[str] = []
    bad = 0
    if (text or "").strip() and not _PEM.search(text):  # text without a PEM block (bare base64, PKCS#7...)
        bad += 1
    for body in _PEM.findall(text or ""):
        b64 = "".join(body.split())
        if not b64.isascii():  # a pasted zero-width space or typographic dash is not base64
            bad += 1
            continue
        pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) \
              + "\n-----END CERTIFICATE-----\n"
        try:
            ssl.create_default_context().load_verify_locations(cadata=pem)  # parses the certificate itself
            good.append(pem)
        except (ValueError, TypeError, ssl.SSLError):
            bad += 1
    return good, bad


def split_hostport(address: str) -> Tuple[str, int, str]:
    """(host, port, netloc for a URL) of "fqdn", "fqdn:port", "1.2.3.4", "[v6]", "[v6]:port" or a bare IPv6."""
    a = address.strip()
    if a.startswith("["):
        host, _, rest = a[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif a.count(":") > 1:  # a bare IPv6 address has no port
        host, port = a, ""
    else:
        host, _, port = a.partition(":")
    try:
        port_n = int(port) if port else 443
    except ValueError:
        port_n = 0  # not a port: the connection fails and the error names the address
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port_n}" if port else "")
    return host, port_n, netloc


def chain_check(host: str, port: int, cadata: str, timeout: float = 10.0) -> Tuple[Optional[str], str]:
    """(SHA-256 fingerprint of the certificate, "") when the server certificate chains to a CA of `cadata` or of
    the system store and is valid for `host` (DNS name or IP address in the SAN); (None, reason) otherwise."""
    ctx = ssl.create_default_context()
    # Python 3.13 adds VERIFY_X509_STRICT to the default context, which rejects a CA certificate without a key usage
    # extension (common for an enterprise or a lab CA). The chain and the name are still checked in full; clearing the
    # flag keeps the same verdict on every Python version the adapter image may carry.
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    if cadata.strip():
        ctx.load_verify_locations(cadata=cadata)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return hashlib.sha256(tls.getpeercert(binary_form=True) or b"").hexdigest(), ""
    except ssl.SSLCertVerificationError as e:
        return None, str(getattr(e, "verify_message", "") or e)


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
        ca_mode: bool = False,
        cadata: str = "",
    ) -> None:
        self.host = host.strip()
        self._addr, self._port, self._netloc = split_hostport(self.host)
        self.timeout = timeout
        self.call_budget = call_budget
        self.calls = 0  # HTTP requests sent, retries included: what the budget counts
        self.requests = 0  # calls asked by the adapter, each counted once
        self.budget_hit = False
        self.throttled = 0
        self.errors = 0
        self.retried = 0
        self.transport_failures = 0  # consecutive timeouts or dropped connections
        self.failed: Dict[str, str] = {}  # optional calls that failed in this cycle: path -> reason
        self.trusted = {f.lower().replace(":", "") for f in (trusted_fingerprints or [])}
        self.enforce_trust = enforce_trust or ca_mode
        self.ca_mode = ca_mode  # trusted: a pinned fingerprint, or a CA chain with a matching name
        self.cadata = cadata
        self.trust = ""  # how the certificate was trusted: "pinned", "ca", or "" (not trusted, or not checked)
        self.chain_error = ""
        self.fingerprint: Optional[str] = None
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.verify = False  # trust is decided by check_trust(), by fingerprint
        self._session.headers.update({"Accept": "application/json"})
        self._trust_checked = False

    # ---- trust -------------------------------------------------------------------
    def check_trust(self) -> None:
        """Raise CertificateNotTrusted if trust is enforced and the server certificate is not trusted: one of the
        pinned fingerprints, or (CA mode) a certificate that chains to a trusted CA and is valid for the address."""
        if self._trust_checked:
            return
        host, port_n = self._addr, self._port
        self.fingerprint = server_cert_sha256(host, port_n, min(self.timeout, 10))
        if self.fingerprint in self.trusted:
            self.trust = "pinned"
        elif self.ca_mode:
            chained, self.chain_error = chain_check(host, port_n, self.cadata, min(self.timeout, 10))
            if chained and chained == self.fingerprint:
                self.trust = "ca"
            elif not self.chain_error:
                self.chain_error = "the certificate changed between two connections"
        if self.enforce_trust and not self.trust:
            if self.ca_mode:
                raise CertificateNotTrusted(
                    f"{self.host}: certificate {self.fingerprint[:16]}... is not pinned and does not chain to a trusted "
                    f"CA for this address ({self.chain_error})")
            if not self.trusted:
                raise CertificateNotTrusted(
                    f"{self.host}: no trusted certificate is recorded for this manager (accept its certificate "
                    "on the account, or set certificate trust to 'ca' or 'report')"
                )
            raise CertificateNotTrusted(
                f"{self.host}: certificate {self.fingerprint[:16]}... is not a trusted certificate"
            )
        # bind the session to the certificate that was just checked
        self._session.mount("https://", _PinnedAdapter(self.fingerprint))
        self._trust_checked = True

    # ---- HTTP --------------------------------------------------------------------
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.check_trust()
        with self._lock:
            self.requests += 1
        delay = 1.0
        for attempt in range(4):
            with self._lock:
                if self.transport_failures >= 3:
                    # a manager that keeps timing out is skipped for the rest of the cycle, so that it cannot
                    # hold the collection for the timeout of every remaining call; a call not sent uses no budget
                    raise requests.ConnectionError(f"{self.host}: skipped after repeated timeouts in this cycle")
                if self.calls >= self.call_budget:
                    self.budget_hit = True
                    raise BudgetExceeded(f"{self.host}: call budget {self.call_budget} used")
                self.calls += 1
            try:
                resp = self._session.get(
                    f"https://{self._netloc}{path}", params=params, timeout=self.timeout
                )
                if resp.status_code not in (429, 503):
                    self.transport_failures = 0
            except (requests.Timeout, requests.ConnectionError):
                with self._lock:
                    self.transport_failures += 1
                if attempt >= 1:  # one retry on a timeout or a dropped connection
                    raise
                with self._lock:
                    self.retried += 1
                time.sleep(delay)
                continue
            if resp.status_code in (429, 503) and attempt < 3:
                with self._lock:
                    self.throttled += 1
                try:  # seconds; an HTTP date falls back to the back-off delay
                    wait = float(resp.headers.get("Retry-After") or delay)
                except ValueError:
                    wait = delay
                if not math.isfinite(wait) or wait < 0:  # "-1" or "nan" would make time.sleep raise
                    wait = delay
                time.sleep(min(wait, 15.0))
                delay *= 2
                continue
            if resp.status_code >= 400:
                with self._lock:
                    self.errors += 1
                    if resp.status_code in (429, 503):  # still throttled after the retries: counts for the breaker
                        self.transport_failures += 1
                raise NsxError(resp.status_code, path, resp.text)
            return resp.json() if resp.content else {}
        with self._lock:  # a manager that keeps answering 429 or 503 is skipped like one that times out
            self.transport_failures += 1
        raise NsxError(429, path, "throttled after retries")

    def try_get(self, path: str, params: Optional[Dict[str, Any]] = None, default: Any = None) -> Any:
        """GET that returns `default` when the call fails. The failure is recorded in `failed`, so the
        caller can tell "empty answer" from "no answer" (see `ok`)."""
        try:
            return self.get(path, params)
        except NsxError as e:
            self._failed(path, f"HTTP {e.status}")
        except requests.RequestException as e:
            self._failed(path, type(e).__name__, count=True)
        return default

    def _failed(self, path: str, reason: str, count: bool = False) -> None:
        with self._lock:
            self.failed[path] = reason
            if count:
                self.errors += 1

    def absent(self, path: str) -> bool:
        """True when `path` answered HTTP 404. The failure is then forgotten: the object does not exist on this
        manager, which is an answer, not a gap in the data."""
        with self._lock:
            if self.failed.get(path) == "HTTP 404":
                del self.failed[path]
                self.errors = max(0, self.errors - 1)
                return True
        return False

    def ok(self, path: str) -> bool:
        """False if an optional call to `path` failed in this cycle: its data is unknown, not empty."""
        return path not in self.failed

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
            if not cursor:
                return items
            if len(items) >= max_items:
                # more pages remain: the list is incomplete, so its data is reported as a gap, not as the whole
                self._failed(path, f"truncated at {len(items)} items")
                return items
            query["cursor"] = cursor

    def try_list(self, path: str, params: Optional[Dict[str, Any]] = None, key: str = "results") -> List[dict]:
        try:
            return self.list(path, params, key)
        except NsxError as e:
            self._failed(path, f"HTTP {e.status}")
        except requests.RequestException as e:
            self._failed(path, type(e).__name__, count=True)
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
        if not isinstance(self.data, dict):  # valid JSON of another shape is no cache either
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
            if value is not None:  # a failed load is not cached: it is tried again in the next cycle
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
