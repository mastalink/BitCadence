"""Secret custody behind one small, tenant-aware interface.

Clients identify a secret by organization, scope, and name. They never need
to know whether the value lives in the local encrypted store or in the shared
database as AES-256-GCM ciphertext. That seam is what lets a self-hosted
instance remain local while a saved hosted instance serves the same secret to
authorized sessions on any device without returning the raw value to them.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(RuntimeError):
    """Base class for secret-custody failures safe to surface to operators."""


class VaultUnavailableError(VaultError):
    """The configured vault cannot currently read or write secrets."""


class SecretNotFoundError(VaultError):
    """The requested secret does not exist."""


@dataclass(frozen=True)
class SecretRef:
    """Tenant-scoped identity for one secret.

    ``scope`` is the owning record (for example an LLM connection id).
    ``legacy_config_key`` preserves compatibility with existing local
    connection keys while all new callers use the tenant-aware identity.
    """

    org_id: str
    scope: str
    name: str
    legacy_config_key: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("org_id", "scope", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            if "\0" in value:
                raise ValueError(f"{field_name} may not contain NUL characters")

    @property
    def local_key(self) -> str:
        if self.legacy_config_key:
            return self.legacy_config_key
        return f"MCO_SECRET_{self.record_id.replace('-', '_').upper()}"

    @property
    def aad(self) -> bytes:
        # FROZEN WIRE CONSTANT - do not rebrand. This string is the AES-GCM
        # Additional Authenticated Data: it is authenticated together with the
        # ciphertext, so changing a single byte makes every secret written by
        # an earlier version fail to decrypt (InvalidTag) with no way back.
        # The ":v1" is the version handle to use if this ever must change -
        # bump it deliberately alongside a re-encryption migration.
        return json.dumps(
            ["batoncadence:v1", self.org_id, self.scope, self.name],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def record_id(self) -> str:
        material = json.dumps(
            [self.org_id, self.scope, self.name],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # FROZEN WIRE CONSTANT - do not rebrand. This namespace determines the
        # deterministic id a secret is stored under (see local_key above);
        # changing it repoints every lookup at a key that was never written.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"batoncadence:secret:{material}"))


class SecretVault(ABC):
    """Deep interface for storing tenant-scoped secret values."""

    @abstractmethod
    def put(self, ref: SecretRef, value: str) -> None:
        ...

    @abstractmethod
    def get(self, ref: SecretRef) -> str:
        ...

    @abstractmethod
    def delete(self, ref: SecretRef) -> None:
        ...

    @abstractmethod
    def exists(self, ref: SecretRef) -> bool:
        ...


class LocalEncryptedVault(SecretVault):
    """Adapter over ConfigManager's AES-256-GCM local secret store."""

    def __init__(self, config: Any):
        self._config = config

    def put(self, ref: SecretRef, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        try:
            self._config.set(ref.local_key, value, encrypt=True)
        except Exception as exc:
            raise VaultUnavailableError(
                "The local encrypted secret store is locked. Unlock it in "
                "`mco setup --menu` before saving credentials."
            ) from exc

    def get(self, ref: SecretRef) -> str:
        value = self._config.get(ref.local_key)
        if value == "encrypted_in_secret_store":
            raise VaultUnavailableError(
                "The local encrypted secret store is locked. Unlock it before using credentials."
            )
        if not value:
            raise SecretNotFoundError(f"Secret '{ref.name}' is not configured")
        return str(value)

    def delete(self, ref: SecretRef) -> None:
        if self._config.get(ref.local_key) == "encrypted_in_secret_store":
            raise VaultUnavailableError(
                "The local encrypted secret store is locked. Unlock it before deleting credentials."
            )
        try:
            self._config.delete(ref.local_key)
        except Exception as exc:
            raise VaultUnavailableError(
                "The local encrypted secret store is locked. Unlock it before deleting credentials."
            ) from exc

    def exists(self, ref: SecretRef) -> bool:
        # The sentinel intentionally counts as configured: list screens may
        # report key_set without needing to decrypt or expose the value.
        return bool(self._config.get(ref.local_key))


def decode_master_key(value: str) -> bytes:
    """Decode a 256-bit vault key from URL-safe/base64 or 64-character hex."""

    raw = str(value or "").strip()
    if not raw:
        raise VaultUnavailableError(
            "MCO_VAULT_MASTER_KEY is required for the shared database vault"
        )
    try:
        if len(raw) == 64:
            key = bytes.fromhex(raw)
        else:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, binascii.Error) as exc:
        raise VaultUnavailableError(
            "MCO_VAULT_MASTER_KEY must be a base64 or 64-character hex 256-bit key"
        ) from exc
    if len(key) != 32:
        raise VaultUnavailableError(
            f"MCO_VAULT_MASTER_KEY decoded to {len(key)} bytes; expected 32"
        )
    return key


class SharedDatabaseVault(SecretVault):
    """Adapter storing ciphertext in ``secret_records``.

    The encryption key is supplied by the gateway environment (and can later
    be injected by a cloud KMS Adapter). Associated data binds ciphertext to
    its tenant and owning record, so moving a blob between rows fails closed.
    """

    def __init__(self, db: Any, master_key: bytes, key_version: int = 1):
        if db is None:
            raise VaultUnavailableError("The shared database vault requires a database")
        if len(master_key) != 32:
            raise ValueError("master_key must be 32 bytes")
        self._db = db
        self._aes = AESGCM(master_key)
        self._key_version = int(key_version)

    def _row(self, ref: SecretRef) -> Optional[dict]:
        try:
            result = (
                self._db.table("secret_records")
                .select("*")
                .eq("id", ref.record_id)
                .eq("org_id", ref.org_id)
                .execute()
            )
        except Exception as exc:
            raise VaultUnavailableError("The shared vault could not read its database") from exc
        rows = result.data or []
        return rows[0] if rows else None

    def _ensure_org(self, ref: SecretRef) -> None:
        try:
            existing = (
                self._db.table("organizations")
                .select("id")
                .eq("id", ref.org_id)
                .execute()
            )
            if existing.data:
                return
            result = self._db.table("organizations").insert({
                "id": ref.org_id,
                "name": ref.org_id,
            }).execute()
        except Exception as exc:
            # A concurrent first write may have inserted the same organization.
            try:
                raced = (
                    self._db.table("organizations")
                    .select("id")
                    .eq("id", ref.org_id)
                    .execute()
                )
                if raced.data:
                    return
            except Exception:
                pass
            raise VaultUnavailableError(
                "The shared vault could not establish the organization record"
            ) from exc
        if not result.data:
            raise VaultUnavailableError(
                "The shared vault could not establish the organization record"
            )

    def put(self, ref: SecretRef, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, value.encode("utf-8"), ref.aad)
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": ref.record_id,
            "org_id": ref.org_id,
            "scope": ref.scope,
            "name": ref.name,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "key_version": self._key_version,
            "updated_at": now,
        }
        self._ensure_org(ref)
        try:
            # The deterministic tenant-scoped id makes rotation one atomic
            # upsert instead of a select-then-write race.
            result = self._db.table("secret_records").upsert(row).execute()
        except Exception as exc:
            raise VaultUnavailableError("The shared vault could not persist the secret") from exc
        if not result.data:
            raise VaultUnavailableError("The shared vault failed to persist the secret")

    def get(self, ref: SecretRef) -> str:
        row = self._row(ref)
        if row is None:
            raise SecretNotFoundError(f"Secret '{ref.name}' is not configured")
        try:
            nonce = base64.b64decode(row["nonce"], validate=True)
            ciphertext = base64.b64decode(row["ciphertext"], validate=True)
            plaintext = self._aes.decrypt(nonce, ciphertext, ref.aad)
            return plaintext.decode("utf-8")
        except (KeyError, ValueError, binascii.Error, InvalidTag, UnicodeDecodeError) as exc:
            raise VaultUnavailableError(
                "The shared vault record could not be authenticated or decrypted"
            ) from exc

    def delete(self, ref: SecretRef) -> None:
        try:
            (
                self._db.table("secret_records")
                .delete()
                .eq("id", ref.record_id)
                .eq("org_id", ref.org_id)
                .execute()
            )
        except Exception as exc:
            raise VaultUnavailableError("The shared vault could not delete the secret") from exc

    def exists(self, ref: SecretRef) -> bool:
        return self._row(ref) is not None


def build_secret_vault(config: Any, db: Any = None) -> SecretVault:
    """Select the configured production Adapter.

    ``local`` is the compatibility default. ``database`` enables saved hosted
    instances: all gateway replicas share encrypted records while the master
    key remains outside the database.
    """

    backend = str(config.get("MCO_SECRET_VAULT_BACKEND", "local") or "local").strip().lower()
    if backend == "local":
        return LocalEncryptedVault(config)
    if backend == "database":
        key = decode_master_key(config.get("MCO_VAULT_MASTER_KEY") or "")
        try:
            version = int(config.get("MCO_VAULT_KEY_VERSION", 1) or 1)
        except (TypeError, ValueError) as exc:
            raise VaultUnavailableError("MCO_VAULT_KEY_VERSION must be an integer") from exc
        return SharedDatabaseVault(db, key, version)
    raise VaultUnavailableError(
        f"Unknown MCO_SECRET_VAULT_BACKEND '{backend}'; expected local or database"
    )
