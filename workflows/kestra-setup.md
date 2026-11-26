# Kestra Setup Guide

How to install Kestra locally (or on a VM) with Docker Compose, configure FOLIO secrets, and deploy the workflows from this repository.

## Prerequisites

- Docker & Docker Compose v2 (`docker compose version`)
- `curl` (optional, for importing flows via API)

## 1. Start Kestra with Docker Compose

Create a working directory (e.g. `~/kestra`) with a `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: kestra
      POSTGRES_USER: kestra
      POSTGRES_PASSWORD: kestra_dev_password
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -d kestra -U kestra"]
      interval: 10s
      timeout: 5s
      retries: 5

  kestra:
    image: kestra/kestra:latest
    restart: unless-stopped
    user: root
    command: server standalone
    ports:
      - "8080:8080"
      - "8081:8081"
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      KESTRA_CONFIGURATION: |
        datasources:
          postgres:
            url: jdbc:postgresql://postgres:5432/kestra
            driverClassName: org.postgresql.Driver
            username: kestra
            password: kestra_dev_password
        kestra:
          server:
            basicAuth:
              enabled: true
              username: admin@example.com
              password: ChangeMe123!
          repository:
            type: postgres
          storage:
            type: local
            local:
              basePath: /app/storage
          queue:
            type: postgres
          tasks:
            tmpDir:
              path: /tmp/kestra-wd/tmp
          url: http://localhost:8080/
    volumes:
      - kestra-data:/app/storage
      - /var/run/docker.sock:/var/run/docker.sock
      - /tmp/kestra-wd:/tmp/kestra-wd

volumes:
  postgres-data:
  kestra-data:
```

> **Note:** Mounting `/var/run/docker.sock` and `/tmp/kestra-wd` is required so Kestra's `Docker` task runner can launch `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` containers for each integration task.

## 2. Configure Secrets (Open Source Edition)

In Kestra Open Source, `{{ secret('KEY') }}` reads base64-encoded `SECRET_<KEY>` environment variables from the Kestra container's `.env` file.

Create `.env` in your Kestra directory:

```bash
# Encode each secret value with base64 (no newline):
#   printf '%s' 'sidecar-module-access-client' | base64 -w 0
#   printf '%s' '<your-bth-secret>' | base64 -w 0

SECRET_FOLIO_USERNAME_BTH=<base64-encoded-username>
SECRET_FOLIO_SECRET_BTH=<base64-encoded-secret>

# Add additional tenants the same way:
# SECRET_FOLIO_USERNAME_HH=<base64-encoded-username>
# SECRET_FOLIO_SECRET_HH=<base64-encoded-secret>
```

> Never commit `.env` to Git. In Kestra Enterprise/Cloud, you can manage these secrets directly in the UI or connect HashiCorp Vault / AWS Secrets Manager instead.

## 3. Launch Kestra

```bash
docker compose up -d
```

Open the Kestra UI at `http://localhost:8080` and sign in with the `basicAuth` credentials configured above.

## 4. Import the `loan-renewals` Flow

### Option A: Kestra UI
1. Go to **Flows** → **Create**.
2. Paste the contents of `workflows/circulation/loan-renewals.yml`.
3. Click **Save**.

### Option B: Kestra API (`curl`)
```bash
curl -X POST \
  -u "admin@example.com:ChangeMe123!" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @workflows/circulation/loan-renewals.yml \
  "http://localhost:8080/api/v1/flows"
```

To update an existing flow, use `PUT http://localhost:8080/api/v1/flows/library.circulation/loan-renewals`.

## 5. Test & Enable Scheduling

1. In the Kestra UI, open **`library.circulation` → `loan-renewals`** and click **Execute**.
2. Leave `execute = false` for your first run to verify authentication, tenant config, and eligible loans in dry-run mode.
3. Inspect the **Outputs** tab to download `renewals_report_bth.json`.
4. Once verified, run with `execute = true` on `test`, and then enable the `scheduled_renewals` trigger (`disabled: false`) with your desired `cron` schedule.

> **Scheduling reminder:** Because overdue loans are excluded by default, ensure `job_interval < smallest_target_period * renewal_window_rate` (see `integrations/loan-renewals/README.md`).
