"""_summary_

Returns:
    _type_: _description_

secretspec run -- uv run python src/loan_renewals/tests.py
"""
import asyncio
import json
from typing import Any

from utils import FolioAsyncApiClient, get_client, save_json_to_file


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


async def get_circulation_rules(client: FolioAsyncApiClient) -> dict[str, Any]:
    """Fetch current circulation rules via GET /circulation/rules."""
    response = await client.get("circulation/rules")
    response.raise_for_status()
    return response.json()


async def get_open_loans(client: FolioAsyncApiClient) -> list[dict[str, Any]]:
    """Fetch open loans for a user via GET /circulation/loans?query=userId=={user_id}."""
    params = {
        "query": 'status.name=="Open"',
        "limit": 1000,
        "offset": 0,
    }
    response = await client.get("circulation/loans", params=params)
    response.raise_for_status()
    json_data = response.json()
    save_json_to_file(json_data, "open_loans.json")
    print(json_data)
    # print("Fetched open loans:", json.dumps(json_data, indent=2))
    return json_data.get("loans", [])

async def get_loan_policies(client: FolioAsyncApiClient) -> list[dict[str, Any]]:
    """Fetch loan policies via GET /loan-policy-storage/loan-policies."""
    params = {
        "limit": 1000,
    }
    response = await client.get("loan-policy-storage/loan-policies", params=params)
    response.raise_for_status()
    json_data = response.json()
    save_json_to_file(json_data, "loan_policies.json")
    print(json_data)
    # print("Fetched loan policies:", json.dumps(json_data, indent=2))
    return json_data.get("loanPolicies", [])


async def get_fixed_due_date_schedules(client: FolioAsyncApiClient) -> list[dict[str, Any]]:
    """Fetch fixed due date schedules via GET /fixed-due-date-schedule-storage/fixed-due-date-schedules."""
    params = {
        "limit": 1000,
    }
    response = await client.get(
        "fixed-due-date-schedule-storage/fixed-due-date-schedules", params=params
    )
    response.raise_for_status()
    json_data = response.json()
    save_json_to_file(json_data, "fixed_due_date_schedules.json")
    print(json_data)
    # print("Fetched fixed due date schedules:", json.dumps(json_data, indent=2))
    return json_data.get("fixedDueDateSchedules", [])

if __name__ == "__main__":
    folio_client = get_client()

    # insts = asyncio.run(get_tenant_institution(folio_client))
    fixed_due_date_schedules = asyncio.run(get_fixed_due_date_schedules(folio_client))
    docs = json.dumps(fixed_due_date_schedules, indent=2)
    # save_json_to_file(fixed_due_date_schedules, "fixed_due_date_schedules.json")

    # print("Institutions:", insts)
