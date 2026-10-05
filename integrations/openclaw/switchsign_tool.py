from __future__ import annotations

import os

import httpx


def switchsign_create_contract(
    client_name: str,
    client_email: str,
    contract_title: str,
    markdown_body: str,
    client_company_name: str | None = None,
    client_title: str | None = None,
) -> dict:
    """Create a contract via SwitchSign and return the signing URL."""
    body = {
        "client_name": client_name,
        "client_email": client_email,
        "contract_title": contract_title,
        "markdown_body": markdown_body,
    }
    if client_company_name:
        body["client_company_name"] = client_company_name
    if client_title:
        body["client_title"] = client_title
    resp = httpx.post(
        f"{os.environ['SWITCHSIGN_BASE_URL'].rstrip('/')}/api/contracts",
        headers={"X-API-Key": os.environ["SWITCHSIGN_API_KEY"]},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def switchsign_ping() -> dict:
    """Verify SwitchSign admin API connectivity."""
    resp = httpx.get(
        f"{os.environ['SWITCHSIGN_BASE_URL'].rstrip('/')}/api/agent/ping",
        headers={"X-API-Key": os.environ["SWITCHSIGN_API_KEY"]},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
