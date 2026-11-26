from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loan_renewals.models import RenewalConfig

_ALLOWED_CONFIG_KEYS = frozenset(
    {
        "renewal_window_rate",
        "include_fixed_policy",
        "include_overdue",
        "include_grace_period",
        "allowed_loan_type_names",
        "allowed_loan_type_ids",
        "group_max_loan_days",
        "default_max_loan_days",
        "max_renewals_per_run",
        "page_size",
        "max_retries",
        "retry_backoff_seconds",
    }
)


@dataclass(slots=True)
class TenantJobConfig:
    """Renewal job configuration for a single tenant."""

    key: str
    enabled: bool
    config: RenewalConfig


def _build_renewal_config(
    raw: dict[str, Any],
    *,
    dry_run: bool,
) -> RenewalConfig:
    unknown = set(raw.keys()) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"Unknown renewal config keys in tenants.toml: {', '.join(sorted(unknown))}"
        )

    kwargs: dict[str, Any] = {"dry_run": dry_run}
    for key in (
        "renewal_window_rate",
        "include_fixed_policy",
        "include_overdue",
        "include_grace_period",
        "default_max_loan_days",
        "max_renewals_per_run",
        "page_size",
        "max_retries",
        "retry_backoff_seconds",
    ):
        if key in raw and raw[key] is not None:
            kwargs[key] = raw[key]

    if "allowed_loan_type_names" in raw:
        names = [str(n).strip() for n in raw["allowed_loan_type_names"] if str(n).strip()]
        kwargs["allowed_loan_type_names"] = set(names) if names else None

    if "allowed_loan_type_ids" in raw:
        ids = [str(i).strip() for i in raw["allowed_loan_type_ids"] if str(i).strip()]
        kwargs["allowed_loan_type_ids"] = set(ids) if ids else None

    if "group_max_loan_days" in raw and raw["group_max_loan_days"] is not None:
        kwargs["group_max_loan_days"] = {
            str(k).strip().lower(): int(v)
            for k, v in raw["group_max_loan_days"].items()
        }

    return RenewalConfig(**kwargs)


def load_tenants_config(
    path: str | Path,
    *,
    dry_run: bool = True,
) -> dict[str, TenantJobConfig]:
    """Load per-tenant renewal configurations from a TOML file."""
    config_path = Path(path)
    with config_path.open("rb") as f:
        data = tomllib.load(f)

    defaults: dict[str, Any] = dict(data.get("defaults") or {})
    raw_tenants: dict[str, Any] = data.get("tenants") or {}

    jobs: dict[str, TenantJobConfig] = {}
    for raw_key, tenant_section in raw_tenants.items():
        if not isinstance(tenant_section, dict):
            raise ValueError(f"Invalid [tenants.{raw_key}] section in {config_path}")

        key = str(raw_key).strip().lower()
        section = dict(tenant_section)
        enabled = bool(section.pop("enabled", True))
        merged = {**defaults, **section}
        jobs[key] = TenantJobConfig(
            key=key,
            enabled=enabled,
            config=_build_renewal_config(merged, dry_run=dry_run),
        )

    return jobs
