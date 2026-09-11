"""Unit tests for configuration manager and profiles."""

import tempfile
from pathlib import Path
import os
import pytest

from mco.config import ConfigManager, EnvironmentProfile, is_sensitive_key
from mco.security import SecretStore


def test_config_profiles():
    """Verify standard environment profile constants."""
    assert EnvironmentProfile.LOCAL_ONLY == "Local-Only"
    assert EnvironmentProfile.CLOUD_HEAVY == "Cloud-Heavy"
    assert EnvironmentProfile.HYBRID == "Hybrid"
    assert len(EnvironmentProfile.all_profiles()) == 3


def test_config_manager_file_io():
    """Verify that writing settings writes to the underlying file, and overlays read correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        store_file = Path(tmpdir) / "secrets.enc"
        
        manager = ConfigManager(env_path=env_file, store_path=store_file)
        
        # Initial is empty
        assert manager.get("OPERATOR_NAME") is None
        
        # Plain write
        manager.set("OPERATOR_NAME", "Jane Doe")
        assert manager.get("OPERATOR_NAME") == "Jane Doe"
        
        # Verify it was written to disk
        content = env_file.read_text(encoding="utf-8")
        assert "OPERATOR_NAME=Jane Doe" in content

        # Reload manager and verify it retrieves the key
        new_manager = ConfigManager(env_path=env_file, store_path=store_file)
        assert new_manager.get("OPERATOR_NAME") == "Jane Doe"


def test_masked_config():
    """Verify that sensitive keys are masked in output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # store_path is NOT optional in tests: omitting it binds this manager
        # to the singleton pointing at the operator's real ~/.mco store.
        manager = ConfigManager(env_path=env_file, store_path=Path(tmpdir) / "s.enc")
        manager.set("SUPABASE_KEY", "sb_key_123456789", encrypt=False)
        manager.set("OPERATOR_NAME", "Alice")
        
        masked = manager.get_masked_config()
        
        # Plain setting remains plain
        assert masked["OPERATOR_NAME"] == "Alice"
        # Sensitive setting is masked
        assert masked["SUPABASE_KEY"].startswith("sb")
        assert "*" in masked["SUPABASE_KEY"]
        assert "123456789" not in masked["SUPABASE_KEY"]


def test_config_manager_encryption():
    """Verify that ConfigManager.set(..., encrypt=True) behaves correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        store_file = Path(tmpdir) / "secrets.enc"
        
        manager = ConfigManager(env_path=env_file, store_path=store_file)
        
        # Reset and prepare the store state
        store = manager._store
        store.lock()
        try:
            Path(store._path).unlink(missing_ok=True)
        except Exception:
            pass
        store._secrets = None
        store._master_key = None
        store._envelope = None
        
        master_key = b"A" * 32
        store.initialize(master_key)
        
        # Verify store is unlocked initially
        assert store.is_unlocked
        
        # Use manager to set an encrypted value
        manager.set("SUPABASE_KEY", "super_secret_supabase_key", encrypt=True)
        
        # 1. Plain setting in .env must be the placeholder string
        assert env_file.is_file()
        content = env_file.read_text(encoding="utf-8")
        assert "SUPABASE_KEY=encrypted_in_secret_store" in content
        
        # 2. Getting from unlocked manager must return the real key
        assert manager.get("SUPABASE_KEY") == "super_secret_supabase_key"
        
        # 3. Lock store and confirm it returns the placeholder
        store.lock()
        assert not store.is_unlocked
        assert manager.get("SUPABASE_KEY") == "encrypted_in_secret_store"
        
        # 4. Unlock store and confirm it returns the real key again
        assert store.unlock(master_key)
        assert store.is_unlocked
        assert manager.get("SUPABASE_KEY") == "super_secret_supabase_key"


def test_sentinel_in_store_does_not_shadow_real_env_value():
    """Regression: a leaked 'encrypted_in_secret_store' sentinel in the store must
    not mask the real value resolved from .env (the bug behind 'Database not
    configured' even though the store unlocked)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        store_file = Path(tmpdir) / "secrets.enc"
        env_file.write_text(
            "SUPABASE_URL=https://real.example.co\nSUPABASE_KEY=real_key_123\n",
            encoding="utf-8",
        )

        manager = ConfigManager(env_path=env_file, store_path=store_file)
        store = manager._store
        store.lock()
        try:
            Path(store._path).unlink(missing_ok=True)
        except Exception:
            pass
        store._secrets = None
        store._master_key = None
        store._envelope = None
        store.initialize(b"A" * 32)

        # Poison the store with the sentinel, as the original setup bug did.
        store.set("SUPABASE_URL", "encrypted_in_secret_store")
        store.set("SUPABASE_KEY", "encrypted_in_secret_store")

        # Re-run load() so the overlay logic applies, then resolve.
        manager.load()
        assert manager.get("SUPABASE_URL") == "https://real.example.co"
        assert manager.get("SUPABASE_KEY") == "real_key_123"



# ── credentials are encrypted by default ──────────────────────────────────────

from mco.config import SENSITIVE_KEY_MARKERS, is_sensitive_key
import mco.security as security_mod


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    """A ConfigManager with its own .env and secret store.

    `get_secret_store()` is a process-wide singleton that ignores its
    store_path once created, so without resetting it every test inherits the
    previous test's store.
    """
    from mco.config import ConfigManager

    def _make(with_store: bool):
        monkeypatch.setattr(security_mod, "_store", None, raising=False)
        env = tmp_path / f"{'enc' if with_store else 'plain'}.env"
        store = tmp_path / f"{'enc' if with_store else 'plain'}.enc"
        cfg = ConfigManager(env_path=env, store_path=store)
        if with_store:
            cfg._store.initialize(b"0" * 32)
        return cfg, env

    return _make


@pytest.mark.parametrize("key", [
    "SUPABASE_KEY", "SERVICENOW_PASSWORD", "DYNATRACE_API_TOKEN", "MCO_WEBHOOK_SECRET",
    # Runtime-named secrets can never live in a static set - and these are
    # exactly what got written to .env in clear text.
    "LLM_CONN_abc123_API_KEY", "LLM_CONN_xyz_API_KEY",
    "SOME_PRIVATE_KEY", "CUSTOM_PASSWORD", "X_SECRET",
])
def test_credential_shaped_keys_are_sensitive(key):
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize("key", ["OPERATOR_NAME", "MCO_PROFILE", "MCO_GATEWAY_URL", "NTFY_URL"])
def test_ordinary_settings_are_not_sensitive(key):
    assert is_sensitive_key(key) is False


def test_secret_is_encrypted_without_the_caller_asking(fresh_config):
    """The regression this change exists for.

    `config.set(key, api_key)` with no `encrypt=` used to write clear text.
    Every caller had to remember; the LLM connections route did not.
    """
    cfg, env = fresh_config(with_store=True)
    cfg.set("LLM_CONN_abc_API_KEY", "sk-super-secret-value")

    on_disk = env.read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in on_disk, "credential leaked into .env"
    assert "encrypted_in_secret_store" in on_disk
    assert cfg._store.get("LLM_CONN_abc_API_KEY") == "sk-super-secret-value"


def test_credentials_never_fall_back_to_plaintext(fresh_config, monkeypatch):
    """The silent-fallback era is over.

    A warned plaintext write is not a control - the credential still lands on
    disk in the clear. Off Windows (no OS keychain to auto-provision a store),
    the write must REFUSE with instructions instead.
    """
    monkeypatch.setattr(os, "name", "posix")
    cfg, env = fresh_config(with_store=False)
    with pytest.raises(RuntimeError, match="MCO_MASTER_PASSWORD"):
        cfg.set("LLM_CONN_abc_API_KEY", "sk-plain")
    assert "sk-plain" not in env.read_text(encoding="utf-8") if env.exists() else True


def test_windows_auto_provisions_a_store_key_first(fresh_config, monkeypatch):
    """On Windows the store is created on demand - key persisted BEFORE the
    store exists, so an interrupt can never orphan it."""
    import mco.config as config_mod

    monkeypatch.setattr(os, "name", "nt")
    calls = []

    class _FakeCredMan:
        @classmethod
        def store_key(cls, key):
            calls.append(("persist", key))

    monkeypatch.setattr(security_mod, "WindowsCredentialProvider", _FakeCredMan)
    cfg, env = fresh_config(with_store=False)

    real_init = cfg._store.initialize
    def _tracked_init(key, **kw):
        calls.append(("initialize", key))
        return real_init(key, **kw)
    monkeypatch.setattr(cfg._store, "initialize", _tracked_init)

    cfg.set("LLM_CONN_auto_API_KEY", "sk-auto")

    assert [c[0] for c in calls] == ["persist", "initialize"], "key must be persisted before the store exists"
    assert calls[0][1] == calls[1][1], "the persisted key and the store key must match"
    assert "sk-auto" not in env.read_text(encoding="utf-8")
    assert cfg._store.get("LLM_CONN_auto_API_KEY") == "sk-auto"


def test_locked_existing_store_refuses_rather_than_stacking(fresh_config, monkeypatch):
    """An orphaned store must not be silently replaced - that loses data."""
    cfg, env = fresh_config(with_store=True)
    cfg._store.lock() if hasattr(cfg._store, "lock") else None
    if cfg._store.is_unlocked:
        # simulate the orphaned state: initialized on disk, no key in memory
        monkeypatch.setattr(type(cfg._store), "is_unlocked", property(lambda self: False))
    with pytest.raises(RuntimeError, match="locked"):
        cfg.set("SERVICENOW_PASSWORD", "hunter2")


def test_explicit_plaintext_opt_out_still_available(fresh_config):
    """encrypt=False remains a deliberate, visible escape hatch."""
    cfg, env = fresh_config(with_store=False)
    cfg.set("MCO_WEBHOOK_SECRET", "shhh", encrypt=False)
    assert "shhh" in env.read_text(encoding="utf-8")


def test_non_secret_values_stay_readable_in_env(fresh_config):
    cfg, env = fresh_config(with_store=True)
    cfg.set("OPERATOR_NAME", "joe")
    assert "OPERATOR_NAME=joe" in env.read_text(encoding="utf-8")


def test_encrypt_false_still_forces_plaintext(fresh_config):
    """Explicit opt-out remains available for callers that need it."""
    cfg, env = fresh_config(with_store=True)
    cfg.set("MCO_WEBHOOK_SECRET", "shhh", encrypt=False)
    assert "shhh" in env.read_text(encoding="utf-8")
def test_dynamic_model_connection_key_is_sensitive_and_resolves_immediately(tmp_path):
    env_file = tmp_path / ".env"
    store_file = tmp_path / "secrets.enc"
    manager = ConfigManager(env_path=env_file, store_path=store_file)
    # SecretStore is process-global in production; isolate this test from
    # earlier ConfigManager instances that intentionally exercise that singleton.
    manager._store = SecretStore(store_path=store_file)
    manager._store.initialize(b"D" * 32)
    key = "LLM_CONN_123_API_KEY"

    assert is_sensitive_key(key)
    manager.set(key, "sk-dynamic-secret", encrypt=True)

    assert manager.get(key) == "sk-dynamic-secret"
    assert "sk-dynamic-secret" not in env_file.read_text(encoding="utf-8")
    assert manager.get_masked_config()[key] != "sk-dynamic-secret"


def test_config_rejects_newline_injection(tmp_path):
    manager = ConfigManager(
        env_path=tmp_path / ".env",
        store_path=tmp_path / "secrets.enc",
    )

    with pytest.raises(ValueError, match="newlines"):
        manager.set("MCO_ORGS", "acme\nMCO_VAULT_MASTER_KEY=attacker")

