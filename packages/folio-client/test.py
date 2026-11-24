import os
from typing import Any

from folio_client import FolioApiClient
from dotenv import load_dotenv


def get_tenant_institution(
    client: FolioApiClient, code: str | None = None
) -> dict[str, Any] | None:
    """Return the institution matching code, or the first institution."""
    # https://api.sunet-test.bibliotek.sunet.se/location-units/institutions?query=cql.allRecords=1%20sortby%20name&limit=2000
    params = {"query": f'code=="{code}"'} if code else None
    response = client.get("location-units/institutions", params=params)
    response.raise_for_status()
    institutions = response.json().get("locinsts", [])
    if code is None:
        return next(iter(institutions), None)
    return next(
        (
            institution
            for institution in institutions
            if institution.get("code") == code
        ),
        None,
    )


def get_tenant_all_service_points(client: FolioApiClient):
    """Retrieve all service points."""
    try:
        response = client.get("service-points")
        response.raise_for_status()
    except Exception as e:
        print(f"Request failed: {e}")
        return None

    data = response.json()
    return data.get("servicepoints", [])


if __name__ == "__main__":
    # Initialize client
    load_dotenv()
    prefix = "BTH"
    USERNAME = os.getenv(f"FOLIO_API_USERNAME_{prefix}") or os.getenv(
        f"FOLIO_USERNAME_{prefix}"
    )
    SECRET = os.getenv(f"FOLIO_API_SECRET_{prefix}") or os.getenv(
        f"FOLIO_SECRET_{prefix}"
    )
    print("---->", USERNAME, SECRET)
    auth_base_url: str = f"https://auth.{prefix.lower()}-test.bibliotek.sunet.se"
    api_base_url: str = f"https://api.{prefix.lower()}-test.bibliotek.sunet.se"
    print("Initializing FOLIO API client...")
    print(f"Using username ({prefix}): {USERNAME}")

    client = FolioApiClient(
        username=USERNAME,
        secret=SECRET,
        auth_base_url=auth_base_url,
        api_base_url=api_base_url,
        realm="bth",
        tenant="bth",
    )

    insts = get_tenant_institution(client)
    print("Institutions:", insts)
