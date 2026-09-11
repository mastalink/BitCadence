"""Tenant-aware local and shared secret-vault Adapters."""

import base64

import pytest

from mco.config import ConfigManager
from mco.localstore import LocalStore
from mco.security import SecretStore
from mco.secret_vault import (
    LocalEncryptedVault,
    SecretNotFoundError,
    SecretRef,
    SharedDatabaseVault,
    VaultUnavailableError,
    build_secret_vault,
    decode_master_key,
)


class MemoryConfig:
    def __init__(self, **values):
        self.values = dict(values)
        self.calls = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, encrypt=False):
        self.calls.append((key, value, encrypt))
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def ref(org="acme", scope="conn-1"):
    return SecretRef(
        org_id=org,
        scope=scope,
        name="api_key",
        legacy_config_key=f"LLM_CONN_{scope}_API_KEY",
    )


def test_local_adapter_never_requests_plaintext_write():
    cfg = MemoryConfig()
    vault = LocalEncryptedVault(cfg)

    vault.put(ref(), "sk-local")

    assert cfg.calls == [("LLM_CONN_conn-1_API_KEY", "sk-local", True)]
    assert vault.get(ref()) == "sk-local"


def test_local_adapter_reports_locked_sentinel():
    cfg = MemoryConfig(LLM_CONN_conn_1_API_KEY="encrypted_in_secret_store")
    secret_ref = SecretRef(
        org_id="acme",
        scope="conn-1",
        name="api_key",
        legacy_config_key="LLM_CONN_conn_1_API_KEY",
    )

    with pytest.raises(VaultUnavailableError, match="locked"):
        LocalEncryptedVault(cfg).get(secret_ref)


def test_local_adapter_migrates_legacy_plaintext_only_after_encrypted_write(tmp_path):
    env_file = tmp_path / ".env"
    store_file = tmp_path / "secrets.enc"
    secret_ref = ref()
    env_file.write_text(f"{secret_ref.local_key}=legacy-plaintext\n", encoding="utf-8")
    config = ConfigManager(env_path=env_file, store_path=store_file)
    config._store = SecretStore(store_path=store_file)
    config._store.initialize(b"L" * 32)
    vault = LocalEncryptedVault(config)

    assert vault.get(secret_ref) == "legacy-plaintext"
    vault.put(secret_ref, "legacy-plaintext")

    env_text = env_file.read_text(encoding="utf-8")
    assert "legacy-plaintext" not in env_text
    assert f"{secret_ref.local_key}=encrypted_in_secret_store" in env_text
    assert config._store.get(secret_ref.local_key) == "legacy-plaintext"


def test_failed_legacy_migration_preserves_plaintext(tmp_path, monkeypatch):
    """When the vault genuinely cannot be provisioned, plaintext must survive.

    On Windows, config.set() now auto-provisions the store on first credential
    write (key persisted to Credential Manager before the store exists), so an
    uninitialized store no longer means "unavailable" there - the vault simply
    becomes available. The unavailable case is real on platforms with no OS
    keychain, so this test pins that path explicitly.
    """
    import os as _os
    monkeypatch.setattr(_os, "name", "posix")

    env_file = tmp_path / ".env"
    store_file = tmp_path / "secrets.enc"
    secret_ref = ref()
    env_file.write_text(f"{secret_ref.local_key}=keep-me\n", encoding="utf-8")
    config = ConfigManager(env_path=env_file, store_path=store_file)
    config._store = SecretStore(store_path=store_file)  # locked and uninitialized

    with pytest.raises(VaultUnavailableError):
        LocalEncryptedVault(config).put(secret_ref, "keep-me")

    assert f"{secret_ref.local_key}=keep-me" in env_file.read_text(encoding="utf-8")


def test_vault_auto_provisions_on_windows(tmp_path, monkeypatch):
    """The flip side: on Windows the vault becomes available instead of failing."""
    import os as _os
    monkeypatch.setattr(_os, "name", "nt")

    env_file = tmp_path / ".env"
    store_file = tmp_path / "secrets.enc"
    secret_ref = ref()
    config = ConfigManager(env_path=env_file, store_path=store_file)
    config._store = SecretStore(store_path=store_file)  # uninitialized

    LocalEncryptedVault(config).put(secret_ref, "sk-provisioned")

    assert "sk-provisioned" not in env_file.read_text(encoding="utf-8")
    assert config._store.get(secret_ref.local_key) == "sk-provisioned"


def test_shared_adapter_round_trip_never_stores_plaintext(tmp_path):
    db = LocalStore(tmp_path / "vault.db")
    vault = SharedDatabaseVault(db, b"V" * 32)
    secret_ref = ref()

    vault.put(secret_ref, "sk-database-secret")
    rows = db.table("secret_records").select("*").execute().data

    assert len(rows) == 1
    assert "sk-database-secret" not in str(rows)
    assert db.table("organizations").select("*").eq("id", "acme").execute().data
    assert vault.exists(secret_ref)
    assert vault.get(secret_ref) == "sk-database-secret"
    db.close()


def test_shared_adapter_isolates_tenants_and_wrong_keys(tmp_path):
    db = LocalStore(tmp_path / "vault.db")
    acme = SharedDatabaseVault(db, b"A" * 32)
    globex = SharedDatabaseVault(db, b"G" * 32)
    acme.put(ref("acme"), "acme-secret")
    globex.put(ref("globex"), "globex-secret")

    assert acme.get(ref("acme")) == "acme-secret"
    assert globex.get(ref("globex")) == "globex-secret"
    with pytest.raises(VaultUnavailableError, match="decrypted"):
        SharedDatabaseVault(db, b"X" * 32).get(ref("acme"))
    db.close()


def test_shared_adapter_delete_is_tenant_scoped(tmp_path):
    db = LocalStore(tmp_path / "vault.db")
    vault = SharedDatabaseVault(db, b"V" * 32)
    vault.put(ref("acme"), "a")
    vault.put(ref("globex"), "g")

    vault.delete(ref("acme"))

    with pytest.raises(SecretNotFoundError):
        vault.get(ref("acme"))
    assert vault.get(ref("globex")) == "g"
    db.close()


def test_factory_selects_shared_adapter_and_validates_key(tmp_path):
    db = LocalStore(tmp_path / "vault.db")
    encoded = base64.urlsafe_b64encode(b"F" * 32).decode()
    cfg = MemoryConfig(
        MCO_SECRET_VAULT_BACKEND="database",
        MCO_VAULT_MASTER_KEY=encoded,
    )

    assert isinstance(build_secret_vault(cfg, db), SharedDatabaseVault)
    assert decode_master_key(encoded) == b"F" * 32
    with pytest.raises(VaultUnavailableError):
        decode_master_key("too-short")
    db.close()
