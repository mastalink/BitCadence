"""
Lightweight per-token (fallback per-IP) rate limiting for the BitCadence gateway.

Implemented as a stdlib-only in-memory token bucket. No external dependencies.

Configuration (env / .env / secret store via MCO_RATE_LIMIT):
    MCO_RATE_LIMIT   — max requests per minute per identity (default: 120)
                       Set to 0 or empty to disable rate limiting entirely.

The identity key is, in order of preference:
  1. The Bearer token extracted from the Authorization header (per-token).
  2. The client IP from X-Forwarded-For (first hop) or request.client.host.

/healthz is always exempt.
"""

from __future__ import annotations

import os
import time
import threading
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ── Exempt paths ──────────────────────────────────────────────────────────────

_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})


# ── Token bucket ──────────────────────────────────────────────────────────────

class _Bucket:
    """Single token bucket for one identity (thread-safe)."""

    __slots__ = ("tokens", "last_refill", "_lock")

    def __init__(self, capacity: float) -> None:
        self.tokens: float = capacity
        self.last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, capacity: float, refill_rate: float) -> bool:
        """Return True if the request is allowed, False if it should be 429'd.

        Uses a continuous refill: tokens accumulate at *refill_rate* tokens/sec
        up to *capacity*, then one token is consumed per request.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(capacity, self.tokens + elapsed * refill_rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class RateLimitStore:
    """Thread-safe store of per-identity token buckets with periodic GC."""

    def __init__(self, capacity: float, refill_rate: float) -> None:
        # capacity  = max burst (== requests/min default: equals limit)
        # refill_rate = tokens per second
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(self._capacity))
        self._lock = threading.Lock()
        self._last_gc: float = time.monotonic()

    def is_allowed(self, identity: str) -> bool:
        with self._lock:
            bucket = self._buckets[identity]
            self._maybe_gc()
        return bucket.consume(self._capacity, self._refill_rate)

    def _maybe_gc(self) -> None:
        """Evict idle buckets every ~60 seconds to prevent unbounded memory growth."""
        now = time.monotonic()
        if now - self._last_gc < 60:
            return
        self._last_gc = now
        # A bucket is idle when its token count == capacity (fully refilled = no
        # recent traffic). Safe to remove; it'll be re-created on next request.
        stale = [k for k, b in self._buckets.items() if b.tokens >= self._capacity]
        for k in stale:
            del self._buckets[k]


# ── Identity extraction ───────────────────────────────────────────────────────

def _extract_identity(request: Request) -> str:
    """Return bearer token if present, else best-effort client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return f"tok:{token}"
    # Fall back to IP: honour X-Forwarded-For for reverse-proxy deploys.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return f"ip:{xff.split(',')[0].strip()}"
    host = (request.client.host if request.client else None) or "unknown"
    return f"ip:{host}"


# ── Middleware ────────────────────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate-limiting middleware.

    Injected by create_app(); exempt from /healthz.  Returns HTTP 429 with a
    JSON body on excess, identical to the rest of the gateway's error shape.
    """

    def __init__(self, app, store: RateLimitStore) -> None:
        super().__init__(app)
        self._store = store

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        identity = _extract_identity(request)
        if not self._store.is_allowed(identity):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "detail": "Too many requests. Please slow down.",
                },
            )
        return await call_next(request)


# ── Factory ───────────────────────────────────────────────────────────────────

def _parse_limit() -> int | None:
    """Read MCO_RATE_LIMIT from env. Returns None to disable, else int >= 1."""
    # Defer to os.environ here; get_config() is available but importing it
    # would create a circular-import risk (cli -> ratelimit -> config is fine,
    # but avoid importing routes from here).
    raw = os.environ.get("MCO_RATE_LIMIT", "").strip()
    if raw == "":
        # Default: 120 requests per minute per identity.
        return 120
    try:
        val = int(raw)
    except ValueError:
        return 120
    return val if val > 0 else None  # 0 == disabled


def build_rate_limit_store() -> RateLimitStore | None:
    """Return a configured RateLimitStore, or None if limiting is disabled."""
    limit = _parse_limit()
    if limit is None:
        return None
    capacity = float(limit)
    refill_rate = capacity / 60.0  # tokens per second
    return RateLimitStore(capacity=capacity, refill_rate=refill_rate)
