from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FolioTenantCredentials:
    """Resolved FOLIO authentication settings for a single library tenant."""

    library_tenant: str
    folio_tenant: str
    folio_realm: str
    username: str
    secret: str
    environment: str = "test"

    def __repr__(self) -> str:
        return (
            f"FolioTenantCredentials(library_tenant={self.library_tenant!r}, "
            f"folio_tenant={self.folio_tenant!r}, folio_realm={self.folio_realm!r}, "
            f"username={self.username!r}, secret='***', environment={self.environment!r})"
        )


@runtime_checkable
class CredentialProvider(Protocol):
    """Pluggable interface for resolving FOLIO credentials for a tenant."""

    def get_credentials(
        self, tenant_key: str | None = None
    ) -> FolioTenantCredentials: ...


class Settings(BaseSettings):
    """Runtime configuration, validated once at startup."""

    # Precedence -> [Settings(...) keyword arguments, os.environ, .env file, field defaults]
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    # --- Environment -----------------------------------------------------
    environment: str = "local"
    log_level: str = "INFO"

    # --- Default FOLIO credentials ---------------------------------------
    tenant_name: str | None = None
    folio_tenants: str | None = None
    folio_tenant: str | None = None
    folio_tenant_realm: str | None = None
    folio_tenant_username: str | None = None
    folio_tenant_secret: SecretStr | None = None

    def get_folio_credentials(
        self, tenant_key: str | None = None
    ) -> FolioTenantCredentials:
        """Resolve FOLIO credentials for a tenant via EnvCredentialProvider."""
        return EnvCredentialProvider(self).get_credentials(tenant_key)


class EnvCredentialProvider:
    """Resolves per-tenant FOLIO credentials from environment variables.

    For tenant key ``<KEY>`` (e.g. ``BTH``, ``HH``, ``MDU``), checks:
      - ``FOLIO_TENANT_<KEY>``       -> fallback ``FOLIO_TENANT``
      - ``FOLIO_REALM_<KEY>``        -> fallback ``FOLIO_TENANT_REALM``
      - ``FOLIO_USERNAME_<KEY>``     -> fallback ``FOLIO_TENANT_USERNAME``
      - ``FOLIO_SECRET_<KEY>``       -> fallback ``FOLIO_TENANT_SECRET``
      - ``FOLIO_ENVIRONMENT_<KEY>``  -> fallback ``ENVIRONMENT``
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings(strict_env=False)

    def get_credentials(
        self, tenant_key: str | None = None
    ) -> FolioTenantCredentials:
        raw_tenant = tenant_key or self.settings.tenant_name
        if not raw_tenant or not raw_tenant.strip():
            raise ValueError(
                "No tenant specified: pass tenant_key or set TENANT_NAME"
            )

        library_tenant = raw_tenant.strip().lower()
        suffix = library_tenant.upper()

        default_secret = (
            self.settings.folio_tenant_secret.get_secret_value()
            if self.settings.folio_tenant_secret is not None
            else None
        )

        folio_tenant = (
            os.getenv(f"FOLIO_TENANT_{suffix}") or self.settings.folio_tenant
        )
        folio_realm = (
            os.getenv(f"FOLIO_REALM_{suffix}") or self.settings.folio_tenant_realm
        )
        username = (
            os.getenv(f"FOLIO_USERNAME_{suffix}")
            or self.settings.folio_tenant_username
        )
        secret = os.getenv(f"FOLIO_SECRET_{suffix}") or default_secret
        raw_env = (
            os.getenv(f"FOLIO_ENVIRONMENT_{suffix}") or self.settings.environment
        )
        environment = "test" if raw_env == "local" else raw_env

        missing = [
            name
            for name, val in (
                (f"FOLIO_TENANT_{suffix} / FOLIO_TENANT", folio_tenant),
                (f"FOLIO_REALM_{suffix} / FOLIO_TENANT_REALM", folio_realm),
                (f"FOLIO_USERNAME_{suffix} / FOLIO_TENANT_USERNAME", username),
                (f"FOLIO_SECRET_{suffix} / FOLIO_TENANT_SECRET", secret),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"Missing FOLIO credentials for tenant '{library_tenant}': "
                + ", ".join(missing)
            )

        return FolioTenantCredentials(
            library_tenant=library_tenant,
            folio_tenant=folio_tenant,
            folio_realm=folio_realm,
            username=username,
            secret=secret,
            environment=environment,
        )


class MultiTenantCredentialProvider:
    """Resolves credentials for one or all configured tenants."""

    def __init__(
        self,
        settings: Settings | None = None,
        configured_tenants: Iterable[str] | None = None,
        provider: CredentialProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings(strict_env=False)
        self._inner: CredentialProvider = provider or EnvCredentialProvider(
            self.settings
        )
        if configured_tenants is not None:
            self.configured_tenants = [
                t.strip().lower() for t in configured_tenants if t.strip()
            ]
        elif self.settings.folio_tenants:
            self.configured_tenants = [
                t.strip().lower()
                for t in self.settings.folio_tenants.split(",")
                if t.strip()
            ]
        else:
            self.configured_tenants = []

    def list_tenants(self) -> list[str]:
        if self.configured_tenants:
            return list(self.configured_tenants)
        if self.settings.tenant_name and self.settings.tenant_name.strip():
            return [self.settings.tenant_name.strip().lower()]
        return []

    def get_credentials(
        self, tenant_key: str | None = None
    ) -> FolioTenantCredentials:
        return self._inner.get_credentials(tenant_key)

    def get_all_credentials(
        self, *, skip_invalid: bool = False
    ) -> dict[str, FolioTenantCredentials]:
        resolved: dict[str, FolioTenantCredentials] = {}
        for tenant in self.list_tenants():
            try:
                resolved[tenant] = self.get_credentials(tenant)
            except ValueError as exc:
                if not skip_invalid:
                    raise
                logger.warning("Skipping tenant '%s': %s", tenant, exc)
        return resolved


@lru_cache
def get_settings(*, strict_env: bool = True) -> Settings:
    """Return the validated settings, loaded once per process."""
    return Settings()


if __name__ == "__main__":
    settings = get_settings(strict_env=False)
    print(settings.model_dump_json(indent=2, exclude_none=True))
