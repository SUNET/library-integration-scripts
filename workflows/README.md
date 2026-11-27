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
- **Behavior:** Fans out across `inputs.tenants` using `ForEach` (`concurrencyLimit: 1`, `allowFailure: true`, exponential retry up to 3 attempts), runs `loan-renewals` inside the pre-built Docker image (`inputs.image`, default `ghcr.io/sunet/loan-renewals:0.1.0`), and captures `reports/renewals_report_<tenant>.json` as Kestra output artifacts.
- **Build image (from repository root):**

  ```bash
  docker build -f integrations/loan-renewals/Dockerfile -t loan-renewals:0.1.0 .
  ```

- **Safety defaults:** `execute: false` (dry-run) and schedule trigger `disabled: true` until verified in `test`.
- **Required Kestra secrets per tenant `<KEY>`:**
  - `FOLIO_TENANT_<KEY>` (e.g. `FOLIO_TENANT_BTH`)
  - `FOLIO_REALM_<KEY>` (e.g. `FOLIO_REALM_BTH`)
  - `FOLIO_USERNAME_<KEY>` (e.g. `FOLIO_USERNAME_BTH`)
  - `FOLIO_SECRET_<KEY>` (e.g. `FOLIO_SECRET_BTH`)

## Deploying Flows

See [`kestra-setup.md`](./kestra-setup.md) for Kestra installation, secret setup, and uploading flows via the UI or API.
