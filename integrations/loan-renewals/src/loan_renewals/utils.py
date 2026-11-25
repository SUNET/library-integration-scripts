"""Utility functions for the loan renewals integration."""

import json

from folio_client import FolioAsyncApiClient, get_folio_client


def get_client(tenant_key: str | None = None) -> FolioAsyncApiClient:
    """Initialize and return a configured FOLIO API client."""
    return get_folio_client(tenant_key)


def save_json_to_file(data: dict, filename: str) -> None:
    """Save a dictionary as a JSON file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def open_json_file(filename: str) -> dict:
    """Open a JSON file and return its contents as a dictionary."""
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)
