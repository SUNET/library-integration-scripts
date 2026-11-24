"""Utility functions for the loan renewals integration."""


from folio_client import FolioAsyncApiClient, get_folio_client


def get_client(tenant_key: str | None = None) -> FolioAsyncApiClient:
    """Initialize and return a configured FOLIO API client."""
    return get_folio_client(tenant_key)
