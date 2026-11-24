"""_summary_

Returns:
    _type_: _description_

secretspec run -- uv run python src/loan_renewals/setup.py
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from .utils import FolioAsyncApiClient, get_client

DEFAULT_REQUEST_POLICY_ID = "40e2d763-bc4d-403b-bfe7-14112dc7b376"
DEFAULT_NOTICE_POLICY_ID = "c590dc70-763e-4117-8b4f-e742fe3f2f54"
DEFAULT_OVERDUE_FINE_POLICY_ID = "ad406a94-90e3-451c-b5a0-fed9c4f5b5e8"
DEFAULT_LOST_ITEM_FEE_POLICY_ID = "b4089ee9-8134-43b7-a29d-f0eefade6827"


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


async def create_loan_policy(
    client: FolioAsyncApiClient,
    name: str = "LPTestAutoRenewal1Day",
    *,
    loanable: bool = True,
    profile_id: str = "Rolling",
    duration: int = 1,
    interval_id: str = "Days",
    closed_library_due_date_management_id: str = "END_OF_THE_NEXT_OPEN_DAY",
    renewable: bool = True,
    number_allowed: int = 10,
    renew_from_id: str = "CURRENT_DUE_DATE",
    policy_id: str | None = None,
) -> str:
    """Create a loan policy via POST /loan-policy-storage/loan-policies."""
    payload: dict[str, Any] = {
        "name": name,
        "loanable": loanable,
        "loansPolicy": {
            "profileId": profile_id,
            "period": {
                "duration": duration,
                "intervalId": interval_id,
            },
            "closedLibraryDueDateManagementId": closed_library_due_date_management_id,
        },
        "renewable": renewable,
        "renewalsPolicy": {
            "numberAllowed": number_allowed,
            "renewFromId": renew_from_id,
        },
    }
    if policy_id is not None:
        payload["id"] = policy_id

    response = await client.post("loan-policy-storage/loan-policies", json_data=payload)
    print(
        f"POST loan-policy '{payload['name']}': {response.status_code} - {response.text}"
    )
    response.raise_for_status()
    return response.json()["id"]


async def create_loan_types(
    client: FolioAsyncApiClient,
    loan_name: str,
    source: str = "folio",
) -> str:
    """Create a loan type via POST /loan-types."""
    loan_id = str(uuid.uuid4())
    loan_type = {
        "id": loan_id,
        "name": loan_name,
        "source": f"{source}",
    }
    response = await client.post("loan-types", json_data=loan_type)
    print(
        f"POST loan-type '{loan_type['name']}': {response.status_code} - {response.text}"
    )
    response.raise_for_status()
    return loan_id


async def get_circulation_rules(client: FolioAsyncApiClient) -> dict[str, Any]:
    """Fetch current circulation rules via GET /circulation/rules."""
    response = await client.get("circulation/rules")
    response.raise_for_status()
    return response.json()


async def add_circulation_rule(
    client: FolioAsyncApiClient,
    loan_type_id: str,
    loan_policy_id: str,
    *,
    request_policy_id: str = DEFAULT_REQUEST_POLICY_ID,
    notice_policy_id: str = DEFAULT_NOTICE_POLICY_ID,
    overdue_fine_policy_id: str = DEFAULT_OVERDUE_FINE_POLICY_ID,
    lost_item_fee_policy_id: str = DEFAULT_LOST_ITEM_FEE_POLICY_ID,
    comment: str | None = None,
    backup_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a loan-type circulation rule via GET + PUT /circulation/rules.

    Preserves all existing rules and appends:
    ``t <loan_type_id> : l <loan_policy_id> r <request_policy_id> n <notice_policy_id> o <overdue_fine_policy_id> i <lost_item_fee_policy_id>``
    """
    current_rules = await get_circulation_rules(client)
    rules_id = current_rules["id"]
    existing_text = current_rules.get("rulesAsText", "")

    if backup_path is not None:
        Path(backup_path).write_text(
            json.dumps(current_rules, indent=2), encoding="utf-8"
        )
        print(f"Backed up existing circulation rules to {backup_path}")

    rule_line = (
        f"t {loan_type_id} : "
        f"l {loan_policy_id} "
        f"r {request_policy_id} "
        f"n {notice_policy_id} "
        f"o {overdue_fine_policy_id} "
        f"i {lost_item_fee_policy_id}"
    )
    new_rule_lines = []
    if comment:
        comment_line = comment if comment.startswith("#") else f"# {comment}"
        new_rule_lines.append(comment_line)
    new_rule_lines.append(rule_line)
    new_rule_block = "\n".join(new_rule_lines)

    updated_rules_text = f"{existing_text.rstrip()}\n\n{new_rule_block}\n"
    payload = {
        "id": rules_id,
        "rulesAsText": updated_rules_text,
    }

    response = await client.put("circulation/rules", json_data=payload)
    print(f"PUT circulation/rules: {response.status_code} - {response.text}")
    response.raise_for_status()
    return response.json() if response.content else payload


async def setup(
    folio_client: FolioAsyncApiClient,
    loan_type_name: str = "Example Loan Type",
    loan_policy_name: str = "LPTestAutoRenewal1Day",
    duration: int = 1,
    interval_id: str = "Days",
    closed_library_due_date_management_id: str = "END_OF_THE_NEXT_OPEN_DAY",
    request_policy_id: str = DEFAULT_REQUEST_POLICY_ID,
    notice_policy_id: str = DEFAULT_NOTICE_POLICY_ID,
    overdue_fine_policy_id: str = DEFAULT_OVERDUE_FINE_POLICY_ID,
    lost_item_fee_policy_id: str = DEFAULT_LOST_ITEM_FEE_POLICY_ID,
    backup_path: str | Path | None = None,
) -> dict[str, Any]:
    """Perform initial setup for the loan renewals integration."""
    loan_type_id = await create_loan_types(folio_client, loan_type_name)
    loan_policy_id = await create_loan_policy(
        folio_client,
        loan_policy_name,
        duration=duration,
        interval_id=interval_id,
        closed_library_due_date_management_id=closed_library_due_date_management_id,
    )
    return await add_circulation_rule(
        folio_client,
        loan_type_id=loan_type_id,
        loan_policy_id=loan_policy_id,
        request_policy_id=request_policy_id,
        notice_policy_id=notice_policy_id,
        overdue_fine_policy_id=overdue_fine_policy_id,
        lost_item_fee_policy_id=lost_item_fee_policy_id,
        backup_path=backup_path,
    )


if __name__ == "__main__":
    folio_client = get_client()

    # insts = asyncio.run(get_tenant_institution(folio_client))
    rules = asyncio.run(get_circulation_rules(folio_client))
    print("Circulation rules:", rules)
    # print("Institutions:", insts)
