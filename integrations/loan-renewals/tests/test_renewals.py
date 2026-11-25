from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from loan_renewals import (
    AutoRenewalService,
    LoanTypeFilterRule,
    MaxLoanPeriodRule,
    NoOtherOverdueLoansRule,
    RenewalConfig,
    RenewalContext,
    RuleDecision,
    RuleEngine,
    build_default_rule_engine,
)


def _make_loan(
    loan_id: str = "loan-1",
    item_id: str = "item-1",
    user_id: str = "user-1",
    *,
    status: str = "Open",
    loan_date: datetime | None = None,
    due_date: datetime | None = None,
    renewal_count: int = 0,
    loan_policy_id: str = "policy-1",
    loan_type_id: str = "lt-1",
) -> dict[str, Any]:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    return {
        "id": loan_id,
        "itemId": item_id,
        "userId": user_id,
        "status": {"name": status},
        "loanDate": (loan_date or (now - timedelta(days=20))).isoformat(),
        "dueDate": (due_date or (now - timedelta(hours=1))).isoformat(),
        "renewalCount": renewal_count,
        "loanPolicyId": loan_policy_id,
        "item": {"permanentLoanTypeId": loan_type_id},
    }


def _make_policy(
    policy_id: str = "policy-1",
    *,
    renewable: bool = True,
    number_allowed: int = 10,
    duration: int = 21,
    interval_id: str = "Days",
    renew_from_id: str = "CURRENT_DUE_DATE",
) -> dict[str, Any]:
    return {
        "id": policy_id,
        "name": "LPTest",
        "renewable": renewable,
        "loansPolicy": {
            "period": {"duration": duration, "intervalId": interval_id},
        },
        "renewalsPolicy": {
            "numberAllowed": number_allowed,
            "renewFromId": renew_from_id,
        },
    }


class TestRulesAndEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def test_default_rules_allow_eligible_student_loan(self) -> None:
        loan = _make_loan()
        ctx = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-student"},
            patron_open_loans=[loan],
            now=self.now,
            patron_group_name="student",
            loan_policy=_make_policy(),
            loan_type_name="auto-renewal-test",
        )
        engine = build_default_rule_engine(
            RenewalConfig(allowed_loan_type_names={"auto-renewal-test"})
        )
        allowed, decisions = engine.evaluate(ctx)
        self.assertTrue(allowed)
        self.assertTrue(all(d.allowed for d in decisions))

    def test_max_loan_period_rejects_student_past_180_days_from_loan_date(self) -> None:
        loan = _make_loan(
            loan_date=self.now - timedelta(days=170),
            due_date=self.now,
        )
        ctx = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-student"},
            patron_open_loans=[loan],
            now=self.now,
            patron_group_name="student",
            loan_policy=_make_policy(duration=21, interval_id="Days"),
        )
        decision = MaxLoanPeriodRule({"student": 180, "staff": 365}).evaluate(ctx)
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds max loan horizon", decision.reason)

    def test_no_other_overdue_loans_allows_other_non_overdue_loan(self) -> None:
        loan = _make_loan(loan_id="loan-1", due_date=self.now - timedelta(hours=1))
        other_ok = _make_loan(loan_id="loan-2", due_date=self.now + timedelta(days=5))
        ctx = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-student"},
            patron_open_loans=[loan, other_ok],
            now=self.now,
        )
        self.assertTrue(NoOtherOverdueLoansRule().evaluate(ctx).allowed)

        other_overdue = _make_loan(
            loan_id="loan-3", due_date=self.now - timedelta(days=2)
        )
        ctx.patron_open_loans.append(other_overdue)
        decision = NoOtherOverdueLoansRule().evaluate(ctx)
        self.assertFalse(decision.allowed)
        self.assertIn("loan-3", decision.reason)

    def test_rule_engine_add_remove_replace(self) -> None:
        class AlwaysBlockRule:
            name = "custom_block"

            def evaluate(self, ctx: RenewalContext) -> RuleDecision:
                return RuleDecision(False, self.name, "Blocked by custom rule")

        loan = _make_loan()
        ctx = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan],
            now=self.now,
            patron_group_name="staff",
            loan_policy=_make_policy(),
        )
        engine = build_default_rule_engine()
        engine.add(AlwaysBlockRule())
        allowed, decisions = engine.evaluate(ctx)
        self.assertFalse(allowed)
        self.assertEqual(decisions[-1].rule, "custom_block")

        self.assertTrue(engine.remove("custom_block"))
        allowed_after, _ = engine.evaluate(ctx)
        self.assertTrue(allowed_after)

        engine.add(LoanTypeFilterRule(allowed_names={"only-this"}))
        allowed_filtered, _ = engine.evaluate(ctx)
        self.assertFalse(allowed_filtered)


class TestAutoRenewalService(unittest.IsolatedAsyncioTestCase):
    async def test_service_renews_eligible_and_handles_folio_rejection(self) -> None:
        now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
        eligible_loan = _make_loan(loan_id="loan-ok", item_id="item-ok", user_id="u1")
        rejected_loan = _make_loan(loan_id="loan-rej", item_id="item-rej", user_id="u2")
        not_due_loan = _make_loan(
            loan_id="loan-future",
            item_id="item-future",
            user_id="u1",
            due_date=now + timedelta(days=10),
        )

        client = MagicMock()

        def mock_resp(status_code: int, payload: dict[str, Any]) -> MagicMock:
            resp = MagicMock()
            resp.status_code = status_code
            resp.is_success = 200 <= status_code < 300
            resp.content = b"{}"
            resp.text = str(payload)
            resp.json.return_value = payload
            resp.raise_for_status = MagicMock()
            return resp

        async def fake_get(endpoint: str, params: dict[str, Any] | None = None):
            if endpoint == "circulation/loans":
                return mock_resp(
                    200,
                    {
                        "loans": [eligible_loan, rejected_loan, not_due_loan],
                        "totalRecords": 3,
                    },
                )
            if endpoint == "groups":
                return mock_resp(200, {"usergroups": [{"id": "g-staff", "group": "staff"}]})
            if endpoint == "loan-policy-storage/loan-policies":
                return mock_resp(200, {"loanPolicies": [_make_policy()]})
            if endpoint == "loan-types":
                return mock_resp(200, {"loantypes": [{"id": "lt-1", "name": "auto-renewal-test"}]})
            if endpoint == "users/u1":
                return mock_resp(200, {"id": "u1", "active": True, "patronGroup": "g-staff"})
            if endpoint == "users/u2":
                return mock_resp(200, {"id": "u2", "active": True, "patronGroup": "g-staff"})
            raise AssertionError(f"Unexpected GET {endpoint}")

        async def fake_post(endpoint: str, json_data: dict[str, Any] | None = None):
            assert endpoint == "circulation/renew-by-id"
            assert json_data is not None
            if json_data["itemId"] == "item-ok":
                return mock_resp(200, {"id": "loan-ok", "dueDate": "2026-12-15T12:00:00+00:00"})
            return mock_resp(422, {"errors": [{"message": "Item has an active hold request"}]})

        client.get = AsyncMock(side_effect=fake_get)
        client.post = AsyncMock(side_effect=fake_post)

        service = AutoRenewalService(
            client,
            config=RenewalConfig(
                dry_run=False,
                allowed_loan_type_names={"auto-renewal-test"},
            ),
        )
        summary = await service.run(now=now)

        self.assertEqual(summary.total_open_loans, 3)
        self.assertEqual(summary.eligible, 2)
        self.assertEqual(summary.renewed, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.skipped, 1)

        by_loan = {a.loan_id: a for a in summary.attempts}
        self.assertEqual(by_loan["loan-ok"].result, "renewed")
        self.assertEqual(by_loan["loan-ok"].new_due_date, "2026-12-15T12:00:00+00:00")
        self.assertEqual(by_loan["loan-rej"].result, "failed")
        self.assertIn("active hold request", by_loan["loan-rej"].error_message or "")
        self.assertEqual(by_loan["loan-future"].result, "skipped")


if __name__ == "__main__":
    unittest.main()
