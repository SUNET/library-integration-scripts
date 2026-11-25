from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

RenewalResult = Literal["renewed", "dry_run", "skipped", "failed"]


def parse_folio_datetime(value: str | datetime) -> datetime:
    """Parse a FOLIO ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo is not None
            else value.replace(tzinfo=timezone.utc)
        )
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class RuleDecision:
    """Outcome of evaluating a single renewal eligibility rule."""

    allowed: bool
    rule: str
    reason: str = ""


@dataclass(slots=True)
class RenewalContext:
    """All data needed by eligibility rules to evaluate a single loan."""

    loan: dict[str, Any]
    patron: dict[str, Any]
    patron_open_loans: list[dict[str, Any]]
    now: datetime
    patron_group_name: str | None = None
    loan_policy: dict[str, Any] | None = None
    loan_type_name: str | None = None


@dataclass(slots=True)
class RenewalConfig:
    """Runtime options for an auto-renewal execution."""

    dry_run: bool = True
    max_renewals_per_run: int | None = None
    page_size: int = 100
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    days_before_due: int = 0
    include_overdue: bool = True
    allowed_loan_type_ids: set[str] | None = None
    allowed_loan_type_names: set[str] | None = None
    group_max_loan_days: dict[str, int] = field(
        default_factory=lambda: {
            "staff": 365,
            "student": 180,
        }
    )
    default_max_loan_days: int | None = None


@dataclass(slots=True)
class RenewalAttempt:
    """Structured execution log record for a single loan evaluation/renewal."""

    execution_id: str
    timestamp: str
    loan_id: str
    item_id: str
    patron_id: str
    previous_due_date: str | None
    new_due_date: str | None
    result: RenewalResult
    reason: str
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RenewalRunSummary:
    """Aggregate summary of an auto-renewal execution."""

    execution_id: str
    dry_run: bool
    started_at: str
    finished_at: str
    total_open_loans: int = 0
    eligible: int = 0
    renewed: int = 0
    skipped: int = 0
    failed: int = 0
    attempts: list[RenewalAttempt] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_open_loans": self.total_open_loans,
            "eligible": self.eligible,
            "renewed": self.renewed,
            "skipped": self.skipped,
            "failed": self.failed,
            "attempts": [a.to_dict() for a in self.attempts],
        }
