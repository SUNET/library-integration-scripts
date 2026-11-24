import time
from typing import Any, Optional, Union

import requests
from errors import AuthenticationError


class FolioApiClient:
    """
    API Client for folio operations
    """

    def __init__(
        self,
        username: str,
        secret: str,
        auth_base_url: str = "https://auth.sunet-test.bibliotek.sunet.se",
        api_base_url: str = "https://api.sunet-test.bibliotek.sunet.se",
        realm: str = "diku",
        tenant: str = "diku",
        timeout: int = 30,
    ):
        self.username = username
        self.secret = secret
        self.auth_base_url = auth_base_url.strip()
        self.api_base_url = api_base_url.strip()
        self.realm = realm
        self.tenant = tenant
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_obtained_at: float = 0.0
        self._token_ttl: float = 250  # refresh before typical 300s expiry

    def _get_access_token(self) -> str:
        """Obtain and cache access token from Keycloak, refreshing if expired."""
        if (
            self._access_token is not None
            and (time.time() - self._token_obtained_at) < self._token_ttl
        ):
            return self._access_token

        auth_data = {
            "grant_type": "client_credentials",
            "client_id": self.username,
            "client_secret": self.secret,
        }
        print(
            "-->",
            f"{self.auth_base_url}/realms/{self.realm}/protocol/openid-connect/token",
        )

        try:
            response = requests.post(
                f"{self.auth_base_url}/realms/{self.realm}/protocol/openid-connect/token",
                data=auth_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            self._access_token = response.json()["access_token"]
            self._token_obtained_at = time.time()
            return self._access_token

        except requests.exceptions.RequestException as e:
            raise AuthenticationError(f"Failed to obtain access token: {e}")
        except KeyError:
            raise AuthenticationError("Invalid response: access_token not found")

    def _make_headers(self, content_type: str = "application/json") -> dict[str, str]:
        """Build request headers with authentication."""
        return {
            "Content-Type": content_type,
            "x-okapi-tenant": self.tenant,
            "Authorization": f"Bearer {self._get_access_token()}",
        }

    def get(
        self, endpoint: str, params: dict[str, Any] | None = None, **kwargs
    ) -> requests.Response:
        """Execute GET request."""
        url = self._build_url(endpoint)
        return requests.get(
            url,
            headers=self._make_headers(),
            params=params,
            timeout=self.timeout,
            **kwargs,
        )

    def post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        data: dict[str, Any] | str | None = None,
        **kwargs,
    ) -> requests.Response:
        """Execute POST request."""
        url = self._build_url(endpoint)
        if json_data is not None:
            return requests.post(
                url,
                headers=self._make_headers("application/json"),
                json=json_data,
                timeout=self.timeout,
                **kwargs,
            )
        else:
            return requests.post(
                url,
                headers=self._make_headers("application/x-www-form-urlencoded"),
                data=data,
                timeout=self.timeout,
                **kwargs,
            )

    def delete(self, endpoint: str, **kwargs) -> requests.Response:
        """Execute DELETE request."""
        url = self._build_url(endpoint)
        return requests.delete(
            url, headers=self._make_headers(), timeout=self.timeout, **kwargs
        )

    def patch(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        data: dict[str, Any] | str | None = None,
        **kwargs,
    ) -> requests.Response:
        """Execute PATCH request."""
        url = self._build_url(endpoint)
        if json_data is not None:
            return requests.patch(
                url,
                headers=self._make_headers("application/json"),
                json=json_data,
                timeout=self.timeout,
                **kwargs,
            )
        else:
            return requests.patch(
                url,
                headers=self._make_headers("application/x-www-form-urlencoded"),
                data=data,
                timeout=self.timeout,
                **kwargs,
            )

    def put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        data: dict[str, Any] | str | None = None,
        **kwargs,
    ) -> requests.Response:
        """Execute PUT request."""
        url = self._build_url(endpoint)
        if json_data is not None:
            return requests.put(
                url,
                headers=self._make_headers("application/json"),
                json=json_data,
                timeout=self.timeout,
                **kwargs,
            )
        else:
            return requests.put(
                url,
                headers=self._make_headers("application/x-www-form-urlencoded"),
                data=data,
                timeout=self.timeout,
                **kwargs,
            )

    def _build_url(self, endpoint: str) -> str:
        """Construct full URL from endpoint path."""
        return f"{self.api_base_url}/{endpoint.lstrip('/')}"

    def invalidate_token(self) -> None:
        """Clear cached access token to force re-authentication."""
        self._access_token = None

    @property
    def access_token(self) -> str:
        """Access current token (triggers auth if needed)."""
        return self._get_access_token()


def test():
    # Initialize client
    client = FolioApiClient(username="dd", secret="ddd")

    # GET request
    response = client.get("tenants")
    print(response.json())

    # POST with JSON payload
    contributor_data = {
        "source": "local",
        "name": "dsdds",
        "code": "fsffs",
        "id": "5ed4f9e6-8d04-46b0-b850-af961cc8402b",
    }
    response = client.post("contributor-types", json_data=contributor_data)

    # PATCH request
    response = client.patch(
        "contributor-types/5ed4f9e6-8d04-46b0-b850-af961cc8402b",
        json_data={"name": "updated_name"},
    )

    # DELETE request
    response = client.delete("contributor-types/5ed4f9e6-8d04-46b0-b850-af961cc8402b")

    # POST with form data (if needed)
    response = client.post("some-endpoint", data={"key": "value"})
