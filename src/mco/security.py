"""
BitCadence Encrypted Secret Store
==================================
AES-256-GCM encryption for sensitive configuration values.

Storage format: JSON envelope at ~/.mco/secrets.enc
{
    "version": 1,
    "kdf": "pbkdf2-hmac-sha256",
    "iterations": 600000,
    "salt": "<base64>",
    "nonce": "<base64>",
    "tag": "<base64>",
    "ciphertext": "<base64>"
}
"""

from __future__ import annotations

import base64
import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from loguru import logger

# Constants
_STORE_VERSION = 1
_DEFAULT_ITERATIONS = 600_000
_SALT_LENGTH = 32
_NONCE_LENGTH = 12  # 96-bit nonce for AES-GCM
_KEY_LENGTH = 32  # 256-bit key

DEFAULT_STORE_PATH = Path.home() / ".mco" / "secrets.enc"


class KeyProvider(ABC):
    """Abstract interface for master key providers."""

    @abstractmethod
    def get_key(self) -> bytes:
        """Return the raw master key (32 bytes) or a password-derived key."""
        ...


class PasswordKeyProvider(KeyProvider):
    """Derives a master key from a user-supplied password."""

    def __init__(self, password: str, salt: Optional[bytes] = None,
                 iterations: int = _DEFAULT_ITERATIONS):
        self._password = password
        self._salt = salt or os.urandom(_SALT_LENGTH)
        self._iterations = iterations

    @property
    def salt(self) -> bytes:
        return self._salt

    @property
    def iterations(self) -> int:
        return self._iterations

    def get_key(self) -> bytes:
        return SecretStore.derive_key(self._password, self._salt, self._iterations)


class WindowsCredentialProvider(KeyProvider):
    """Reads the master key from Windows Credential Manager.

    Stores/retrieves a 32-byte key under the target name 'MCO_SECRET_STORE'.
    Requires the ``pywin32`` package on Windows.
    """

    TARGET_NAME = "MCO_SECRET_STORE"

    def get_key(self) -> bytes:
        try:
            import win32cred  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "WindowsCredentialProvider requires pywin32. "
                "Install with: pip install pywin32"
            )

        cred = win32cred.CredRead(self.TARGET_NAME, win32cred.CRED_TYPE_GENERIC)
        if cred is None:
            raise RuntimeError(
                f"No credential found for target '{self.TARGET_NAME}' "
                "in Windows Credential Manager."
            )
        blob = cred["CredentialBlob"]
        # In mock tests, win32cred returns the unicode string directly. In real NT, it returns UTF-16 bytes.
        if isinstance(blob, str):
            blob = blob.encode("latin-1")
            
        # If it was written as Unicode (UTF-16), it will be 64 bytes. Let's decode it back.
        if len(blob) == 64:
            try:
                blob = blob.decode("utf-16").encode("latin-1")
            except Exception:
                pass

        if len(blob) != _KEY_LENGTH:
            raise ValueError(
                f"Stored credential blob is {len(blob)} bytes, expected {_KEY_LENGTH}."
            )
        return blob

    @classmethod
    def store_key(cls, key: bytes) -> None:
        """Store a 32-byte key in Windows Credential Manager."""
        try:
            import win32cred  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "WindowsCredentialProvider requires pywin32. "
                "Install with: pip install pywin32"
            )

        if len(key) != _KEY_LENGTH:
            raise ValueError(f"Key must be {_KEY_LENGTH} bytes, got {len(key)}.")

        # Decode raw bytes using latin-1 to create a valid Unicode string that pywin32 expects
        unicode_key = key.decode("latin-1")

        credential = {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": cls.TARGET_NAME,
            "CredentialBlob": unicode_key,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "UserName": "MCO",
        }
        win32cred.CredWrite(credential, 0)
        logger.info("Stored master key in Windows Credential Manager under target 'MCO_SECRET_STORE'")


class SecretStore:
    """AES-256-GCM encrypted secret store.

    Thread-safe. Secrets are held in memory only while unlocked.
    """

    def __init__(self, store_path: Optional[Path] = None):
        self._path = store_path or DEFAULT_STORE_PATH
        self._lock = threading.Lock()
        self._secrets: Optional[dict[str, str]] = None
        self._master_key: Optional[bytes] = None
        self._envelope: Optional[dict] = None

    def is_initialized(self) -> bool:
        """Check if the encrypted store file exists on disk."""
        return self._path.is_file()

    @property
    def is_unlocked(self) -> bool:
        """True when secrets are decrypted in memory."""
        return self._secrets is not None

    @classmethod
    def derive_key(cls, password: str, salt: bytes,
                   iterations: int = _DEFAULT_ITERATIONS) -> bytes:
        """Derive a 256-bit key from a password using PBKDF2-HMAC-SHA256."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LENGTH,
            salt=salt,
            iterations=iterations,
        )
        return kdf.derive(password.encode("utf-8"))

    def initialize(self, master_key: bytes, salt: Optional[bytes] = None) -> None:
        """Create a new, empty encrypted store with the given master key and optional stable salt.

        Raises FileExistsError if the store already exists.
        """
        with self._lock:
            if self._path.is_file():
                raise FileExistsError(
                    f"Secret store already exists at {self._path}. "
                    "Delete it first to re-initialize."
                )
            self._master_key = master_key
            self._secrets = {}
            if salt:
                self._envelope = {
                    "salt": base64.b64encode(salt).decode()
                }
            self._persist()
            logger.info("Initialized new secret store at {}", self._path)

    def unlock(self, master_key: bytes) -> bool:
        """Decrypt the store into memory.

        Returns True on success, False if decryption fails (wrong key).
        """
        with self._lock:
            if not self._path.is_file():
                logger.warning("Secret store not found at {}", self._path)
                return False

            try:
                raw = self._path.read_text(encoding="utf-8")
                envelope = json.loads(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to read secret store: {}", exc)
                return False

            if envelope.get("version") != _STORE_VERSION:
                logger.error("Unsupported store version: {}", envelope.get("version"))
                return False

            salt = base64.b64decode(envelope["salt"])
            nonce = base64.b64decode(envelope["nonce"])
            ciphertext = base64.b64decode(envelope["ciphertext"])
            tag = base64.b64decode(envelope["tag"])

            try:
                aesgcm = AESGCM(master_key)
                plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
            except Exception:
                # Debug, not warning: auto_unlock tries multiple key sources
                # and emits ONE actionable warning if none of them work.
                logger.debug("Key did not decrypt the secret store at {}", self._path)
                return False

            try:
                self._secrets = json.loads(plaintext.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.error("Decrypted blob is not valid JSON: {}", exc)
                return False

            self._master_key = master_key
            self._envelope = envelope
            logger.info("Secret store unlocked ({} keys)", len(self._secrets))
            return True

    def lock(self) -> None:
        """Clear decrypted secrets from memory."""
        with self._lock:
            self._secrets = None
            self._master_key = None
            self._envelope = None
            logger.info("Secret store locked")

    def auto_unlock(self) -> bool:
        """Attempt to automatically unlock the secret store.

        Tries:
        1. MCO_MASTER_PASSWORD environment variable.
        2. Windows Credential Manager (if on Windows and pywin32 is installed).

        Returns True if successfully unlocked, False otherwise.
        """
        if self.is_unlocked:
            return True

        if not self.is_initialized():
            return False

        # 1. Try env var
        password = os.environ.get("MCO_MASTER_PASSWORD")
        if password:
            try:
                raw = self._path.read_text(encoding="utf-8")
                envelope = json.loads(raw)
                salt = base64.b64decode(envelope["salt"])
                iterations = envelope.get("iterations", _DEFAULT_ITERATIONS)
                key = self.derive_key(password, salt, iterations)
                if self.unlock(key):
                    logger.info("Automatically unlocked secret store via MCO_MASTER_PASSWORD")
                    return True
            except Exception as e:
                logger.debug(f"Failed to auto-unlock via environment variable: {e}")

        # 2. Try Windows Credential Manager
        if os.name == "nt":
            try:
                provider = WindowsCredentialProvider()
                key = provider.get_key()
                if self.unlock(key):
                    logger.info("Automatically unlocked secret store via Windows Credential Manager")
                    return True
            except Exception as e:
                logger.debug(f"Failed to auto-unlock via Windows Credential Manager: {e}")

        # Every key source failed. Say so once, with the path and the remedy -
        # this is the message an operator actually sees at startup.
        logger.warning(
            "Secret store at {} exists but no available key unlocks it "
            "(tried MCO_MASTER_PASSWORD and Windows Credential Manager). "
            "Configuration still loads from .env. To fix: run 'mco setup --menu' "
            "-> security and unlock with your master password, or - if the store "
            "is from an interrupted setup and holds nothing - delete the file and "
            "re-enable encryption.",
            self._path,
        )
        return False

    def get(self, key: str) -> Optional[str]:
        """Get a secret value. Store must be unlocked."""
        with self._lock:
            if self._secrets is None:
                raise RuntimeError("Secret store is locked. Call unlock() or auto_unlock() first.")
            return self._secrets.get(key)

    def set(self, key: str, value: str) -> None:
        """Set a secret and persist to disk."""
        with self._lock:
            if self._secrets is None or self._master_key is None:
                raise RuntimeError("Secret store is locked. Call unlock() first.")
            previous = dict(self._secrets)
            self._secrets[key] = value
            try:
                self._persist()
            except Exception:
                self._secrets = previous
                raise
            logger.debug("Secret '{}' updated", key)

    def delete(self, key: str) -> None:
        """Remove a secret and persist to disk."""
        with self._lock:
            if self._secrets is None or self._master_key is None:
                raise RuntimeError("Secret store is locked. Call unlock() first.")
            previous = dict(self._secrets)
            self._secrets.pop(key, None)
            try:
                self._persist()
            except Exception:
                self._secrets = previous
                raise
            logger.debug("Secret '{}' deleted", key)

    def list_keys(self) -> list[str]:
        """List stored secret names. Store must be unlocked."""
        with self._lock:
            if self._secrets is None:
                raise RuntimeError("Secret store is locked. Call unlock() first.")
            return list(self._secrets.keys())

    def export_masked(self) -> dict:
        """Export all keys with masked values (first 2 chars + asterisks)."""
        with self._lock:
            if self._secrets is None:
                raise RuntimeError("Secret store is locked. Call unlock() first.")
            masked = {}
            for k, v in self._secrets.items():
                if len(v) <= 4:
                    masked[k] = "****"
                else:
                    masked[k] = v[:2] + "*" * (len(v) - 2)
            return masked

    def _persist(self) -> None:
        """Encrypt and write the secrets dict to disk.

        Must be called while holding self._lock.
        """
        assert self._secrets is not None
        assert self._master_key is not None

        plaintext = json.dumps(self._secrets, separators=(",", ":")).encode("utf-8")

        # Reuse existing salt to keep PBKDF2 derivation stable
        if self._envelope and "salt" in self._envelope:
            salt = base64.b64decode(self._envelope["salt"])
        else:
            salt = os.urandom(_SALT_LENGTH)
            
        nonce = os.urandom(_NONCE_LENGTH)

        aesgcm = AESGCM(self._master_key)
        ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)
        ciphertext = ct_with_tag[:-16]
        tag = ct_with_tag[-16:]

        envelope = {
            "version": _STORE_VERSION,
            "kdf": "pbkdf2-hmac-sha256",
            "iterations": _DEFAULT_ITERATIONS,
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "tag": base64.b64encode(tag).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(envelope, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
        self._envelope = envelope


_store: Optional[SecretStore] = None
_store_lock = threading.Lock()


def get_secret_store(store_path: Optional[Path] = None) -> SecretStore:
    """Get or create the global SecretStore singleton."""
    global _store
    with _store_lock:
        if _store is None:
            _store = SecretStore(store_path)
        return _store
