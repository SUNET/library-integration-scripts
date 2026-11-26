from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any, Self

import httpx2
from .config import (
    CredentialProvider,
    EnvCredentialProvider,
    get_settings,
)
from .errors import AuthenticationError


class FolioAsyncApiClient:
    """Async FOLIO API client with cached Keycloak authentication."""

    def __init__(
        self,
        username: str,
        secret: str,
        auth_base_url: str = "https://auth.sunet-test.bibliotek.sunet.se",
        api_base_url: str = "https://api.sunet-test.bibliotek.sunet.se",
        realm: str = "diku",
        tenant: str = "diku",
        timeout: float | httpx2.Timeout = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        self.username = username
        self.secret = secret
        self.auth_base_url = auth_base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/") + "/"
        self.realm = realm
        self.tenant = tenant
        self._timeout = httpx2.Timeout(timeout)
        self._verify_ssl = verify_ssl
        self._session: httpx2.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_obtained_at = 0.0
        self._token_ttl = 250.0

    def _get_session(self) -> httpx2.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = httpx2.AsyncClient(
                base_url=self.api_base_url,
                verify=self._verify_ssl,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._session

    @property
    def session(self) -> httpx2.AsyncClient:
        return self._get_session()

    async def _get_access_token(self) -> str:
        """Return a cached access token, refreshing it when necessary."""
        if (
            self._access_token is not None
            and time.monotonic() - self._token_obtained_at < self._token_ttl
        ):
            return self._access_token

        try:
            response = await self.session.post(
                f"{self.auth_base_url}/realms/{self.realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.username,
                    "client_secret": self.secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            payload = response.json()
            access_token = payload["access_token"]
            if not isinstance(access_token, str):
                raise TypeError
        except httpx2.HTTPError as exc:
            raise AuthenticationError("Failed to obtain access token") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError(
                "Authentication response has no access token"
            ) from exc

        self._token_obtained_at = time.monotonic()
        self._token_ttl = max(float(payload.get("expires_in", 300)) - 50, 0)
        self._access_token = access_token
        return access_token

    async def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._get_access_token()}",
            "Content-Type": content_type,
            "x-okapi-tenant": self.tenant,
        }

    @staticmethod
    def _path(endpoint: str) -> str:
        return endpoint.lstrip("/")

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        return await self.session.get(
            self._path(endpoint),
            headers=await self._headers(),
            params=params,
            **kwargs,
        )

    async def post(
        self,
        endpoint: str,
        json_data: Any = None,
        data: Any = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        content_type = (
            "application/json"
            if json_data is not None
            else "application/x-www-form-urlencoded"
        )
        return await self.session.post(
            self._path(endpoint),
            headers=await self._headers(content_type),
            json=json_data,
            data=data,
            **kwargs,
        )

    async def put(
        self,
        endpoint: str,
        json_data: Any = None,
        data: Any = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        return await self._write("PUT", endpoint, json_data, data, **kwargs)

    async def patch(
        self,
        endpoint: str,
        json_data: Any = None,
        data: Any = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        return await self._write("PATCH", endpoint, json_data, data, **kwargs)

    async def _write(
        self,
        method: str,
        endpoint: str,
        json_data: Any,
        data: Any,
        **kwargs: Any,
    ) -> httpx2.Response:
        content_type = (
            "application/json"
            if json_data is not None
            else "application/x-www-form-urlencoded"
        )
        return await self.session.request(
            method,
            self._path(endpoint),
            headers=await self._headers(content_type),
            json=json_data,
            data=data,
            **kwargs,
        )

    async def delete(self, endpoint: str, **kwargs: Any) -> httpx2.Response:
        return await self.session.delete(
            self._path(endpoint), headers=await self._headers(), **kwargs
        )

    def invalidate_token(self) -> None:
        self._access_token = None
        self._token_obtained_at = 0.0

    @property
    async def access_token(self) -> str:
        return await self._get_access_token()

    async def close(self) -> None:
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()


def get_defaults():
    tenant = os.getenv("ILL_FOLIO_TENANT", None)
    realm = os.getenv("ILL_FOLIO_TENANT_REALM", None)
    username = os.getenv("ILL_FOLIO_TENANT_USERNAME", None)
    secret = os.getenv("ILL_FOLIO_TENANT_SECRET", None)
    return tenant, realm, username, secret


def get_folio_client(
    tenant_key: str | None = None,
    *,
    provider: CredentialProvider | None = None,
) -> FolioAsyncApiClient:
    cred_provider = provider or EnvCredentialProvider(get_settings(strict_env=False))
    creds = cred_provider.get_credentials(tenant_key)

    client = FolioAsyncApiClient(
        username=creds.username,
        secret=creds.secret,
        auth_base_url=(
            f"https://auth.{creds.library_tenant}-{creds.environment}.bibliotek.sunet.se"
        ),
        api_base_url=(
            f"https://api.{creds.library_tenant}-{creds.environment}.bibliotek.sunet.se"
        ),
        realm=creds.folio_realm,
        tenant=creds.folio_tenant,
    )
    print(f"===> Tenant: {creds.library_tenant} ({creds.environment})")
    print(f"===> Auth base URL: {client.auth_base_url}")
    print(f"===> API base URL: {client.api_base_url}")
    return client


async def api_get(
    client,
    endpoint: str,
    params: dict | None = None,
) -> list[dict]:
    """General GET request to retrieve all records from a FOLIO endpoint."""
    if params is None:
        params = {"limit": 1000, "offset": 0}
    response = await client.get(endpoint, params=params)
    response.raise_for_status()
    data = await response.json()
    return data
