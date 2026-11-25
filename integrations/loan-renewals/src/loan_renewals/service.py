from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx2
from folio_client import FolioAsyncApiClient

from loan_renewals.models import (
    RenewalAttempt,
    RenewalConfig,
    RenewalContext,
    RenewalRunSummary,
)
from loan_renewals.rules import RuleEngine, build_default_rule_engine

logger = logging.getLogger(__name__)


def _extract_folio_error(response: httpx2.Response) -> str:
    """Extract a readable error message from a FOLIO error response."""
    try:
        body = response.json()
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            messages = [
                str(err.get("message") or err)
                for err in errors
                if isinstance(err, dict)
            ]
            if messages:
                return "; ".join(messages)
        if "errorMessage" in body:
            return str(body["errorMessage"])
    except Exception:
        pass
    return response.text or f"HTTP {response.status_code}"


class AutoRenewalService:
    """Evaluates open FOLIO loans against a RuleEngine and renews eligible loans."""

    def __init__(
        self,
        client: FolioAsyncApiClient,
        *,
        config: RenewalConfig | None = None,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self.client = client
        self.config = config or RenewalConfig()
        self.rule_engine = rule_engine or build_default_rule_engine(self.config)
        self._patron_cache: dict[str, dict[str, Any]] = {}

    async def _call_with_retry(
        self,
        operation: str,
        fn: Callable[[], Awaitable[httpx2.Response]],
    ) -> httpx2.Response:
        """Execute an HTTP call with exponential backoff for transient errors."""
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await fn()
                if response.status_code >= 500 and attempt < self.config.max_retries:
                    delay = self.config.retry_backoff_seconds * (2**attempt)
                    logger.warning(
                        "%s returned %s; retrying in %.1fs (%d/%d)",
                        operation,
                        response.status_code,
                        delay,
                        attempt + 1,
                        self.config.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                return response
            except httpx2.HTTPError as exc:
                last_exc = exc
                if attempt >= self.config.max_retries:
                    raise
                delay = self.config.retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "%s failed (%s); retrying in %.1fs (%d/%d)",
                    operation,
                    exc,
                    delay,
                    attempt + 1,
                    self.config.max_retries,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def fetch_open_loans(self) -> list[dict[str, Any]]:
        """Paginate through all open loans in FOLIO."""
        loans: list[dict[str, Any]] = []
        offset = 0
        limit = self.config.page_size

        while True:
            response = await self._call_with_retry(
                f"GET circulation/loans (offset={offset})",
                lambda off=offset: self.client.get(
                    "circulation/loans",
                    params={
                        "query": 'status.name=="Open"',
                        "limit": limit,
                        "offset": off,
                    },
                ),
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("loans", [])
            loans.extend(batch)

            total_records = payload.get("totalRecords", len(loans))
            offset += len(batch)
            if not batch or len(batch) < limit or offset >= total_records:
                break

        return loans

    async def fetch_patron_groups(self) -> dict[str, str]:
        """Return a mapping of patron group UUID -> group name."""
        response = await self._call_with_retry(
            "GET groups",
            lambda: self.client.get("groups", params={"limit": 1000}),
        )
        response.raise_for_status()
        groups = response.json().get("usergroups", [])
        return {
            g["id"]: g.get("group", "")
            for g in groups
            if isinstance(g, dict) and g.get("id")
        }

    async def fetch_loan_policies(self) -> dict[str, dict[str, Any]]:
        """Return a mapping of loan policy UUID -> policy payload."""
        response = await self._call_with_retry(
            "GET loan-policy-storage/loan-policies",
            lambda: self.client.get(
                "loan-policy-storage/loan-policies", params={"limit": 1000}
            ),
        )
        response.raise_for_status()
        policies = response.json().get("loanPolicies", [])
        return {
            p["id"]: p
            for p in policies
            if isinstance(p, dict) and p.get("id")
        }

    async def fetch_loan_types(self) -> dict[str, str]:
        """Return a mapping of loan type UUID -> loan type name."""
        response = await self._call_with_retry(
            "GET loan-types",
            lambda: self.client.get("loan-types", params={"limit": 1000}),
        )
        response.raise_for_status()
        loan_types = response.json().get("loantypes", [])
        return {
            lt["id"]: lt.get("name", "")
            for lt in loan_types
            if isinstance(lt, dict) and lt.get("id")
        }

    async def fetch_patron(self, user_id: str) -> dict[str, Any]:
        """Fetch a patron record, cached per run."""
        if user_id not in self._patron_cache:
            response = await self._call_with_retry(
                f"GET users/{user_id}",
                lambda uid=user_id: self.client.get(f"users/{uid}"),
            )
            response.raise_for_status()
            self._patron_cache[user_id] = response.json()
        return self._patron_cache[user_id]

    async def renew_loan(
        self,
        item_id: str,
        user_id: str,
    ) -> httpx2.Response:
        """Renew a loan via FOLIO's POST /circulation/renew-by-id endpoint."""
        return await self._call_with_retry(
            f"POST circulation/renew-by-id (item={item_id})",
            lambda: self.client.post(
                "circulation/renew-by-id",
                json_data={"itemId": item_id, "userId": user_id},
            ),
        )

    async def run(self, *, now: datetime | None = None) -> RenewalRunSummary:
        """Run one full auto-renewal pass over all open loans."""
        execution_id = str(uuid.uuid4())
        run_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started_at = run_now.isoformat()

        self._patron_cache.clear()
        open_loans, patron_groups, loan_policies, loan_types = await asyncio.gather(
            self.fetch_open_loans(),
            self.fetch_patron_groups(),
            self.fetch_loan_policies(),
            self.fetch_loan_types(),
        )

        loans_by_patron: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for loan in open_loans:
            user_id = loan.get("userId")
            if user_id:
                loans_by_patron[user_id].append(loan)

        summary = RenewalRunSummary(
            execution_id=execution_id,
            dry_run=self.config.dry_run,
            started_at=started_at,
            finished_at=started_at,
            total_open_loans=len(open_loans),
        )

        processed_loan_ids: set[str] = set()
        renewals_triggered = 0

        for loan in open_loans:
            loan_id = str(loan.get("id") or "")
            item_id = str(loan.get("itemId") or "")
            patron_id = str(loan.get("userId") or "")
            previous_due_date = loan.get("dueDate")
            attempt_ts = datetime.now(timezone.utc).isoformat()

            if not loan_id or loan_id in processed_loan_ids:
                continue
            processed_loan_ids.add(loan_id)

            if (
                self.config.max_renewals_per_run is not None
                and renewals_triggered >= self.config.max_renewals_per_run
            ):
                logger.info(
                    "Reached max_renewals_per_run (%d); stopping further renewals",
                    self.config.max_renewals_per_run,
                )
                break

            if not patron_id or not item_id:
                summary.skipped += 1
                summary.attempts.append(
                    RenewalAttempt(
                        execution_id=execution_id,
                        timestamp=attempt_ts,
                        loan_id=loan_id,
                        item_id=item_id,
                        patron_id=patron_id,
                        previous_due_date=previous_due_date,
                        new_due_date=None,
                        result="skipped",
                        reason="Loan missing userId or itemId",
                    )
                )
                continue

            try:
                patron = await self.fetch_patron(patron_id)
            except Exception as exc:
                summary.failed += 1
                summary.attempts.append(
                    RenewalAttempt(
                        execution_id=execution_id,
                        timestamp=attempt_ts,
                        loan_id=loan_id,
                        item_id=item_id,
                        patron_id=patron_id,
                        previous_due_date=previous_due_date,
                        new_due_date=None,
                        result="failed",
                        reason="Failed to fetch patron",
                        error_message=str(exc),
                    )
                )
                continue

            group_id = patron.get("patronGroup")
            policy_id = loan.get("loanPolicyId")
            item = loan.get("item") or {}
            loan_type_id = (
                loan.get("loanTypeId")
                or item.get("temporaryLoanTypeId")
                or item.get("permanentLoanTypeId")
            )

            ctx = RenewalContext(
                loan=loan,
                patron=patron,
                patron_open_loans=loans_by_patron.get(patron_id, []),
                now=run_now,
                patron_group_name=patron_groups.get(group_id) if group_id else None,
                loan_policy=loan_policies.get(policy_id) if policy_id else None,
                loan_type_name=loan_types.get(loan_type_id) if loan_type_id else None,
            )

            allowed, decisions = self.rule_engine.evaluate(ctx)
            if not allowed:
                failing = next(d for d in decisions if not d.allowed)
                summary.skipped += 1
                summary.attempts.append(
                    RenewalAttempt(
                        execution_id=execution_id,
                        timestamp=attempt_ts,
                        loan_id=loan_id,
                        item_id=item_id,
                        patron_id=patron_id,
                        previous_due_date=previous_due_date,
                        new_due_date=None,
                        result="skipped",
                        reason=f"[{failing.rule}] {failing.reason}",
                    )
                )
                continue

            summary.eligible += 1
            if self.config.dry_run:
                renewals_triggered += 1
                summary.attempts.append(
                    RenewalAttempt(
                        execution_id=execution_id,
                        timestamp=attempt_ts,
                        loan_id=loan_id,
                        item_id=item_id,
                        patron_id=patron_id,
                        previous_due_date=previous_due_date,
                        new_due_date=None,
                        result="dry_run",
                        reason="Eligible (dry-run)",
                    )
                )
                continue

            try:
                response = await self.renew_loan(item_id=item_id, user_id=patron_id)
                if response.is_success:
                    renewed_loan = response.json() if response.content else {}
                    summary.renewed += 1
                    renewals_triggered += 1
                    summary.attempts.append(
                        RenewalAttempt(
                            execution_id=execution_id,
                            timestamp=attempt_ts,
                            loan_id=loan_id,
                            item_id=item_id,
                            patron_id=patron_id,
                            previous_due_date=previous_due_date,
                            new_due_date=renewed_loan.get("dueDate"),
                            result="renewed",
                            reason="Eligible",
                        )
                    )
                else:
                    error_msg = _extract_folio_error(response)
                    summary.failed += 1
                    summary.attempts.append(
                        RenewalAttempt(
                            execution_id=execution_id,
                            timestamp=attempt_ts,
                            loan_id=loan_id,
                            item_id=item_id,
                            patron_id=patron_id,
                            previous_due_date=previous_due_date,
                            new_due_date=None,
                            result="failed",
                            reason=f"FOLIO rejected renewal (HTTP {response.status_code})",
                            error_message=error_msg,
                        )
                    )
            except Exception as exc:
                summary.failed += 1
                summary.attempts.append(
                    RenewalAttempt(
                        execution_id=execution_id,
                        timestamp=attempt_ts,
                        loan_id=loan_id,
                        item_id=item_id,
                        patron_id=patron_id,
                        previous_due_date=previous_due_date,
                        new_due_date=None,
                        result="failed",
                        reason="Renewal request error",
                        error_message=str(exc),
                    )
                )

        summary.finished_at = datetime.now(timezone.utc).isoformat()
        return summary
