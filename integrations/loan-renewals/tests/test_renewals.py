from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from folio_client import MultiTenantCredentialProvider, Settings
from loan_renewals import (
    AutoRenewalService,
    DueWindowRule,
    LoanPolicyProfileRule,
    LoanTypeFilterRule,
    MaxLoanPeriodRule,
    NoOtherOverdueLoansRule,
    RenewalConfig,
    RenewalContext,
    RenewalRunSummary,
    RuleDecision,
    RuleEngine,
    TenantJobConfig,
    build_default_rule_engine,
    load_tenants_config,
    run_for_tenants,
)
from loan_renewals.cli import _resolve_jobs, build_parser


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
        "dueDate": (due_date or (now + timedelta(hours=1))).isoformat(),
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

    def test_due_window_rate_with_hourly_rolling_policy(self) -> None:
        hourly_policy = _make_policy(duration=1, interval_id="Hours")
        rule = DueWindowRule(window_rate=0.1)  # 6 minutes

        # 5 minutes before due -> inside 6-minute window -> allowed
        loan_inside = _make_loan(due_date=self.now + timedelta(minutes=5))
        ctx_inside = RenewalContext(
            loan=loan_inside,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan_inside],
            now=self.now,
            loan_policy=hourly_policy,
        )
        self.assertTrue(rule.evaluate(ctx_inside).allowed)

        # 10 minutes before due -> outside 6-minute window -> skipped
        loan_outside = _make_loan(due_date=self.now + timedelta(minutes=10))
        ctx_outside = RenewalContext(
            loan=loan_outside,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan_outside],
            now=self.now,
            loan_policy=hourly_policy,
        )
        decision = rule.evaluate(ctx_outside)
        self.assertFalse(decision.allowed)
        self.assertIn("rate=0.1", decision.reason)

    def test_rate_validation_rejects_out_of_bounds(self) -> None:
        with self.assertRaises(ValueError):
            RenewalConfig(renewal_window_rate=1.0)
        with self.assertRaises(ValueError):
            RenewalConfig(renewal_window_rate=-0.1)
        with self.assertRaises(ValueError):
            DueWindowRule(window_rate=1.0)

    def test_fixed_policy_rule_and_schedule_window(self) -> None:
        fixed_policy = {
            "id": "policy-fixed",
            "name": "TestFixedPolicy",
            "loansPolicy": {
                "profileId": "Fixed",
                "fixedDueDateScheduleId": "sched-jul",
            },
            "renewable": True,
            "renewalsPolicy": {"numberAllowed": 10},
        }
        schedule = {
            "id": "sched-jul",
            "name": "Jullån",
            "schedules": [
                {
                    "from": "2026-11-22T23:00:00.000+00:00",
                    "to": "2026-12-21T22:59:59.000+00:00",
                    "due": "2027-01-11T22:59:59.000+00:00",
                }
            ],
        }
        # Span is 50 days -> 10% window is 5 days before Jan 11 -> Jan 6
        loan = _make_loan(
            loan_policy_id="policy-fixed",
            loan_date=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
            due_date=datetime(2027, 1, 11, 22, 59, 59, tzinfo=timezone.utc),
        )

        # Excluded by default
        ctx_default = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan],
            now=datetime(2027, 1, 8, 12, 0, tzinfo=timezone.utc),
            loan_policy=fixed_policy,
            fixed_due_date_schedule=schedule,
        )
        profile_decision = LoanPolicyProfileRule(include_fixed=False).evaluate(ctx_default)
        self.assertFalse(profile_decision.allowed)
        self.assertIn("TestFixedPolicy", profile_decision.reason)

        # Allowed when include_fixed=True and inside 5-day window (Jan 8 -> 3.4 days left)
        self.assertTrue(LoanPolicyProfileRule(include_fixed=True).evaluate(ctx_default).allowed)
        self.assertTrue(DueWindowRule(window_rate=0.1).evaluate(ctx_default).allowed)

        # Outside 5-day window (Jan 1 -> 10.4 days left)
        ctx_early = RenewalContext(
            loan=loan,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan],
            now=datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc),
            loan_policy=fixed_policy,
            fixed_due_date_schedule=schedule,
        )
        self.assertFalse(DueWindowRule(window_rate=0.1).evaluate(ctx_early).allowed)

    def test_overdue_excluded_by_default_and_grace_period_handling(self) -> None:
        policy_with_grace = _make_policy(duration=21, interval_id="Days")
        policy_with_grace["loansPolicy"]["gracePeriod"] = {
            "duration": 3,
            "intervalId": "Days",
        }

        # 2 days overdue (within 3-day gracePeriod)
        loan_within_grace = _make_loan(due_date=self.now - timedelta(days=2))
        ctx_within = RenewalContext(
            loan=loan_within_grace,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan_within_grace],
            now=self.now,
            loan_policy=policy_with_grace,
        )

        # Default: excluded with clear message mentioning --include-overdue
        default_decision = DueWindowRule(window_rate=0.1).evaluate(ctx_within)
        self.assertFalse(default_decision.allowed)
        self.assertIn("--include-overdue", default_decision.reason)

        # Opt-in include_overdue=True: allowed
        self.assertTrue(
            DueWindowRule(window_rate=0.1, include_overdue=True).evaluate(ctx_within).allowed
        )

        # Opt-in include_grace_period=True: allowed while within gracePeriod
        self.assertTrue(
            DueWindowRule(window_rate=0.1, include_grace_period=True)
            .evaluate(ctx_within)
            .allowed
        )

        # 4 days overdue (past 3-day gracePeriod): rejected even with include_grace_period=True
        loan_past_grace = _make_loan(due_date=self.now - timedelta(days=4))
        ctx_past = RenewalContext(
            loan=loan_past_grace,
            patron={"id": "user-1", "active": True, "patronGroup": "grp-staff"},
            patron_open_loans=[loan_past_grace],
            now=self.now,
            loan_policy=policy_with_grace,
        )
        past_decision = DueWindowRule(
            window_rate=0.1, include_grace_period=True
        ).evaluate(ctx_past)
        self.assertFalse(past_decision.allowed)
        self.assertIn("past grace period", past_decision.reason)
        self.assertIn("--include-overdue", past_decision.reason)


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

    async def test_service_fixed_policy_skipped_by_default_and_evaluated_when_enabled(self) -> None:
        fixed_policy = {
            "id": "policy-fixed",
            "name": "TestFixedPolicy",
            "loansPolicy": {
                "profileId": "Fixed",
                "fixedDueDateScheduleId": "sched-jul",
            },
            "renewable": True,
            "renewalsPolicy": {"numberAllowed": 10},
        }
        fixed_schedule = {
            "id": "sched-jul",
            "name": "Jullån",
            "schedules": [
                {
                    "from": "2026-11-22T23:00:00.000+00:00",
                    "to": "2026-12-21T22:59:59.000+00:00",
                    "due": "2027-01-11T22:59:59.000+00:00",
                }
            ],
        }
        fixed_loan = _make_loan(
            loan_id="loan-fixed",
            item_id="item-fixed",
            user_id="u1",
            loan_policy_id="policy-fixed",
            loan_date=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
            due_date=datetime(2027, 1, 11, 22, 59, 59, tzinfo=timezone.utc),
        )
        now = datetime(2027, 1, 8, 12, 0, tzinfo=timezone.utc)

        def mock_resp(status_code: int, payload: dict[str, Any]) -> MagicMock:
            resp = MagicMock()
            resp.status_code = status_code
            resp.is_success = 200 <= status_code < 300
            resp.content = b"{}"
            resp.text = str(payload)
            resp.json.return_value = payload
            resp.raise_for_status = MagicMock()
            return resp

        fetched_schedules = False

        async def fake_get(endpoint: str, params: dict[str, Any] | None = None):
            nonlocal fetched_schedules
            if endpoint == "circulation/loans":
                return mock_resp(200, {"loans": [fixed_loan], "totalRecords": 1})
            if endpoint == "groups":
                return mock_resp(200, {"usergroups": [{"id": "g-staff", "group": "staff"}]})
            if endpoint == "loan-policy-storage/loan-policies":
                return mock_resp(200, {"loanPolicies": [fixed_policy]})
            if endpoint == "loan-types":
                return mock_resp(200, {"loantypes": [{"id": "lt-1", "name": "auto-renewal-test"}]})
            if endpoint == "fixed-due-date-schedule-storage/fixed-due-date-schedules":
                fetched_schedules = True
                return mock_resp(200, {"fixedDueDateSchedules": [fixed_schedule]})
            if endpoint == "users/u1":
                return mock_resp(200, {"id": "u1", "active": True, "patronGroup": "g-staff"})
            raise AssertionError(f"Unexpected GET {endpoint}")

        client = MagicMock()
        client.get = AsyncMock(side_effect=fake_get)
        client.post = AsyncMock()

        # 1. Default: include_fixed_policy=False -> skipped in report, schedules not fetched
        svc_default = AutoRenewalService(client, config=RenewalConfig(dry_run=True))
        summary_default = await svc_default.run(now=now)
        self.assertFalse(fetched_schedules)
        self.assertEqual(summary_default.skipped, 1)
        self.assertEqual(summary_default.eligible, 0)
        self.assertIn("[loan_policy_profile]", summary_default.attempts[0].reason)

        # 2. Enabled: include_fixed_policy=True -> fetches schedules, loan is eligible (dry_run)
        svc_fixed = AutoRenewalService(
            client,
            config=RenewalConfig(dry_run=True, include_fixed_policy=True, renewal_window_rate=0.1),
        )
        summary_fixed = await svc_fixed.run(now=now)
        self.assertTrue(fetched_schedules)
        self.assertEqual(summary_fixed.eligible, 1)
        self.assertEqual(summary_fixed.skipped, 0)
        self.assertEqual(summary_fixed.attempts[0].result, "dry_run")


class TestMultiTenantConfigAndCli(unittest.IsolatedAsyncioTestCase):
    def test_load_repo_default_tenants_toml(self) -> None:
        toml_path = Path(__file__).resolve().parents[1] / "tenants.toml"
        jobs = load_tenants_config(toml_path)
        self.assertTrue(jobs["bth"].enabled)
        self.assertIsNone(jobs["bth"].config.allowed_loan_type_names)
        self.assertEqual(jobs["bth"].config.renewal_window_rate, 0.1)
        self.assertFalse(jobs["bth"].config.include_fixed_policy)
        self.assertFalse(jobs["hh"].enabled)
        self.assertFalse(jobs["mdu"].enabled)

    def test_load_tenants_config_merges_defaults_and_overrides(self) -> None:
        content = """
[defaults]
renewal_window_rate = 0.1
include_fixed_policy = false
group_max_loan_days = { staff = 365, student = 180 }

[tenants.bth]
enabled = true

[tenants.hh]
enabled = true
renewal_window_rate = 0.25
allowed_loan_type_names = ["hemlan"]
group_max_loan_days = { staff = 365, student = 120 }
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tenants.toml"
            path.write_text(content, encoding="utf-8")
            jobs = load_tenants_config(path, dry_run=False)

        self.assertEqual(jobs["bth"].config.renewal_window_rate, 0.1)
        self.assertFalse(jobs["bth"].config.dry_run)
        self.assertEqual(jobs["hh"].config.renewal_window_rate, 0.25)
        self.assertEqual(jobs["hh"].config.allowed_loan_type_names, {"hemlan"})
        self.assertEqual(jobs["hh"].config.group_max_loan_days["student"], 120)

    def test_cli_resolve_jobs_single_multi_and_all_tenants(self) -> None:
        parser = build_parser()

        # Single tenant (backwards compatible)
        args_single = parser.parse_args(["--tenant", "bth"])
        jobs, multi = _resolve_jobs(args_single)
        self.assertFalse(multi)
        self.assertEqual([j.key for j in jobs], ["bth"])

        # Repeatable --tenant
        args_multi = parser.parse_args(["--tenant", "bth", "--tenant", "hh", "--execute"])
        jobs, multi = _resolve_jobs(args_multi)
        self.assertTrue(multi)
        self.assertEqual([j.key for j in jobs], ["bth", "hh"])
        self.assertTrue(all(not j.config.dry_run for j in jobs))

        # --all-tenants with MultiTenantCredentialProvider
        provider = MultiTenantCredentialProvider(
            Settings(), configured_tenants=("bth", "hh", "mdu")
        )
        args_all = parser.parse_args(["--all-tenants"])
        jobs, multi = _resolve_jobs(args_all, provider=provider)
        self.assertTrue(multi)
        self.assertEqual([j.key for j in jobs], ["bth", "hh", "mdu"])

        # --config tenants.toml with CLI override
        toml_path = Path(__file__).resolve().parents[1] / "tenants.toml"
        args_cfg = parser.parse_args(
            ["--config", str(toml_path), "--all-tenants", "--renewal-window-rate", "0.2"]
        )
        jobs, multi = _resolve_jobs(args_cfg)
        self.assertTrue(multi)
        self.assertEqual([j.key for j in jobs], ["bth"])
        self.assertEqual(jobs[0].config.renewal_window_rate, 0.2)

    async def test_run_for_tenants_isolates_errors(self) -> None:
        ok_summary = RenewalRunSummary(
            execution_id="exec-1",
            dry_run=True,
            started_at="2026-11-24T12:00:00+00:00",
            finished_at="2026-11-24T12:00:01+00:00",
            total_open_loans=2,
            eligible=1,
        )

        async def fake_run_auto_renewals(*, tenant_key=None, config=None, rule_engine=None, provider=None):
            if tenant_key == "hh":
                raise ValueError("Missing FOLIO credentials for tenant 'hh'")
            return ok_summary

        jobs = [
            TenantJobConfig(key="hh", enabled=True, config=RenewalConfig()),
            TenantJobConfig(key="bth", enabled=True, config=RenewalConfig()),
        ]
        with patch("loan_renewals.cli.run_auto_renewals", side_effect=fake_run_auto_renewals):
            results = await run_for_tenants(jobs)

        self.assertIsInstance(results["hh"], dict)
        self.assertEqual(results["hh"]["status"], "error")
        self.assertIn("Missing FOLIO credentials", results["hh"]["error"])
        self.assertIs(results["bth"], ok_summary)


if __name__ == "__main__":
    unittest.main()
