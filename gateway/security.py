import hashlib
import hmac
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

MAX_BODY_BYTES = 65_536
MAX_TEXT_LENGTH = 4_000
SUPPORTED_PROFILES = {"banking_upi", "healthcare_rx", "ecommerce_refund"}

REVOKED_LEGACY_KEYS = {
    "plx_live_growth_9482",
    "plx_live_key_99",
    "your_api_key",
    "your_parallix_api_key",
}

_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"sk-[0-9A-Za-z\-_]{20,}"),
    re.compile(r"plx_live_[0-9A-Za-z\-_]+"),
)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED_CREDENTIAL]", cleaned)
    cleaned = re.sub(
        r"(Bearer\s+)[0-9A-Za-z\-_.]+",
        r"\1[REDACTED_TOKEN]",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def sanitize_text(value: Any, max_length: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()
    if len(cleaned) > max_length:
        raise ValueError(f"Input text exceeds maximum allowed length of {max_length} characters.")
    return cleaned


@dataclass(frozen=True)
class AuthenticatedTenant:
    tenant_id: str
    role: str = "audit_client"


class ApiKeyManager:
    def __init__(self, configured_keys: dict[str, str] | None = None) -> None:
        self._digest_to_tenant: dict[str, AuthenticatedTenant] = {}
        self._revoked_digests: set[str] = {hash_api_key(k) for k in REVOKED_LEGACY_KEYS}

        extra_revoked = os.getenv("PARALLIX_REVOKED_KEY_HASHES", "")
        for item in extra_revoked.split(","):
            digest = item.strip().lower()
            if len(digest) == 64:
                self._revoked_digests.add(digest)

        if configured_keys is not None:
            for tenant_id, raw_key in configured_keys.items():
                self._register_key(tenant_id, raw_key)
        else:
            self._load_from_env()

    def _register_key(self, tenant_id: str, raw_key: str) -> None:
        clean_tenant = re.sub(r"[^a-zA-Z0-9_\-]", "_", tenant_id.strip()) or "api_tenant"
        clean_key = raw_key.strip()
        if not clean_key or clean_key.lower() in REVOKED_LEGACY_KEYS:
            return
        digest = hash_api_key(clean_key)
        if digest in self._revoked_digests:
            return
        self._digest_to_tenant[digest] = AuthenticatedTenant(tenant_id=clean_tenant)

    def _load_from_env(self) -> None:
        multi = os.getenv("PARALLIX_API_KEYS", "").strip()
        if multi:
            for pair in multi.split(","):
                pair = pair.strip()
                if ":" in pair:
                    tenant_id, raw_key = pair.split(":", 1)
                    self._register_key(tenant_id, raw_key)
                elif pair:
                    self._register_key("configured_tenant", pair)

        single = os.getenv("PARALLIX_API_KEY", "").strip()
        if single:
            self._register_key("primary_tenant", single)

    @property
    def is_configured(self) -> bool:
        return len(self._digest_to_tenant) > 0

    def validate_key(self, provided_key: str | None) -> tuple[bool, AuthenticatedTenant | None, str]:
        if not provided_key or not isinstance(provided_key, str) or not provided_key.strip():
            return False, None, "Missing X-Parallix-Key header."

        candidate = provided_key.strip()
        if candidate.lower() in REVOKED_LEGACY_KEYS:
            return False, None, "API key has been revoked."

        candidate_digest = hash_api_key(candidate)
        if candidate_digest in self._revoked_digests:
            return False, None, "API key has been revoked."

        if not self._digest_to_tenant:
            return False, None, "Server API authentication is not configured (fail-closed)."

        matched_tenant: AuthenticatedTenant | None = None
        for stored_digest, tenant in self._digest_to_tenant.items():
            if hmac.compare_digest(candidate_digest, stored_digest):
                matched_tenant = tenant

        if matched_tenant is None:
            return False, None, "Invalid X-Parallix-Key credential."

        return True, matched_tenant, "Authenticated"


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = [t for t in self._buckets.get(client_key, []) if t > cutoff]
            if len(timestamps) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - timestamps[0])) + 1)
                self._buckets[client_key] = timestamps
                return False, retry_after
            timestamps.append(now)
            self._buckets[client_key] = timestamps
            return True, 0


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 600, max_entries: int = 2000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._records: dict[str, tuple[float, str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        canonical = repr(sorted(payload.items())).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def lookup(
        self, scope: str, idempotency_key: str | None, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None]:
        if not idempotency_key or not idempotency_key.strip():
            return "MISS", None

        clean_key = sanitize_text(idempotency_key, max_length=128)
        composite = f"{scope}:{clean_key}"
        fp = self.fingerprint(payload)
        now = time.monotonic()

        with self._lock:
            expired = [k for k, (exp, _, _) in self._records.items() if exp <= now]
            for k in expired:
                del self._records[k]

            existing = self._records.get(composite)
            if existing is None:
                return "MISS", None
            _, stored_fp, stored_response = existing
            if not hmac.compare_digest(fp, stored_fp):
                return "CONFLICT", None
            return "HIT", stored_response

    def store(
        self,
        scope: str,
        idempotency_key: str | None,
        payload: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        if not idempotency_key or not idempotency_key.strip():
            return
        clean_key = sanitize_text(idempotency_key, max_length=128)
        composite = f"{scope}:{clean_key}"
        fp = self.fingerprint(payload)
        now = time.monotonic()

        with self._lock:
            if len(self._records) >= self.max_entries:
                oldest_key = next(iter(self._records))
                del self._records[oldest_key]
            self._records[composite] = (now + self.ttl_seconds, fp, response)
