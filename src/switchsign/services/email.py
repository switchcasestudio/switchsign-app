from __future__ import annotations

import base64
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import resend

from switchsign.config import settings
from switchsign.db import Contract
from switchsign.main_support import templates
from switchsign.services.pdf import signed_pdf_filename

resend.api_key = settings.resend_api_key


def _from_header() -> str:
    return f"{settings.email_from_name} <{settings.email_from_address}>"


def _as_aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value) -> str:
    value = _as_aware_utc(value)
    if value is None:
        return ""
    try:
        display_tz = ZoneInfo(settings.tz)
    except Exception:
        display_tz = timezone.utc
    local = value.astimezone(display_tz)
    return local.strftime("%B %-d, %Y at %-I:%M %p %Z")


def _storage_url(storage_path: str | None) -> str | None:
    return storage_path or None


def _logo_url() -> str:
    return f"{settings.switchsign_base_url.rstrip('/')}/static/brand/logo-starburst.png"


def _render_email_template(name: str, **context) -> str:
    context.setdefault("logo_url", _logo_url())
    return templates.get_template(f"emails/{name}").render(**context)


def _message_id(response) -> str:
    if isinstance(response, dict):
        return str(response.get("id", ""))
    return str(getattr(response, "id", ""))


def send_email_with_pdf(
    to: str,
    subject: str,
    html_body: str,
    pdf_path: Path,
    pdf_filename: str,
) -> str:
    """
    Send an email with an attached PDF via Resend.
    Returns the Resend message ID. Raises on failure.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    encoded_pdf = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    response = resend.Emails.send(
        {
            "from": _from_header(),
            "to": [to],
            "subject": subject,
            "html": html_body,
            "reply_to": settings.email_notify_owner,
            "attachments": [
                {
                    "filename": pdf_filename,
                    "content": encoded_pdf,
                }
            ],
        }
    )
    return _message_id(response)


def send_signed_copy_to_owner(contract: Contract, pdf_path: Path) -> str:
    html_body = _render_email_template(
        "signed_to_owner.html",
        contract=contract,
        signed_at_display=_format_datetime(contract.signed_at),
        drive_url=_storage_url(contract.signed_pdf_drive_id),
    )
    return send_email_with_pdf(
        to=settings.email_notify_owner,
        subject=f"[SwitchSign] Signed: {contract.contract_title} — {contract.client_name}",
        html_body=html_body,
        pdf_path=pdf_path,
        pdf_filename=signed_pdf_filename(contract),
    )


def send_signed_copy_to_client(contract: Contract, pdf_path: Path) -> str:
    html_body = _render_email_template("signed_to_client.html", contract=contract)
    return send_email_with_pdf(
        to=contract.client_email,
        subject="Your signed agreement with Switch Case Studio",
        html_body=html_body,
        pdf_path=pdf_path,
        pdf_filename=signed_pdf_filename(contract),
    )


def send_reminder_to_client(contract: Contract) -> str:
    html_body = _render_email_template(
        "reminder_to_client.html",
        contract=contract,
        signing_url=f"{settings.switchsign_base_url.rstrip('/')}/s/{contract.signing_token}",
        expires_at_display=_format_datetime(contract.expires_at),
    )
    response = resend.Emails.send(
        {
            "from": _from_header(),
            "to": [contract.client_email],
            "subject": f"Reminder: {contract.contract_title}",
            "html": html_body,
            "reply_to": settings.email_notify_owner,
        }
    )
    return _message_id(response)
