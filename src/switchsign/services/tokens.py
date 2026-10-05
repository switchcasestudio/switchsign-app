import secrets

from switchsign.config import settings


def generate_signing_token() -> str:
    return secrets.token_urlsafe(24)


def verify_api_key(provided: str) -> bool:
    return secrets.compare_digest(provided, settings.switchsign_api_key)
