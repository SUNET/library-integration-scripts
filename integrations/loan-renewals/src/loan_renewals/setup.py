"""_summary_

Returns:
    _type_: _description_

secretspec run -- uv run python src/loan_renewals/setup.py
"""

import asyncio
from typing import Any

from folio_client import FolioAsyncApiClient, get_folio_client


async def get_tenant_institution(
    client: FolioAsyncApiClient, code: str | None = None
) -> dict[str, Any] | None:
    params = {"query": f'code=="{code}"'} if code else None
    response = await client.get("location-units/institutions", params=params)
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


def get_client(tenant_key: str | None = None) -> FolioAsyncApiClient:
    """Initialize and return a configured FOLIO API client."""
    return get_folio_client(tenant_key)


if __name__ == "__main__":
    folio_client = get_client()

    insts = asyncio.run(get_tenant_institution(folio_client))
    print("Institutions:", insts)
