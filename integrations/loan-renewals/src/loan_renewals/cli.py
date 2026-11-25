from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Sequence

from loan_renewals.models import RenewalConfig, RenewalRunSummary
from loan_renewals.rules import RuleEngine, build_default_rule_engine
from loan_renewals.service import AutoRenewalService
from loan_renewals.utils import get_client


def _parse_group_limits(values: list[str] | None) -> dict[str, int] | None:
    if not values:
        return None
    limits: dict[str, int] = {}
    for item in values:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"Invalid --group-limit '{item}'. Expected format: group_name=days (e.g. staff=365)"
            )
        group, raw_days = item.split("=", 1)
        limits[group.strip().lower()] = int(raw_days.strip())
    return limits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loan-renewals",
        description="Evaluate eligible open FOLIO loans and renew them automatically.",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="FOLIO tenant key (defaults to configured FOLIO_TENANT_NAME / bth).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform renewals in FOLIO (default is dry-run).",
    )
    parser.add_argument(
        "--days-before-due",
        type=int,
        default=0,
        help="Renew loans due within this many days from now (default: 0 = due now or overdue).",
    )
    parser.add_argument(
        "--exclude-overdue",
        action="store_true",
        help="Skip loans that are already past their due date.",
    )
    parser.add_argument(
        "--loan-type",
        action="append",
        dest="loan_types",
        help="Only renew loans with this loan type name (repeatable).",
    )
    parser.add_argument(
        "--loan-type-id",
        action="append",
        dest="loan_type_ids",
        help="Only renew loans with this loan type UUID (repeatable).",
    )
    parser.add_argument(
        "--group-limit",
        action="append",
        dest="group_limits",
        help="Override max loan days for a patron group, e.g. --group-limit staff=365 --group-limit student=180.",
    )
    parser.add_argument(
        "--default-max-days",
        type=int,
        default=None,
        help="Fallback max loan days for patron groups not listed in --group-limit.",
    )
    parser.add_argument(
        "--max-renewals",
        type=int,
        default=None,
        help="Maximum number of loans to renew in a single execution.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write the full JSON execution report.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser


async def run_auto_renewals(
    *,
    tenant_key: str | None = None,
    config: RenewalConfig | None = None,
    rule_engine: RuleEngine | None = None,
) -> RenewalRunSummary:
    """Create a FOLIO client via utils.get_client and execute one renewal pass."""
    cfg = config or RenewalConfig()
    engine = rule_engine or build_default_rule_engine(cfg)
    async with get_client(tenant_key) as client:
        service = AutoRenewalService(client, config=cfg, rule_engine=engine)
        return await service.run()


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = RenewalConfig(
        dry_run=not args.execute,
        max_renewals_per_run=args.max_renewals,
        days_before_due=args.days_before_due,
        include_overdue=not args.exclude_overdue,
        allowed_loan_type_ids=set(args.loan_type_ids) if args.loan_type_ids else None,
        allowed_loan_type_names=set(args.loan_types) if args.loan_types else None,
        default_max_loan_days=args.default_max_days,
    )
    parsed_limits = _parse_group_limits(args.group_limits)
    if parsed_limits is not None:
        config.group_max_loan_days = parsed_limits

    summary = asyncio.run(run_auto_renewals(tenant_key=args.tenant, config=config))

    report = json.dumps(summary.to_dict(), indent=2)
    if args.json_output is not None:
        args.json_output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
