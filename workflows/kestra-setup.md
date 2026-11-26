# Kestra Setup Guide

How to install Kestra locally (or on a server) with Docker Compose, bootstrap admin authentication non-interactively, configure FOLIO secrets, and bulk-load workflow YAML files.

## Prerequisites

- Docker & Docker Compose v2 (`docker compose version`)
- `curl` (optional, for importing flows via API)

## 1. Configure `.env`

Create `workflows/.env` (gitignored — never commit secrets to Git):

```bash
# Postgres
POSTGRES_DB=kestra
POSTGRES_USER=kestra
POSTGRES_PASSWORD=<strong-db-password>

# Kestra (bootstraps admin user non-interactively and skips /ui/setup)
KESTRA_PORT=8080
KESTRA_URL=http://localhost:8080/
KESTRA_BASIC_AUTH_USERNAME=admin@example.com
KESTRA_BASIC_AUTH_PASSWORD=<8+-chars-with-upper-lower-number>

# Kestra Open Source secrets (base64-encoded values, read via {{ secret('KEY') }})
# Encode with: printf '%s' 'value' | base64 -w 0
SECRET_FOLIO_USERNAME_BTH=<base64-encoded-username>
SECRET_FOLIO_SECRET_BTH=<base64-encoded-secret>

# Add additional tenants the same way:
# SECRET_FOLIO_USERNAME_HH=<base64-encoded-username>
# SECRET_FOLIO_SECRET_HH=<base64-encoded-secret>
```

### Non-interactive Admin Setup (Servers / CI)

`workflows/docker-compose.yml` configures basic auth from `.env` so Kestra initializes automatically without needing to visit `/ui/setup`:

```yaml
kestra:
  server:
    basic-auth:
      enabled: true
      username: ${KESTRA_BASIC_AUTH_USERNAME:?KESTRA_BASIC_AUTH_USERNAME must be set in .env}
      password: ${KESTRA_BASIC_AUTH_PASSWORD:?KESTRA_BASIC_AUTH_PASSWORD must be set in .env}
```

Requirements:
- `username` must be a valid email address.
- `password` must be at least 8 characters with uppercase, lowercase, and a number.
- No default passwords are set in `docker-compose.yml`; Docker Compose fails fast if `POSTGRES_PASSWORD`, `KESTRA_BASIC_AUTH_USERNAME`, or `KESTRA_BASIC_AUTH_PASSWORD` is missing.

## 2. Launch Kestra

From `workflows/`:

```bash
docker compose up -d
```

Open the Kestra UI at `http://localhost:8080` and sign in with `KESTRA_BASIC_AUTH_USERNAME` / `KESTRA_BASIC_AUTH_PASSWORD`.

> **Notes:**
> - Mounting `/var/run/docker.sock` and `/tmp/kestra-wd` (with `user: "root"` in local dev) allows Kestra's `Docker` task runner to launch `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` containers for each integration task.
> - Port `8081` (management/actuator) is intentionally not exposed because it is unauthenticated by default.

## 3. Loading Multiple Flow YAML Files (Bulk Import)

When managing many workflow YAML files, avoid adding them one by one in the UI. Use one of these approaches:

### Option A: Mount flows + Kestra CLI sync (recommended for Docker Compose / servers)

Mount your workflow folder into the Kestra container in `docker-compose.yml`:

```yaml
command: server standalone --flow-path /app/flows
volumes:
  - ./circulation:/app/flows/library.circulation:ro
```

Then sync all YAML files in that directory at once (creates new flows and updates existing ones; add `--delete` to remove deleted files):

```bash
docker compose exec kestra /app/kestra flow namespace update library.circulation /app/flows/library.circulation \
  --server http://localhost:8080 \
  --user "${KESTRA_BASIC_AUTH_USERNAME}:${KESTRA_BASIC_AUTH_PASSWORD}"
```

### Option B: Git Sync (`io.kestra.plugin.git.SyncFlows`, best for GitOps)

Create a single bootstrap flow in Kestra that pulls all YAML files from Git on a schedule or webhook:

```yaml
id: sync-flows-from-git
namespace: system

tasks:
  - id: sync_circulation
    type: io.kestra.plugin.git.SyncFlows
    url: https://github.com/SUNET/library-integration-scripts
    branch: main
    gitDirectory: workflows/circulation
    targetNamespace: library.circulation
    delete: false
```

### Option C: Bulk import via REST API (CI/CD)

```bash
for file in workflows/circulation/*.yml; do
  curl -sf -X POST \
    -u "${KESTRA_BASIC_AUTH_USERNAME}:${KESTRA_BASIC_AUTH_PASSWORD}" \
    -H "Content-Type: application/x-yaml" \
    --data-binary @"$file" \
    "http://localhost:8080/api/v1/flows/import"
done
```

### Option D: Automatic sync on startup with a `kestra-init` container

Add a healthcheck to `kestra` and a one-shot `kestra-init` service in `docker-compose.yml` that waits for Kestra to be healthy, syncs every mounted namespace folder under `/app/flows/*`, and exits:

```yaml
  kestra:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/api/v1/configs || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 15

  kestra-init:
    image: kestra/kestra:v2.0.13
    depends_on:
      kestra:
        condition: service_healthy
    restart: "no"
    env_file:
      - .env
    volumes:
      - ./circulation:/app/flows/library.circulation:ro
      # Add more namespaces as needed:
      # - ./catalog:/app/flows/library.catalog:ro
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        set -e
        for dir in /app/flows/*; do
          ns="$(basename "$dir")"
          echo "Syncing namespace $ns from $dir"
          /app/kestra flow namespace update "$ns" "$dir" \
            --server http://kestra:8080 \
            --user "${KESTRA_BASIC_AUTH_USERNAME}:${KESTRA_BASIC_AUTH_PASSWORD}"
        done
```

On a fresh server, `docker compose up -d` starts Postgres, boots Kestra with basic auth, and automatically imports all flows. To re-sync after editing YAML files, run:

```bash
docker compose run --rm kestra-init
```

*(Add `--delete` to `flow namespace update` if you also want flows removed from Kestra when their `.yml` file is deleted.)*

## 4. Test & Enable Scheduling

1. Open **`library.circulation` → `loan-renewals`** in the Kestra UI and click **Execute**.
2. Leave `execute = false` on the first run to verify auth, config, and eligible loans in dry-run mode.
3. Download `renewals_report_<tenant>.json` from the **Outputs** tab.
4. Once verified, enable the `scheduled_renewals` trigger by setting `disabled: false` (or removing `disabled`) with your desired `cron` schedule.

> **Scheduling reminder:** Because overdue loans are excluded by default, ensure `job_interval < smallest_target_period * renewal_window_rate` (see `integrations/loan-renewals/README.md`).
