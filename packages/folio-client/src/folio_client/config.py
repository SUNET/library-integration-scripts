from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, validated once at startup."""

    # Precedence -> [Settings(...) keyword arguments, os.environ, .env file, field defaults]
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,  # Make Settings(libris_sigel="Hal") = Settings(ILL_LIBRIS_SIGEL="Hal") = Settings(HALMSTAD_LIBRIS_USERNAME="Hal")
        extra="forbid",  # Forbid unknown fields in Settings(...) constructor extra = "forbid" | "allow" | "ignore"
    )

    # --- Environment -----------------------------------------------------
    environment: str = "local"
    log_level: str = "INFO"

    # --- FOLIO credentials -----------------------------------------------
    tenant_name: str | None = None
    folio_tenant: str | None = None
    folio_tenant_realm: str | None = None
    folio_tenant_username: str | None = None
    folio_tenant_secret: SecretStr | None = None

    def get_folio_credentials(
        self, tenant_key: str | None = None
    ) -> tuple[str, str | None, str | None, str | None, str | None]:
        """Resolve (library_tenant, folio_tenant, folio_realm, username, secret) for a tenant."""
        library_tenant = (tenant_key or self.tenant_name or "None").strip().lower()
        default_secret = (
            self.folio_tenant_secret.get_secret_value()
            if self.folio_tenant_secret is not None
            else None
        )

        tenant_field = getattr(self, f"folio_tenant_{library_tenant}", None)
        realm_field = getattr(self, f"folio_realm_{library_tenant}", None)
        username_field = getattr(self, f"folio_username_{library_tenant}", None)
        secret_field = getattr(self, f"folio_secret_{library_tenant}", None)

        folio_tenant = tenant_field or self.folio_tenant
        folio_realm = realm_field or self.folio_tenant_realm
        username = username_field or self.folio_tenant_username
        secret = (
            secret_field.get_secret_value()
            if secret_field is not None
            else default_secret
        )
        return library_tenant, folio_tenant, folio_realm, username, secret


@lru_cache
def get_settings(*, strict_env: bool = True) -> Settings:
    """Return the validated settings, loaded once per process."""
    return Settings()


if __name__ == "__main__":
    settings = get_settings(strict_env=False)
    print(settings.model_dump_json(indent=2, exclude_none=True))
