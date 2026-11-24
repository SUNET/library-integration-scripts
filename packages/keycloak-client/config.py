from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

KeycloakAdminLoginMethod = Literal["cli", "user"]


class Settings(BaseSettings):
    """Runtime configuration, validated once at startup."""

    # Precedence -> [Settings(...) keyword arguments, os.environ, .env file, field defaults]
    model_config = SettingsConfigDict(
        env_prefix="FOLIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,  # Make Settings(libris_sigel="Hal") = Settings(ILL_LIBRIS_SIGEL="Hal") = Settings(HALMSTAD_LIBRIS_USERNAME="Hal")
        extra="forbid",  # Forbid unknown fields in Settings(...) constructor extra = "forbid" | "allow" | "ignore"
    )

    # --- Keycloak (admin provisioning) -------------------------------
    # Aliases accept the bare KEYCLOAK_* names used by the ops scripts as
    # well as the ILL_-prefixed form.
    keycloak_url: str = Field(
        default="http://localhost:8081",
        validation_alias=AliasChoices("ILL_KEYCLOAK_URL", "KEYCLOAK_URL"),
    )
    keycloak_realm: str = Field(
        default="ill-bridge",
        validation_alias=AliasChoices("ILL_KEYCLOAK_REALM", "KEYCLOAK_REALM"),
    )
    keycloak_admin_realm: str = Field(
        default="ill-bridge",
        validation_alias=AliasChoices(
            "ILL_KEYCLOAK_ADMIN_REALM", "KEYCLOAK_ADMIN_REALM"
        ),
    )
    keycloak_admin_login_method: KeycloakAdminLoginMethod = Field(
        default="user",
        validation_alias=AliasChoices(
            "ILL_KEYCLOAK_ADMIN_LOGIN_METHOD",
            "KEYCLOAK_ADMIN_LOGIN_METHOD",
            "keycloak_admin_login_method",
        ),
    )
    keycloak_admin_client_id: str | None = Field(
        default="ill-bridge-provisioner",
        validation_alias=AliasChoices(
            "ILL_KEYCLOAK_ADMIN_CLIENT_ID", "KEYCLOAK_ADMIN_CLIENT_ID"
        ),
    )
    # SecretStr keeps the client secret out of repr(), tracebacks and logs.
    keycloak_admin_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ILL_KEYCLOAK_ADMIN_CLIENT_SECRET", "KEYCLOAK_ADMIN_CLIENT_SECRET"
        ),
    )
    keycloak_api_audience: str = Field(
        default="ill-bridge-api",
        validation_alias=AliasChoices(
            "ILL_KEYCLOAK_API_AUDIENCE", "KEYCLOAK_API_AUDIENCE"
        ),
    )
    keycloak_timeout: float = Field(
        default=30.0,
        validation_alias=AliasChoices("ILL_KEYCLOAK_TIMEOUT", "KEYCLOAK_TIMEOUT"),
    )
    keycloak_verify_ssl: bool = Field(
        default=False,
        validation_alias=AliasChoices("ILL_KEYCLOAK_VERIFY_SSL", "KEYCLOAK_VERIFY_SSL"),
    )

    # --- Environment -----------------------------------------------------
    log_level: str = "INFO"


@lru_cache
def get_settings(*, strict_env: bool = True) -> Settings:
    """Return the validated settings, loaded once per process.
    If strict_env is True, raise an exception if any ILL_* env vars are unrecognised.
    """
    return Settings()


if __name__ == "__main__":
    settings = get_settings(strict_env=False)
    print(settings.model_dump_json(indent=2, exclude_none=True))
