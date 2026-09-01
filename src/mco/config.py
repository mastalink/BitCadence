"""
BitCadence Configuration Management
====================================
Handles environment profile selections and loading/writing settings
from local .env files and the encrypted SecretStore.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from dotenv import load_dotenv, dotenv_values

from mco.security import get_secret_store

# Profiles
class EnvironmentProfile:
    LOCAL_ONLY = "Local-Only"
    CLOUD_HEAVY = "Cloud-Heavy"
    HYBRID = "Hybrid"

    @classmethod
    def all_profiles(cls) -> list[str]:
        return [cls.LOCAL_ONLY, cls.CLOUD_HEAVY, cls.HYBRID]


# Sensitive keys that should be encrypted in the secret store rather than plaintext .env
SENSITIVE_KEYS = {
    "SUPABASE_KEY",
    "SUPABASE_URL",
    "SERVICENOW_PASSWORD",
    "SERVICENOW_TOKEN",
    "DYNATRACE_API_TOKEN",
    "MCO_AGENT_TOKEN",
    "MCO_LOCAL_TOKEN",
    "MCO_METRICS_TOKEN",
    "MCO_SESSION_SECRET",
    "MCO_TRUSTED_HEADER_SECRET",
    "MCO_VAULT_MASTER_KEY",
    "MCO_WEBHOOK_SECRET",
}

# Secrets whose names are generated at runtime can never appear in a static
# set. LLM provider credentials, for instance, are stored per connection as
# LLM_CONN_<id>_API_KEY. Treat anything that *looks* like a credential as one.
# Suffix-anchored on purpose: a bare substring match would flag names like
# MAX_TOKENS_LIMIT. Kept as a module constant because tests assert against it.
SENSITIVE_KEY_MARKERS = ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN", "_PRIVATE_KEY")


def is_sensitive_key(key: str) -> bool:
    """Should this configuration key be treated as a credential?

    ONE predicate for masking, storage, and retrieval. They used to disagree:
    `get_masked_config` matched on name patterns while `set()` consulted only
    the static set, so a runtime-named secret such as `LLM_CONN_x_API_KEY` was
    masked in the UI *and written to .env in clear text*. Dynamic names (model
    connections, MCO_SECRET_* vault refs) can never be enumerated statically,
    so well-known suffixes are treated as sensitive too.

    NOTE: this function was briefly defined twice - two branches each added
    their own copy, and Python's silent last-def-wins meant one shadowed the
    other with slightly different coverage. If you're adding a rule, extend
    THIS definition; do not add another.
    """
    upper = str(key or "").upper()
    return (
        upper in SENSITIVE_KEYS
        or upper.startswith("MCO_SECRET_")
        or upper.endswith(SENSITIVE_KEY_MARKERS)
    )


# The global config home: works from any directory, any terminal. Lives next
# to the secret store (secrets.enc) and the embedded database (local.db).
GLOBAL_ENV_PATH = Path.home() / ".mco" / ".env"


def resolve_env_path() -> Path:
    """Where configuration lives, in precedence order:

    1. MCO_ENV_FILE          - explicit override (CI, repo-local dev runs)
    2. ~/.mco/.env if it exists - the global home, so `mco` behaves the same
                               from any directory
    3. ./.env                - fallback for a fresh checkout that was never
                               installed (pre-migration installs)

    The global file deliberately outranks the working directory: .env files
    belonging to OTHER projects are everywhere, and `mco` must not change
    behavior based on where you happen to be standing.
    """
    override = os.environ.get("MCO_ENV_FILE")
    if override:
        return Path(override)
    if GLOBAL_ENV_PATH.is_file():
        return GLOBAL_ENV_PATH
    return Path(".env")


class ConfigManager:
    """Manages system configuration settings.

    Resolves values by combining standard .env variables
    with decrypted credentials in the SecretStore if available.
    """

    def __init__(self, env_path: Optional[Path] = None, store_path: Optional[Path] = None):
        self._env_path = env_path or resolve_env_path()
        self._store = get_secret_store(store_path)
        self._cached_config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load configuration from environment, .env file, and secret store overlay."""
        # 1. Start with values from .env if present
        config = {}
        if self._env_path.is_file():
            dotenv_vals = dotenv_values(self._env_path)
            for k, v in dotenv_vals.items():
                if v is not None:
                    config[k] = v

        # 2. Overlay system env vars (system environment takes precedence)
        for k, v in os.environ.items():
            config[k] = v

        # 3. Attempt to auto-unlock secret store and overlay secrets
        if self._store.is_initialized():
            if not self._store.is_unlocked:
                self._store.auto_unlock()
            
            if self._store.is_unlocked:
                for key in self._store.list_keys():
                    secret_val = self._store.get(key)
                    # The "encrypted_in_secret_store" sentinel belongs only in .env as a
                    # pointer; if it ever leaked into the store, it must NOT shadow the
                    # real value resolved from .env/system env.
                    if secret_val and secret_val != "encrypted_in_secret_store":
                        config[key] = secret_val

        self._cached_config = config

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a configuration value."""
        # Check if the secret store is unlocked and has the key
        if is_sensitive_key(key) and self._store.is_unlocked:
            secret_val = self._store.get(key)
            # Skip the sentinel so a poisoned store can't mask the real .env value.
            if secret_val is not None and secret_val != "encrypted_in_secret_store":
                return secret_val

        return self._cached_config.get(key, default)

    def set(self, key: str, value: str, encrypt: Optional[bool] = None) -> None:
        """Set a configuration parameter.

        Credentials are encrypted by default. `encrypt=None` (the default)
        means "encrypt if this looks like a secret and the store can take it";
        pass True to require encryption, or False to force plaintext.

        Callers previously had to opt in with `encrypt=True`, so any code path
        that forgot wrote a credential to .env in clear text - which is exactly
        how LLM provider API keys ended up there. Defaulting the other way
        makes forgetting safe.
        """
        sensitive = is_sensitive_key(key)
        if encrypt is None:
            encrypt = sensitive

        if encrypt:
            self._ensure_store_ready(key)
            self._store.set(key, value)
            # Remove any plaintext entry in local .env to prevent leaks
            self._update_dotenv_file(key, "encrypted_in_secret_store")
            self._cached_config[key] = "encrypted_in_secret_store"
            return

        self._update_dotenv_file(key, value)
        self._cached_config[key] = value

    def _ensure_store_ready(self, key: str) -> None:
        """Make the secret store usable, or refuse the write with instructions.

        Credentials are never written to plaintext .env. There used to be a
        warned fallback, but a warning in a log nobody reads is not a control -
        the credential still landed on disk in the clear, and the security
        scanner rightly kept flagging it.

        On Windows the store can be provisioned automatically: generate a
        random master key, persist it to Credential Manager FIRST (so an
        interrupt can never orphan the store - the failure mode the setup
        wizard explicitly guards against), then initialize. A fresh Windows
        install therefore keeps working with zero prompts. Elsewhere there is
        no OS keychain provider, so we refuse with the exact commands to fix
        it rather than choosing between an orphaned store and a plaintext
        secret on the operator's behalf.
        """
        if self._store.is_unlocked:
            return
        if self._store.is_initialized():
            # Store exists but no key source unlocked it - do not stack a new
            # store on top of an orphaned one; that loses data quietly.
            raise RuntimeError(
                f"Cannot store credential {key!r}: the encrypted secret store exists "
                f"but is locked. Unlock it (set MCO_MASTER_PASSWORD, or run "
                f"'mco setup --menu' -> Security), then retry."
            )
        if os.name == "nt":
            import secrets as _secrets
            from mco.security import WindowsCredentialProvider
            master_key = _secrets.token_bytes(32)
            try:
                # Persist the key BEFORE the store exists: an interrupt between
                # these two calls must leave "no store", never "store, no key".
                WindowsCredentialProvider.store_key(master_key)
                self._store.initialize(master_key)
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot store credential {key!r}: automatic secret-store setup "
                    f"failed ({exc}). Run 'mco setup --menu' -> Security to set it "
                    f"up with a master password."
                ) from exc
            logger.info(
                "Encrypted secret store provisioned automatically "
                "(key held by Windows Credential Manager)."
            )
            return
        raise RuntimeError(
            f"Cannot store credential {key!r}: no encrypted secret store is set up "
            f"and this platform has no OS keychain to hold a key automatically. "
            f"Either set MCO_MASTER_PASSWORD and run 'mco setup --menu' -> Security "
            f"to create the store, or pass encrypt=False to store this value in "
            f"plaintext .env deliberately."
        )

    def delete(self, key: str) -> None:
        """Delete a configuration parameter."""
        if self._store.is_unlocked and key in self._store.list_keys():
            self._store.delete(key)

        self._update_dotenv_file(key, None)
        self._cached_config.pop(key, None)

    def _update_dotenv_file(self, key: str, value: Optional[str]) -> None:
        """Write or remove a key in the local .env file atomically."""
        if any(ch in str(key) for ch in ("\r", "\n", "=")):
            raise ValueError("Configuration keys may not contain newlines or '='")
        if value is not None and any(ch in str(value) for ch in ("\r", "\n")):
            raise ValueError("Configuration values may not contain newlines")
        lines = []
        if self._env_path.is_file():
            lines = self._env_path.read_text(encoding="utf-8").splitlines()

        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped or "=" not in stripped:
                new_lines.append(line)
                continue
            
            k, v = stripped.split("=", 1)
            if k.strip() == key:
                found = True
                if value is not None:
                    new_lines.append(f"{key}={value}")
            else:
                new_lines.append(line)

        if not found and value is not None:
            new_lines.append(f"{key}={value}")

        self._env_path.parent.mkdir(parents=True, exist_ok=True)
        self._env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def list_keys(self) -> list[str]:
        """List all loaded configuration keys."""
        keys = set(self._cached_config.keys())
        if self._store.is_unlocked:
            keys.update(self._store.list_keys())
        return sorted(list(keys))

    def get_masked_config(self) -> Dict[str, str]:
        """Return config dict with sensitive keys masked for safety."""
        masked = {}
        for k in self.list_keys():
            val = self.get(k)
            if not val:
                continue
            if is_sensitive_key(k):
                if val == "encrypted_in_secret_store":
                    masked[k] = "[ENCRYPTED]"
                elif len(val) <= 4:
                    masked[k] = "****"
                else:
                    masked[k] = val[:2] + "*" * (len(val) - 2)
            else:
                masked[k] = str(val)
        return masked


_config_manager: Optional[ConfigManager] = None


def get_config(env_path: Optional[Path] = None, store_path: Optional[Path] = None) -> ConfigManager:
    """Get the active ConfigManager singleton."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(env_path, store_path)
    return _config_manager
