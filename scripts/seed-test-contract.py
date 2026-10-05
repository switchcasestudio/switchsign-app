import os

import httpx


def main() -> None:
    api_key = os.environ["SWITCHSIGN_API_KEY"]
    base_url = os.environ.get("SWITCHSIGN_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    payload = {
        "client_name": "Test Client",
        "client_email": "test@example.com",
        "contract_title": "Test Contract",
        "markdown_body": "## Test Contract\n\nLorem ipsum dolor sit amet.",
    }
    response = httpx.post(
        f"{base_url}/api/contracts",
        headers={"X-API-Key": api_key},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    print(f"Created contract {data['id']}")
    print(f"Signing URL: {data['signing_url']}")
    print(f"Expires at: {data['expires_at']}")
    print("Open this URL in a browser to test the signing flow.")


if __name__ == "__main__":
    main()
