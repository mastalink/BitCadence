"""
Named connections to LLM providers, managed from the Control Panel
("Settings -> Model Connections").

BitCadence orchestrates AGENTS (Claude Code, Codex, Gemini CLI, custom
workers) - it does not itself call an LLM to do the work of a job. A "model
connection" here is deliberately narrow: a named, testable credential an
operator can hand to a custom worker/executor (or use to sanity-check that a
key is live) without leaving the Control Panel. It is not a chat/completions
gateway.

Storage split (mirrors how every other credential in this codebase is
handled - see admin_routes.py SETTING_GROUPS and mco/security.py):
- Metadata (name, provider, base_url, model, org) lives in the
  `llm_connections` table (LocalStore or Supabase - same dual-backend
  contract as agent_registry).
- The API key itself is never stored in that table. It goes through the same
  tenant-aware SecretVault seam used by every saved instance. The local
  Adapter stores AES-256-GCM ciphertext in SecretStore; the shared Adapter
  stores ciphertext in `secret_records` while its master key remains outside
  the database. Reads only ever report key_set: bool.

Testing a connection makes ONE cheap, free "list models" call to the
provider - it validates the key/base_url authenticate without spending
tokens on a real generation.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

# Fixed base URLs for the built-in providers - only `custom` accepts an
# operator-supplied base_url. Letting `provider=anthropic` also carry an
# arbitrary base_url would turn "configure a connection" into an SSRF
# primitive; pinning these closes that off while keeping `custom` for
# self-hosted / OpenAI-compatible endpoints (Ollama, vLLM, LM Studio, ...).
PROVIDERS = {
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "test_path": "/v1/models",
        "auth_style": "anthropic",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "test_path": "/models",
        "auth_style": "bearer",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "test_path": "/models",
        "auth_style": "query",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "base_url": None,  # operator-supplied, required for this provider
        "test_path": "/models",
        "auth_style": "bearer",
    },
}


def config_key_for(connection_id: str) -> str:
    """Legacy local-vault locator for one connection's API key."""
    return f"LLM_CONN_{connection_id}_API_KEY"


def _client(transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    kwargs = {"timeout": 10.0}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def test_connection(
    provider: str,
    api_key: str,
    base_url: Optional[str] = None,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict:
    """Make one cheap, real call to prove a key/base_url pair actually
    authenticates. Never raises - always returns {ok, detail, latency_ms}."""
    meta = PROVIDERS.get(provider)
    if meta is None:
        return {"ok": False, "detail": f"Unknown provider '{provider}'", "latency_ms": None}
    if not api_key:
        return {"ok": False, "detail": "No API key configured for this connection", "latency_ms": None}

    url_base = meta["base_url"] or base_url
    if not url_base:
        return {"ok": False, "detail": "base_url is required for a custom connection", "latency_ms": None}

    url = url_base.rstrip("/") + meta["test_path"]
    headers = {}
    params = {}
    if meta["auth_style"] == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif meta["auth_style"] == "bearer":
        headers = {"Authorization": f"Bearer {api_key}"}
    elif meta["auth_style"] == "query":
        params = {"key": api_key}

    started = time.monotonic()
    try:
        with _client(transport) as client:
            resp = client.get(url, headers=headers, params=params)
        latency_ms = round((time.monotonic() - started) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "detail": "Connection OK", "latency_ms": latency_ms}
        if resp.status_code in (401, 403):
            return {"ok": False, "detail": f"Authentication rejected (HTTP {resp.status_code})",
                    "latency_ms": latency_ms}
        return {"ok": False, "detail": f"Unexpected HTTP {resp.status_code}", "latency_ms": latency_ms}
    except httpx.TimeoutException:
        return {"ok": False, "detail": "Timed out reaching the provider", "latency_ms": None}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"Request failed: {type(e).__name__}", "latency_ms": None}
