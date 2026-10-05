from __future__ import annotations

import base64
import re
import time
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from weasyprint import CSS, HTML

from switchsign.config import settings
from switchsign.db import Contract, ContractStatus
from switchsign.main_support import PACKAGE_DIR, templates
from switchsign.services.markdown_render import render_contract_markdown
from switchsign.services.summary import derive_cover_overview, extract_quick_summary, strip_redundant_preamble

SERVICE_PROVIDER_BLOCK = {
    "legal_name": "Switch Case LLC DBA Switch Case Studio",
    "address": "123 Example Street, Portland, OR 97205",
    "signer_name": "Moshe Atia",
    "title": "President",
}


def signed_pdf_filename(contract: Contract) -> str:
    date_part = (contract.signed_at or contract.created_at).date().isoformat()
    client = _safe_filename_part(contract.client_name)
    title = _safe_filename_part(contract.contract_title)
    return f"{date_part} - {client} - {title}.pdf"


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "", value).strip()
    return re.sub(r"\s+", " ", cleaned) or "contract"


def _as_aware_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_signed_at(value) -> str:
    signed_at = _as_aware_utc(value)
    if signed_at is None:
        return ""
    try:
        display_tz = ZoneInfo(settings.tz)
    except Exception:
        display_tz = timezone.utc
    local = signed_at.astimezone(display_tz)
    return local.strftime("%B %-d, %Y at %-I:%M %p %Z")


def _format_signed_date(value) -> str:
    signed_at = _as_aware_utc(value)
    if signed_at is None:
        return ""
    try:
        display_tz = ZoneInfo(settings.tz)
    except Exception:
        display_tz = timezone.utc
    return signed_at.astimezone(display_tz).strftime("%m/%d/%Y")


def generate_signed_pdf(contract: Contract, session: Session) -> Path:
    """
    Generate the signed PDF for a contract. Returns the absolute path
    to the saved file. The contract must already be in 'signed' status
    and have client_signed_name, client_signed_address, signature path.
    """
    if contract.status != ContractStatus.signed.value:
        raise ValueError("Contract must be signed before PDF generation")
    if not contract.client_signature_png_path:
        raise ValueError("Contract is missing signature image path")
    if not contract.client_signed_name or not contract.client_signed_address:
        raise ValueError("Contract is missing signed client details")

    data_dir = settings.switchsign_db_path.parent
    signature_path = data_dir / contract.client_signature_png_path
    if not signature_path.exists():
        raise FileNotFoundError(f"Signature image not found: {signature_path}")

    signature_image_base64 = base64.b64encode(signature_path.read_bytes()).decode("ascii")
    output_dir = data_dir / "signed-pdfs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{contract.id}.pdf"

    quick_summary_markdown, contract_body_markdown = extract_quick_summary(contract.markdown_body)
    # The branded PDF generates its own formal preamble, so strip any duplicate
    # agreement-title/party-intro the body may contain before section 1.
    contract_body_markdown = strip_redundant_preamble(contract_body_markdown)
    # Always give the cover a project overview: fall back to the opening of the
    # first numbered section when no explicit Quick Summary was authored.
    cover_overview_markdown = quick_summary_markdown or derive_cover_overview(contract_body_markdown)
    # Signed PDFs are customer-facing SCS artifacts; keep them branded even if
    # older deployments still have BRAND_STYLE disabled in the environment.
    template_name = "contract_pdf_brand.html"
    stylesheet_name = "contract-brand.css"

    # The contracting party is the company when one was given, otherwise the
    # individual signer. Title/company also surface in the signature block.
    client_party = contract.client_company_name or contract.client_signed_name

    html_string = templates.get_template(template_name).render(
        contract_title=contract.contract_title,
        client_name=contract.client_name,
        client_party=client_party,
        client_company_name=contract.client_company_name,
        client_title=contract.client_title,
        client_email=contract.client_email,
        contract_html=render_contract_markdown(contract_body_markdown),
        quick_summary_html=render_contract_markdown(cover_overview_markdown),
        has_quick_summary=bool(cover_overview_markdown),
        client_signed_name=contract.client_signed_name,
        client_signed_address=contract.client_signed_address,
        signature_image_base64=signature_image_base64,
        signed_at_display=_format_signed_at(contract.signed_at),
        signed_date_display=_format_signed_date(contract.signed_at),
        audit_id=contract.id,
        client_ip=contract.client_ip or "",
        service_provider_block=SERVICE_PROVIDER_BLOCK,
        generated_at_epoch=time.time(),
    )

    static_dir = PACKAGE_DIR / "static"
    stylesheet = static_dir / stylesheet_name
    HTML(string=html_string, base_url=str(static_dir)).write_pdf(
        output_path,
        stylesheets=[CSS(filename=str(stylesheet))],
    )
    return output_path
