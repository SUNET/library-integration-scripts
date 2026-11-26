"""Auto-renewal CLI (dry-run by default; pass --execute to renew).

Examples:
    secretspec run -- uv run loan-renewals --tenant bth --json-output renewals_report.json
    secretspec run -- uv run loan-renewals --tenant bth --tenant hh --execute
    secretspec run -- uv run loan-renewals --config tenants.toml --all-tenants --execute
    secretspec run -- uv run loan-renewals --loan-type auto-renewal-test --renewal-window-rate 0.2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from folio_client import CredentialProvider, MultiTenantCredentialProvider

from loan_renewals.models import RenewalConfig, RenewalRunSummary
from loan_renewals.rules import RuleEngine, build_default_rule_engine
from loan_renewals.service import AutoRenewalService
from loan_renewals.tenants import TenantJobConfig, load_tenants_config
from loan_renewals.utils import get_client

logger = logging.getLogger(__name__)


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


def _parse_rate(value: str) -> float:
    try:
        rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid --renewal-window-rate '{value}': expected a float in [0.0, 1.0)"
        ) from exc
    if not (0.0 <= rate < 1.0):
        raise argparse.ArgumentTypeError(
            f"--renewal-window-rate must be in [0.0, 1.0), got {rate}"
        )
    return rate


def _parse_tenants(values: list[str] | None) -> list[str]:
    if not values:
        return []
    tenants: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            key = part.strip().lower()
            if key and key not in seen:
                seen.add(key)
                tenants.append(key)
    return tenants


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loan-renewals",
        description="Evaluate eligible open FOLIO loans and renew them automatically.",
    )
    parser.add_argument(
        "--tenant",
        action="append",
        dest="tenants",
        help="FOLIO tenant key (repeatable or comma-separated, e.g. --tenant bth --tenant hh).",
    )
    parser.add_argument(
        "--all-tenants",
        action="store_true",
        help="Run for all enabled tenants in --config, or all tenants in FOLIO_TENANTS env var.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to tenants.toml with per-tenant renewal rules.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform renewals in FOLIO (default is dry-run).",
    )
    parser.add_argument(
        "--renewal-window-rate",
        type=_parse_rate,
        default=None,
        help="Fraction of the loan period before dueDate to trigger renewal, in [0.0, 1.0) (default: 0.1 = 10%%).",
    )
    parser.add_argument(
        "--include-fixed-policy",
        action="store_true",
        default=None,
        help="Include loans under Fixed due-date loan policies (excluded and reported as skipped by default).",
    )
    parser.add_argument(
        "--include-overdue",
        action="store_true",
        default=None,
        help="Include loans that are already past their due date (excluded and reported as skipped by default).",
    )
    parser.add_argument(
        "--include-grace-period",
        action="store_true",
        default=None,
        help="Allow overdue loans still within their loan policy's loansPolicy.gracePeriod.",
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


def _apply_cli_overrides(
    base: RenewalConfig,
    args: argparse.Namespace,
) -> RenewalConfig:
    """Apply explicitly provided CLI flags on top of a base RenewalConfig."""
    parsed_limits = _parse_group_limits(args.group_limits)
    return RenewalConfig(
        dry_run=not args.execute,
        max_renewals_per_run=(
            args.max_renewals
            if args.max_renewals is not None
            else base.max_renewals_per_run
        ),
        page_size=base.page_size,
        max_retries=base.max_retries,
        retry_backoff_seconds=base.retry_backoff_seconds,
        renewal_window_rate=(
            args.renewal_window_rate
            if args.renewal_window_rate is not None
            else base.renewal_window_rate
        ),
        include_overdue=(
            True if args.include_overdue else base.include_overdue
        ),
        include_grace_period=(
            True if args.include_grace_period else base.include_grace_period
        ),
        include_fixed_policy=(
            True if args.include_fixed_policy else base.include_fixed_policy
        ),
        allowed_loan_type_ids=(
            set(args.loan_type_ids)
            if args.loan_type_ids
            else base.allowed_loan_type_ids
        ),
        allowed_loan_type_names=(
            set(args.loan_types)
            if args.loan_types
            else base.allowed_loan_type_names
        ),
        group_max_loan_days=(
            parsed_limits
            if parsed_limits is not None
            else dict(base.group_max_loan_days)
        ),
        default_max_loan_days=(
            args.default_max_days
            if args.default_max_days is not None
            else base.default_max_loan_days
        ),
    )


async def run_auto_renewals(
    *,
    tenant_key: str | None = None,
    config: RenewalConfig | None = None,
    rule_engine: RuleEngine | None = None,
    provider: CredentialProvider | None = None,
) -> RenewalRunSummary:
    """Create a FOLIO client via utils.get_client and execute one renewal pass."""
    cfg = config or RenewalConfig()
    engine = rule_engine or build_default_rule_engine(cfg)
    async with get_client(tenant_key, provider=provider) as client:
        service = AutoRenewalService(client, config=cfg, rule_engine=engine)
        return await service.run()


async def run_for_tenants(
    jobs: Sequence[TenantJobConfig],
    *,
    provider: CredentialProvider | None = None,
) -> dict[str, RenewalRunSummary | dict[str, str]]:
    """Run auto-renewals across multiple tenants with per-tenant error isolation."""
    results: dict[str, RenewalRunSummary | dict[str, str]] = {}
    for job in jobs:
        try:
            results[job.key] = await run_auto_renewals(
                tenant_key=job.key,
                config=job.config,
                provider=provider,
            )
        except Exception as exc:
            logger.exception("Auto-renewal failed for tenant '%s'", job.key)
            results[job.key] = {
                "status": "error",
                "tenant": job.key,
                "error": str(exc),
            }
    return results


def _resolve_jobs(
    args: argparse.Namespace,
    *,
    provider: MultiTenantCredentialProvider | None = None,
) -> tuple[list[TenantJobConfig], bool]:
    """Resolve the list of TenantJobConfig to run and whether output is multi-tenant."""
    requested_tenants = _parse_tenants(args.tenants)
    dry_run = not args.execute

    if args.config is not None:
        all_jobs = load_tenants_config(args.config, dry_run=dry_run)
        if requested_tenants:
            selected: list[TenantJobConfig] = []
            for key in requested_tenants:
                if key not in all_jobs:
                    raise ValueError(
                        f"Tenant '{key}' not found in {args.config} "
                        f"(available: {', '.join(sorted(all_jobs)) or 'none'})"
                    )
                job = all_jobs[key]
                selected.append(
                    TenantJobConfig(
                        key=job.key,
                        enabled=True,
                        config=_apply_cli_overrides(job.config, args),
                    )
                )
            return selected, len(selected) > 1 or args.all_tenants

        enabled_jobs = [
            TenantJobConfig(
                key=job.key,
                enabled=job.enabled,
                config=_apply_cli_overrides(job.config, args),
            )
            for job in all_jobs.values()
            if job.enabled
        ]
        if not enabled_jobs:
            raise ValueError(f"No enabled tenants found in {args.config}")
        return enabled_jobs, True

    base_config = _apply_cli_overrides(RenewalConfig(dry_run=dry_run), args)

    if args.all_tenants:
        mt_provider = provider or MultiTenantCredentialProvider()
        tenant_keys = mt_provider.list_tenants()
        if not tenant_keys:
            raise ValueError(
                "No tenants configured for --all-tenants. Set FOLIO_TENANTS, TENANT_NAME, or pass --config tenants.toml."
            )
        return [
            TenantJobConfig(key=key, enabled=True, config=base_config)
            for key in tenant_keys
        ], True

    if len(requested_tenants) > 1:
        return [
            TenantJobConfig(key=key, enabled=True, config=base_config)
            for key in requested_tenants
        ], True

    if len(requested_tenants) == 1:
        return [
            TenantJobConfig(key=requested_tenants[0], enabled=True, config=base_config)
        ], False

    # Backwards-compatible default: single tenant resolved from TENANT_NAME env var
    return [TenantJobConfig(key="", enabled=True, config=base_config)], False


def _serialize_results(
    results: dict[str, RenewalRunSummary | dict[str, str]],
) -> dict[str, Any]:
    return {
        tenant: (
            val.to_dict() if isinstance(val, RenewalRunSummary) else val
        )
        for tenant, val in results.items()
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    provider = MultiTenantCredentialProvider()
    jobs, multi_tenant_output = _resolve_jobs(args, provider=provider)

    if not multi_tenant_output:
        job = jobs[0]
        tenant_key = job.key or None
        summary = asyncio.run(
            run_auto_renewals(
                tenant_key=tenant_key,
                config=job.config,
                provider=provider,
            )
        )
        report_data: dict[str, Any] = summary.to_dict()
    else:
        results = asyncio.run(run_for_tenants(jobs, provider=provider))
        report_data = _serialize_results(results)

    report = json.dumps(report_data, indent=2)
    if args.json_output is not None:
        args.json_output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
