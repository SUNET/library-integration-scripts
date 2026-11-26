# Loan Renewals

Standalone integration that evaluates open FOLIO loans against configurable rules and renews eligible loans via FOLIO's `POST /circulation/renew-by-id` endpoint. By default, it runs in **dry-run** mode and outputs a JSON report. Pass `--execute` to perform actual renewals.

## Setup

```bash
cd integrations/loan-renewals
uv sync
```

## Authentication Environment Variables

Credentials are resolved per tenant `<KEY>` (e.g. `BTH`, `HH`, `MDU`) by `folio-client`:

```bash
ENVIRONMENT=test
FOLIO_TENANTS=bth,hh,mdu                          # optional, used by --all-tenants without --config

FOLIO_TENANT_BTH=bth
FOLIO_REALM_BTH=bth
FOLIO_USERNAME_BTH=sidecar-module-access-client   # falls back to FOLIO_TENANT_USERNAME
FOLIO_SECRET_BTH=<secret>                         # falls back to FOLIO_TENANT_SECRET
FOLIO_ENVIRONMENT_BTH=test                        # optional, falls back to ENVIRONMENT
```

See `packages/folio-client/README.md` for full credential details.

## CLI Usage

### Single tenant

```bash
# Dry-run for bth (writes report to renewals_report.json)
uv run loan-renewals --tenant bth --json-output renewals_report.json

# Execute renewals for bth
uv run loan-renewals --tenant bth --execute

# Filter by loan type and adjust renewal window (20% of loan period before dueDate)
uv run loan-renewals --tenant bth --loan-type auto-renewal-test --renewal-window-rate 0.2 --execute
```

### Multiple tenants

```bash
# Explicit list of tenants
uv run loan-renewals --tenant bth --tenant hh --execute

# All enabled tenants from tenants.toml
uv run loan-renewals --config tenants.toml --all-tenants --execute

# Single tenant using its rules from tenants.toml
uv run loan-renewals --config tenants.toml --tenant bth --execute

# All tenants from FOLIO_TENANTS env var (without tenants.toml)
uv run loan-renewals --all-tenants --execute
```

### Important flags

| Flag | Default | Description |
| --- | --- | --- |
| `--tenant KEY` | `TENANT_NAME` | Target tenant key (repeatable or comma-separated) |
| `--all-tenants` | `false` | Run all enabled tenants in `--config`, or all in `FOLIO_TENANTS` |
| `--config PATH` | none | Path to `tenants.toml` with per-tenant renewal rules |
| `--execute` | `false` | Perform renewals in FOLIO (omit for dry-run) |
| `--renewal-window-rate` | `0.1` | Fraction of loan period before `dueDate` to renew (`[0.0, 1.0)`) |
| `--include-fixed-policy` | `false` | Include Fixed due-date policies (skipped by default) |
| `--include-overdue` | `false` | Include loans past `dueDate` (excluded and reported as skipped by default) |
| `--include-grace-period` | `false` | Allow overdue loans still within `loansPolicy.gracePeriod` (`dueDate + gracePeriod >= now`) |
| `--loan-type NAME` | all | Restrict to loan type name (repeatable) |
| `--group-limit G=DAYS` | `staff=365`, `student=180` | Max total loan days from `loanDate` per patron group |
| `--max-renewals N` | unlimited | Cap renewals per execution |
| `--json-output PATH` | stdout | Write full JSON execution report to file |

CLI flags always override values loaded from `tenants.toml`.

## Configuration (`tenants.toml`)

`tenants.toml` defines default and per-tenant renewal rules (no secrets):

```toml
[defaults]
renewal_window_rate = 0.1
include_fixed_policy = false
include_overdue = false
include_grace_period = false
allowed_loan_type_names = []
group_max_loan_days = { staff = 365, student = 180 }

[tenants.bth]
enabled = true

[tenants.hh]
enabled = false
```

## Scheduling & Renewal Window Timing

By default, overdue loans are excluded (`now <= dueDate <= now + period * rate`) so loans that may already have triggered overdue fee policies are not auto-renewed.

Because of this, the job schedule interval and `renewal_window_rate` must satisfy:

$$\text{job\_interval} < \text{period} \times \text{rate}$$

If the job runs less frequently than the renewal window, a loan can slip past `dueDate` between two runs and become ineligible (overdue).

Examples at `renewal_window_rate = 0.1`:

| Loan period | 10% window | Safe schedule interval |
| --- | --- | --- |
| 21 days | 2.1 days (~50h) | Daily (24h) — 2 chances before due |
| 7 days | 16.8 hours | Every 6–12 hours |
| 1 day | 2.4 hours | Every 1 hour |
| 1 hour (test) | 6 minutes | Every 2–3 minutes |

If you want a safety net for missed job runs without touching fined loans, enable `--include-grace-period` (allows `dueDate + loansPolicy.gracePeriod >= now`).

## Tests

```bash
uv run python -m unittest discover -s tests -v
```
