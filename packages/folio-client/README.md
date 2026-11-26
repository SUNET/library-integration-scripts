# folio-client

Reusable FOLIO API client package with cached Keycloak (`client_credentials`) authentication and multi-tenant support.

## Environment Variables

### Shared defaults (optional fallbacks)

```bash
ENVIRONMENT=test                                    # default for all tenants ("local" maps to "test")
FOLIO_TENANTS=bth,hh,mdu                            # comma-separated list for MultiTenantCredentialProvider
FOLIO_TENANT_USERNAME=sidecar-module-access-client  # shared fallback if username is the same across tenants
```

### Per-tenant variables

For each tenant `<KEY>` (e.g. `BTH`, `HH`, `MDU`):

```bash
# Tenant: bth
FOLIO_TENANT_BTH=bth
FOLIO_REALM_BTH=bth
FOLIO_USERNAME_BTH=sidecar-module-access-client     # optional if FOLIO_TENANT_USERNAME is set
FOLIO_SECRET_BTH=<secret>
FOLIO_ENVIRONMENT_BTH=test                          # optional, falls back to ENVIRONMENT

# Tenant: hh
FOLIO_TENANT_HH=hh
FOLIO_REALM_HH=hh
FOLIO_USERNAME_HH=sidecar-module-access-client
FOLIO_SECRET_HH=<secret>
FOLIO_ENVIRONMENT_HH=prod                           # per-tenant override
```

Single-tenant variables (`TENANT_NAME`, `FOLIO_TENANT`, `FOLIO_TENANT_REALM`, `FOLIO_TENANT_USERNAME`, `FOLIO_TENANT_SECRET`) continue to work as fallbacks.

## Usage

### Single tenant

```python
from folio_client import get_folio_client

async with get_folio_client("bth") as client:
    response = await client.get("circulation/loans", params={"limit": 10})
    loans = response.json()
```

### Multiple tenants

```python
from folio_client import MultiTenantCredentialProvider, get_folio_client

# Reads FOLIO_TENANTS env var, or pass configured_tenants=("bth", "hh", "mdu")
provider = MultiTenantCredentialProvider()

for tenant_key in provider.list_tenants():
    try:
        async with get_folio_client(tenant_key, provider=provider) as client:
            response = await client.get("circulation/loans", params={"limit": 10})
            ...
    except Exception as exc:
        print(f"Tenant '{tenant_key}' failed: {exc}")
```

### Pre-validate all configured tenants

```python
# Raises ValueError if any tenant is missing credentials (use skip_invalid=True to log & skip)
all_creds = provider.get_all_credentials(skip_invalid=True)
```
