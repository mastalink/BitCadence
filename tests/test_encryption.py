"""Unit tests for AES-256-GCM secret store."""

import tempfile
import base64
import json
from pathlib import Path
import pytest
from mco.security import SecretStore, PasswordKeyProvider, get_secret_store


def test_derive_key():
    """Verify that key derivation produces a reliable 32-byte key."""
    password = "SuperSecurePassword123"
    salt = b"0" * 32
    key1 = SecretStore.derive_key(password, salt, iterations=1000)
    key2 = SecretStore.derive_key(password, salt, iterations=1000)
    assert len(key1) == 32
    assert key1 == key2

    # Different iterations -> different key
    key3 = SecretStore.derive_key(password, salt, iterations=2000)
    assert key1 != key3


def test_secret_store_lifecycle():
    """Test standard initialize, set, get, lock, unlock flow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = Path(tmpdir) / "secrets.enc"
        store = SecretStore(store_path=store_path)
        
        # 1. State queries on uninitialized
        assert not store.is_initialized()
        assert not store.is_unlocked
        
        # 2. Initialize
        master_key = b"A" * 32
        store.initialize(master_key)
        assert store.is_initialized()
        assert store.is_unlocked
        
        # 3. Insert and retrieve key
        store.set("GEMINI_API_KEY", "gemini_secret_123")
        assert store.get("GEMINI_API_KEY") == "gemini_secret_123"
        
        # 4. Lock
        store.lock()
        assert not store.is_unlocked
        with pytest.raises(RuntimeError):
            store.get("GEMINI_API_KEY")
            
        # 5. Unlock with WRONG key
        wrong_key = b"B" * 32
        success = store.unlock(wrong_key)
        assert not success
        assert not store.is_unlocked
        
        # 6. Unlock with CORRECT key
        success = store.unlock(master_key)
        assert success
        assert store.is_unlocked
        assert store.get("GEMINI_API_KEY") == "gemini_secret_123"
        
        # 7. Check file envelope structure
        raw_content = store_path.read_text(encoding="utf-8")
        envelope = json.loads(raw_content)
        assert envelope["version"] == 1
        assert "salt" in envelope
        assert "nonce" in envelope
        assert "tag" in envelope
        assert "ciphertext" in envelope


def test_windows_credential_provider_mock(monkeypatch):
    """Verify WindowsCredentialProvider behavior and mock integration."""
    from mco.security import WindowsCredentialProvider

    # Mock the win32cred library
    mock_creds = {}

    class MockWin32Cred:
        CRED_TYPE_GENERIC = 1
        CRED_PERSIST_LOCAL_MACHINE = 2

        @staticmethod
        def CredWrite(credential, flags):
            mock_creds[credential["TargetName"]] = credential["CredentialBlob"]

        @staticmethod
        def CredRead(target_name, type_):
            if target_name in mock_creds:
                return {
                    "CredentialBlob": mock_creds[target_name]
                }
            return None

    monkeypatch.setattr("sys.platform", "win32")
    import sys
    sys.modules["win32cred"] = MockWin32Cred  # type: ignore

    # Store key
    test_key = b"C" * 32
    WindowsCredentialProvider.store_key(test_key)
    
    # Read key
    provider = WindowsCredentialProvider()
    retrieved_key = provider.get_key()
    assert retrieved_key == test_key


def test_atomic_persist_failure_preserves_previous_envelope(monkeypatch, tmp_path):
    store_path = tmp_path / "secrets.enc"
    store = SecretStore(store_path=store_path)
    master_key = b"E" * 32
    store.initialize(master_key)
    store.set("FIRST", "kept")
    previous = store_path.read_bytes()

    monkeypatch.setattr("mco.security.os.replace", lambda source, target: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        store.set("SECOND", "must-not-land")

    assert store_path.read_bytes() == previous
    assert store.get("FIRST") == "kept"
    assert store.get("SECOND") is None


# ── the guard itself: tests must never touch the operator's real store ────────

def test_default_store_path_is_redirected_away_from_home():
    """A default-constructed store must NOT be ~/.mco/secrets.enc during tests.

    This is the file-path half of the isolation that a Credential-Manager-only
    guard missed - and the reason a full suite run kept orphaning the real
    store even after the first guard landed.
    """
    from pathlib import Path
    import mco.security as security_mod

    real = Path.home() / ".mco" / "secrets.enc"
    assert security_mod.DEFAULT_STORE_PATH != real
    assert security_mod.SecretStore()._path != real
    assert security_mod.get_secret_store()._path != real


def test_initializing_a_default_store_cannot_reach_the_real_file(tmp_path):
    """Even initialize() on a default store stays in the temp sandbox."""
    from pathlib import Path
    import mco.security as security_mod

    store = security_mod.get_secret_store()
    store.initialize(b"Z" * 32)
    assert store._path != Path.home() / ".mco" / "secrets.enc"
    assert store.is_initialized()
