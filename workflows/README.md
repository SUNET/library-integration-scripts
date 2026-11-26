# Workflows

Kestra workflow definitions used to schedule, execute, and monitor library integrations.

Business logic stays inside each standalone project under `integrations/`; workflows handle orchestration, retries, secret injection, and execution report capture.

## Structure

```text
workflows/
├── circulation/
│   └── loan-renewals.yml   # Per-tenant auto-renewal orchestration
├── kestra-setup.md         # Installing and configuring Kestra
└── README.md
```

## Workflows

### `circulation/loan-renewals.yml`

- **Namespace / ID:** `library.circulation` / `loan-renewals`
- **Behavior:** Fans out across `inputs.tenants` using `ForEach` (`concurrencyLimit: 1`, `allowFailure: true`, exponential retry up to 3 attempts), runs `uv run loan-renewals` for each tenant, and saves `renewals_report_<tenant>.json` as a Kestra output file.
- **Safety defaults:** `execute: false` (dry-run) and schedule trigger `disabled: true` until verified in `test`.
- **Required Kestra secrets per tenant `<KEY>`:**
  - `FOLIO_USERNAME_<KEY>` (e.g. `FOLIO_USERNAME_BTH`)
  - `FOLIO_SECRET_<KEY>` (e.g. `FOLIO_SECRET_BTH`)

## Deploying Flows

See [`kestra-setup.md`](./kestra-setup.md) for Kestra installation, secret setup, and uploading flows via the UI or API.
