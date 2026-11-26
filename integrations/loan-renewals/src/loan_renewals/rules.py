from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Protocol, runtime_checkable

from loan_renewals.models import (
    RenewalConfig,
    RenewalContext,
    RuleDecision,
    parse_folio_datetime,
)


@runtime_checkable
class RenewalRule(Protocol):
    """Contract for a pluggable auto-renewal eligibility rule."""

    name: str

    def evaluate(self, ctx: RenewalContext) -> RuleDecision: ...


def _period_to_timedelta(duration: int, interval_id: str) -> timedelta:
    interval = interval_id.strip().lower()
    if interval == "minutes":
        return timedelta(minutes=duration)
    if interval == "hours":
        return timedelta(hours=duration)
    if interval == "days":
        return timedelta(days=duration)
    if interval == "weeks":
        return timedelta(weeks=duration)
    if interval == "months":
        return timedelta(days=duration * 30)
    return timedelta(days=duration)


def _find_matching_schedule_entry(
    loan: dict[str, Any],
    fixed_due_date_schedule: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Find the schedule entry matching a loan's dueDate or loanDate."""
    if not fixed_due_date_schedule:
        return None
    schedules = fixed_due_date_schedule.get("schedules") or []
    if not schedules:
        return None

    raw_due = loan.get("dueDate")
    if raw_due:
        loan_due = parse_folio_datetime(raw_due)
        for entry in schedules:
            if entry.get("due") and parse_folio_datetime(entry["due"]) == loan_due:
                return entry

    raw_loan_date = loan.get("loanDate")
    if raw_loan_date:
        loan_date = parse_folio_datetime(raw_loan_date)
        for entry in schedules:
            if entry.get("from") and entry.get("to"):
                if (
                    parse_folio_datetime(entry["from"])
                    <= loan_date
                    <= parse_folio_datetime(entry["to"])
                ):
                    return entry

    return None


def calculate_loan_period_span(
    loan: dict[str, Any],
    loan_policy: dict[str, Any] | None,
    fixed_due_date_schedule: dict[str, Any] | None = None,
) -> timedelta:
    """Resolve the active loan period span for Rolling or Fixed policies."""
    if loan_policy:
        renewals_policy = loan_policy.get("renewalsPolicy") or {}
        loans_policy = loan_policy.get("loansPolicy") or {}
        period = renewals_policy.get("period") or loans_policy.get("period")
        if period and period.get("duration") is not None:
            return _period_to_timedelta(
                int(period["duration"]),
                str(period.get("intervalId", "Days")),
            )

        profile_id = str(loans_policy.get("profileId") or "").strip().lower()
        if profile_id == "fixed":
            entry = _find_matching_schedule_entry(loan, fixed_due_date_schedule)
            if entry and entry.get("from") and entry.get("due"):
                span = parse_folio_datetime(entry["due"]) - parse_folio_datetime(
                    entry["from"]
                )
                return max(span, timedelta(0))
            if loan.get("dueDate") and loan.get("loanDate"):
                span = parse_folio_datetime(loan["dueDate"]) - parse_folio_datetime(
                    loan["loanDate"]
                )
                return max(span, timedelta(0))

    return timedelta(0)


def calculate_grace_period(loan_policy: dict[str, Any] | None) -> timedelta:
    """Resolve loansPolicy.gracePeriod as a timedelta (defaults to 0)."""
    if not loan_policy:
        return timedelta(0)
    grace = (loan_policy.get("loansPolicy") or {}).get("gracePeriod")
    if not grace or grace.get("duration") is None:
        return timedelta(0)
    return max(
        _period_to_timedelta(
            int(grace["duration"]),
            str(grace.get("intervalId", "Days")),
        ),
        timedelta(0),
    )


def calculate_projected_due_date(
    loan: dict[str, Any],
    loan_policy: dict[str, Any] | None,
    now: datetime,
) -> datetime:
    """Estimate the due date after one renewal using the FOLIO loan policy."""
    if not loan_policy:
        return now

    renewals_policy = loan_policy.get("renewalsPolicy") or {}
    period = renewals_policy.get("period") or (
        (loan_policy.get("loansPolicy") or {}).get("period")
    )
    if not period:
        return now

    duration = int(period.get("duration", 0))
    interval_id = str(period.get("intervalId", "Days"))
    delta = _period_to_timedelta(duration, interval_id)

    renew_from = str(renewals_policy.get("renewFromId", "SYSTEM_DATE")).upper()
    if renew_from == "CURRENT_DUE_DATE" and loan.get("dueDate"):
        base = parse_folio_datetime(loan["dueDate"])
    else:
        base = now
    return base + delta


class OpenLoanRule:
    """Require the loan status to be Open."""

    name = "open_loan"

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        status_name = (ctx.loan.get("status") or {}).get("name")
        if status_name != "Open":
            return RuleDecision(
                False, self.name, f"Loan status is '{status_name}', expected 'Open'"
            )
        return RuleDecision(True, self.name)


class LoanPolicyProfileRule:
    """Control whether Fixed-profile loan policies are eligible for auto-renewal."""

    name = "loan_policy_profile"

    def __init__(self, *, include_fixed: bool = False) -> None:
        self.include_fixed = include_fixed

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        if self.include_fixed or not ctx.loan_policy:
            return RuleDecision(True, self.name)

        loans_policy = ctx.loan_policy.get("loansPolicy") or {}
        profile_id = str(loans_policy.get("profileId") or "").strip().lower()
        if profile_id == "fixed":
            policy_name = ctx.loan_policy.get("name") or ctx.loan_policy.get("id")
            return RuleDecision(
                False,
                self.name,
                f"Fixed loan policy '{policy_name}' is excluded (enable --include-fixed-policy to include)",
            )
        return RuleDecision(True, self.name)


class LoanTypeFilterRule:
    """Restrict auto-renewal to specific loan type IDs and/or names."""

    name = "loan_type_filter"

    def __init__(
        self,
        allowed_ids: Iterable[str] | None = None,
        allowed_names: Iterable[str] | None = None,
    ) -> None:
        self.allowed_ids = {i.strip() for i in allowed_ids} if allowed_ids else set()
        self.allowed_names = (
            {n.strip().lower() for n in allowed_names} if allowed_names else set()
        )

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        if not self.allowed_ids and not self.allowed_names:
            return RuleDecision(True, self.name)

        item = ctx.loan.get("item") or {}
        loan_type_id = (
            ctx.loan.get("loanTypeId")
            or item.get("temporaryLoanTypeId")
            or item.get("permanentLoanTypeId")
        )
        loan_type_name = (
            ctx.loan_type_name
            or (item.get("temporaryLoanType") or {}).get("name")
            or (item.get("permanentLoanType") or {}).get("name")
        )

        if loan_type_id and loan_type_id in self.allowed_ids:
            return RuleDecision(True, self.name)
        if loan_type_name and loan_type_name.strip().lower() in self.allowed_names:
            return RuleDecision(True, self.name)

        return RuleDecision(
            False,
            self.name,
            f"Loan type '{loan_type_name or loan_type_id}' is not in allowed auto-renewal loan types",
        )


class DueWindowRule:
    """Require the loan to be within window_rate * loan_period before its dueDate.

    By default, overdue loans (dueDate < now) are excluded so loans that may
    already have triggered overdue fees/notices are not auto-renewed.
    Set ``include_grace_period=True`` to allow loans still within
    ``dueDate + loansPolicy.gracePeriod``, or ``include_overdue=True`` to
    include all overdue loans.
    """

    name = "due_window"

    def __init__(
        self,
        *,
        window_rate: float = 0.1,
        include_overdue: bool = False,
        include_grace_period: bool = False,
        fallback_window: timedelta = timedelta(0),
    ) -> None:
        if not (0.0 <= window_rate < 1.0):
            raise ValueError(
                f"window_rate must be in [0.0, 1.0), got {window_rate}"
            )
        self.window_rate = window_rate
        self.include_overdue = include_overdue
        self.include_grace_period = include_grace_period
        self.fallback_window = fallback_window

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        raw_due = ctx.loan.get("dueDate")
        if not raw_due:
            return RuleDecision(False, self.name, "Loan has no dueDate")

        due_date = parse_folio_datetime(raw_due)
        period_span = calculate_loan_period_span(
            ctx.loan, ctx.loan_policy, ctx.fixed_due_date_schedule
        )
        window = (
            period_span * self.window_rate
            if period_span > timedelta(0)
            else self.fallback_window
        )
        cutoff = ctx.now + window

        if due_date > cutoff:
            return RuleDecision(
                False,
                self.name,
                (
                    f"Loan dueDate {due_date.isoformat()} is after renewal window "
                    f"cutoff {cutoff.isoformat()} (rate={self.window_rate}, window={window})"
                ),
            )

        if due_date < ctx.now and not self.include_overdue:
            if self.include_grace_period:
                grace = calculate_grace_period(ctx.loan_policy)
                grace_deadline = due_date + grace
                if grace > timedelta(0) and grace_deadline >= ctx.now:
                    return RuleDecision(True, self.name)
                if grace > timedelta(0):
                    return RuleDecision(
                        False,
                        self.name,
                        (
                            f"Loan is overdue (dueDate {due_date.isoformat()}) and past "
                            f"grace period ({grace}, expired {grace_deadline.isoformat()}); "
                            "overdue loans are excluded by default (pass --include-overdue to include)"
                        ),
                    )
                return RuleDecision(
                    False,
                    self.name,
                    (
                        f"Loan is overdue (dueDate {due_date.isoformat()}) and policy has no gracePeriod; "
                        "overdue loans are excluded by default (pass --include-overdue to include)"
                    ),
                )
            return RuleDecision(
                False,
                self.name,
                (
                    f"Loan is overdue (dueDate {due_date.isoformat()}); "
                    "overdue loans are excluded by default (pass --include-overdue to include)"
                ),
            )

        return RuleDecision(True, self.name)


class ActivePatronRule:
    """Require the patron account to be active."""

    name = "active_patron"

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        if ctx.patron.get("active") is not True:
            return RuleDecision(False, self.name, "Patron is not active")
        return RuleDecision(True, self.name)


class NoOtherOverdueLoansRule:
    """Reject renewal if the patron has any other overdue open loan."""

    name = "no_other_overdue_loans"

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        current_loan_id = ctx.loan.get("id")
        for other in ctx.patron_open_loans:
            if other.get("id") == current_loan_id:
                continue
            other_due = other.get("dueDate")
            if other_due and parse_folio_datetime(other_due) < ctx.now:
                return RuleDecision(
                    False,
                    self.name,
                    f"Patron has another overdue loan ({other.get('id')})",
                )
        return RuleDecision(True, self.name)


class RenewalLimitRule:
    """Respect FOLIO loan policy renewalability and maximum renewal count."""

    name = "renewal_limit"

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        policy = ctx.loan_policy
        if not policy:
            return RuleDecision(True, self.name)

        if policy.get("renewable") is False:
            return RuleDecision(
                False, self.name, "Loan policy does not allow renewals"
            )

        renewals_policy = policy.get("renewalsPolicy") or {}
        if renewals_policy.get("unlimited") is True:
            return RuleDecision(True, self.name)

        number_allowed = renewals_policy.get("numberAllowed")
        if number_allowed is not None:
            current_count = int(ctx.loan.get("renewalCount") or 0)
            if current_count >= int(number_allowed):
                return RuleDecision(
                    False,
                    self.name,
                    f"Renewal count {current_count} reached policy limit {number_allowed}",
                )
        return RuleDecision(True, self.name)


class MaxLoanPeriodRule:
    """Enforce a maximum total loan duration from original loanDate per patron group."""

    name = "max_loan_period"

    def __init__(
        self,
        group_max_days: dict[str, int],
        *,
        default_max_days: int | None = None,
    ) -> None:
        self.group_max_days = {k.strip().lower(): v for k, v in group_max_days.items()}
        self.default_max_days = default_max_days

    def _resolve_max_days(self, ctx: RenewalContext) -> int | None:
        group_id = str(ctx.patron.get("patronGroup") or "").strip().lower()
        group_name = (ctx.patron_group_name or "").strip().lower()

        if group_id in self.group_max_days:
            return self.group_max_days[group_id]
        if group_name in self.group_max_days:
            return self.group_max_days[group_name]
        return self.default_max_days

    def evaluate(self, ctx: RenewalContext) -> RuleDecision:
        max_days = self._resolve_max_days(ctx)
        if max_days is None:
            group_label = ctx.patron_group_name or ctx.patron.get("patronGroup")
            return RuleDecision(
                False,
                self.name,
                f"Patron group '{group_label}' is not eligible for auto-renewal",
            )

        raw_loan_date = ctx.loan.get("loanDate")
        if not raw_loan_date:
            return RuleDecision(False, self.name, "Loan has no loanDate")

        loan_date = parse_folio_datetime(raw_loan_date)
        max_due_date = loan_date + timedelta(days=max_days)
        projected_due_date = calculate_projected_due_date(
            ctx.loan, ctx.loan_policy, ctx.now
        )

        if projected_due_date > max_due_date:
            return RuleDecision(
                False,
                self.name,
                (
                    f"Projected due date {projected_due_date.isoformat()} exceeds "
                    f"max loan horizon {max_due_date.isoformat()} ({max_days} days)"
                ),
            )
        return RuleDecision(True, self.name)


class RuleEngine:
    """Ordered, mutable collection of RenewalRule objects."""

    def __init__(self, rules: Iterable[RenewalRule] = ()) -> None:
        self._rules: list[RenewalRule] = list(rules)

    @property
    def rules(self) -> tuple[RenewalRule, ...]:
        return tuple(self._rules)

    def add(self, rule: RenewalRule, *, index: int | None = None) -> RuleEngine:
        if index is None:
            self._rules.append(rule)
        else:
            self._rules.insert(index, rule)
        return self

    def remove(self, rule_name: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != rule_name]
        return len(self._rules) < before

    def replace(self, rule_name: str, new_rule: RenewalRule) -> bool:
        for idx, rule in enumerate(self._rules):
            if rule.name == rule_name:
                self._rules[idx] = new_rule
                return True
        return False

    def evaluate(
        self,
        ctx: RenewalContext,
        *,
        fail_fast: bool = True,
    ) -> tuple[bool, list[RuleDecision]]:
        decisions: list[RuleDecision] = []
        all_allowed = True
        for rule in self._rules:
            decision = rule.evaluate(ctx)
            decisions.append(decision)
            if not decision.allowed:
                all_allowed = False
                if fail_fast:
                    return False, decisions
        return all_allowed, decisions


def build_default_rule_engine(config: RenewalConfig | None = None) -> RuleEngine:
    """Construct the standard auto-renewal rule pipeline from RenewalConfig."""
    cfg = config or RenewalConfig()
    engine = RuleEngine(
        [
            OpenLoanRule(),
            LoanPolicyProfileRule(include_fixed=cfg.include_fixed_policy),
            DueWindowRule(
                window_rate=cfg.renewal_window_rate,
                include_overdue=cfg.include_overdue,
                include_grace_period=cfg.include_grace_period,
            ),
            ActivePatronRule(),
            NoOtherOverdueLoansRule(),
            RenewalLimitRule(),
            MaxLoanPeriodRule(
                cfg.group_max_loan_days,
                default_max_days=cfg.default_max_loan_days,
            ),
        ]
    )
    if cfg.allowed_loan_type_ids or cfg.allowed_loan_type_names:
        engine.add(
            LoanTypeFilterRule(
                allowed_ids=cfg.allowed_loan_type_ids,
                allowed_names=cfg.allowed_loan_type_names,
            ),
            index=1,
        )
    return engine
