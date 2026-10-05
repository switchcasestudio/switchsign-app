from __future__ import annotations

import base64
import binascii
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from switchsign.config import settings
from switchsign.db import AuditLog, Contract, ContractStatus, get_session
from switchsign.main_support import templates
from switchsign.services.email import send_signed_copy_to_client, send_signed_copy_to_owner
from switchsign.services.markdown_render import render_contract_markdown
from switchsign.services.pdf import generate_signed_pdf
from switchsign.services.storage import signed_pdf_object_name, upload_pdf_to_storage
from switchsign.utils.time import now_utc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["public"])

_SIGNATURE_PREFIX = "data:image/png;base64,"
_MIN_SIGNATURE_BYTES = 100
_MAX_SIGNATURE_BYTES = 1024 * 1024
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _client_ip(request: Request) -> str | None:
    # Behind Traefik the socket peer is the proxy, so prefer the real client
    # IP from X-Forwarded-For (first hop) when present.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def _client_meta(request: Request) -> dict[str, str | None]:
    return {
        "ip": _client_ip(request),
        "ua": request.headers.get("user-agent"),
    }


def _get_contract_or_404(token: str, db: Session) -> Contract:
    contract = db.scalar(select(Contract).where(Contract.signing_token == token))
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "Signing link not found"})
    return contract


def _render_not_found(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("not_found.html", {"request": request}, status_code=404)


def _is_expired(contract: Contract) -> bool:
    expires_at = contract.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now_utc().tzinfo)
    return expires_at <= now_utc()


def _decode_signature_png(signature_data_url: str) -> bytes:
    if not signature_data_url.startswith(_SIGNATURE_PREFIX):
        raise HTTPException(status_code=400, detail={"error": "Signature must be a PNG data URL"})
    encoded = signature_data_url[len(_SIGNATURE_PREFIX) :]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "Signature image is invalid"}) from exc
    if len(data) < _MIN_SIGNATURE_BYTES:
        raise HTTPException(status_code=400, detail={"error": "Signature image is too small"})
    if len(data) > _MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=400, detail={"error": "Signature image is too large"})
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail={"error": "Signature image must be PNG"})
    return data


def _email_signed_copies(contract: Contract, pdf_path: Path, db: Session) -> None:
    try:
        message_id = send_signed_copy_to_owner(contract, pdf_path)
        db.add(
            AuditLog(
                contract_id=contract.id,
                event="email_sent",
                event_metadata={"to": "owner", "recipient": settings.email_notify_owner, "message_id": message_id},
                at=now_utc(),
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to email owner for contract %s", contract.id)
        db.add(
            AuditLog(
                contract_id=contract.id,
                event="email_failed",
                event_metadata={"to": "owner", "error": str(exc)},
                at=now_utc(),
            )
        )
        db.commit()

    try:
        message_id = send_signed_copy_to_client(contract, pdf_path)
        db.add(
            AuditLog(
                contract_id=contract.id,
                event="email_sent",
                event_metadata={"to": "client", "recipient": contract.client_email, "message_id": message_id},
                at=now_utc(),
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to email client for contract %s", contract.id)
        db.add(
            AuditLog(
                contract_id=contract.id,
                event="email_failed",
                event_metadata={"to": "client", "error": str(exc)},
                at=now_utc(),
            )
        )
        db.commit()


@router.get("/s/{token}", response_class=HTMLResponse)
def signing_page(token: str, request: Request, db: Session = Depends(get_session)) -> HTMLResponse:
    try:
        contract = _get_contract_or_404(token, db)
    except HTTPException:
        return _render_not_found(request)

    if contract.status == ContractStatus.signed.value:
        return templates.TemplateResponse(
            "signed.html",
            {"request": request, "client_name": contract.client_name, "client_signed_name": contract.client_signed_name},
        )
    if contract.status == ContractStatus.cancelled.value:
        return templates.TemplateResponse("cancelled.html", {"request": request})
    if contract.status == ContractStatus.expired.value or _is_expired(contract):
        if contract.status == ContractStatus.pending.value:
            contract.status = ContractStatus.expired.value
            db.add(AuditLog(contract_id=contract.id, event="expired", event_metadata=_client_meta(request), at=now_utc()))
            db.commit()
        return templates.TemplateResponse("expired.html", {"request": request})

    db.add(AuditLog(contract_id=contract.id, event="viewed", event_metadata=_client_meta(request), at=now_utc()))
    db.commit()

    return templates.TemplateResponse(
        "sign.html",
        {
            "request": request,
            "token": token,
            "client_name": contract.client_name,
            "contract_title": contract.contract_title,
            "contract_html": render_contract_markdown(contract.markdown_body),
        },
    )


@router.get("/s/{token}/address-suggest")
def address_suggest(
    token: str,
    q: str = Query("", max_length=200),
    db: Session = Depends(get_session),
) -> dict:
    contract = _get_contract_or_404(token, db)
    if contract.status != ContractStatus.pending.value or _is_expired(contract):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "Signing link is no longer active"})

    q = q.strip()
    if len(q) < 3 or not settings.google_maps_api_key:
        return {"suggestions": []}

    try:
        response = httpx.post(
            "https://places.googleapis.com/v1/places:autocomplete",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": settings.google_maps_api_key,
            },
            json={"input": q, "includedRegionCodes": ["us"]},
            timeout=4.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        # Autocomplete is a convenience; the field still accepts free text.
        logger.warning("Address autocomplete lookup failed: %s", exc)
        return {"suggestions": []}

    suggestions = []
    for item in payload.get("suggestions", []):
        text = item.get("placePrediction", {}).get("text", {}).get("text")
        if text:
            suggestions.append(text)
    return {"suggestions": suggestions[:5]}


@router.post("/s/{token}/submit")
def submit_signature(
    token: str,
    request: Request,
    db: Session = Depends(get_session),
    client_signed_name: str = Form(...),
    client_legal_name: str = Form(""),
    client_company_name: str = Form(""),
    client_title: str = Form(""),
    client_phone: str = Form(...),
    client_signed_email: str = Form(...),
    client_signed_address: str = Form(...),
    signature_data_url: str = Form(...),
    agreed: bool = Form(False),
) -> RedirectResponse:
    contract = _get_contract_or_404(token, db)
    if contract.status != ContractStatus.pending.value:
        raise HTTPException(status_code=409, detail={"error": f"Contract is already {contract.status}"})
    if _is_expired(contract):
        contract.status = ContractStatus.expired.value
        db.add(AuditLog(contract_id=contract.id, event="expired", event_metadata=_client_meta(request), at=now_utc()))
        db.commit()
        raise HTTPException(status_code=409, detail={"error": "Signing link has expired"})
    if not agreed:
        raise HTTPException(status_code=400, detail={"error": "You must agree before signing"})
    client_signed_name = client_signed_name.strip()
    client_company_name = client_company_name.strip()
    client_title = client_title.strip()
    client_phone = client_phone.strip()
    client_signed_email = client_signed_email.strip().lower()
    client_signed_address = client_signed_address.strip()
    # The single "Full legal name" field is the binding signer; keep the
    # legal-name column populated for continuity in the admin/audit views.
    client_legal_name = client_legal_name.strip() or client_signed_name
    if not client_signed_name:
        raise HTTPException(status_code=400, detail={"error": "Full legal name is required"})
    if not client_phone:
        raise HTTPException(status_code=400, detail={"error": "Phone number is required"})
    if not _EMAIL_RE.match(client_signed_email):
        raise HTTPException(status_code=400, detail={"error": "A valid email address is required"})
    if not client_signed_address:
        raise HTTPException(status_code=400, detail={"error": "Business address is required"})

    signature_png = _decode_signature_png(signature_data_url)
    signed_at = now_utc()
    signature_dir = settings.switchsign_db_path.parent / "signatures"
    signature_dir.mkdir(parents=True, exist_ok=True)
    relative_path = Path("signatures") / f"{contract.id}.png"
    signature_path = settings.switchsign_db_path.parent / relative_path

    try:
        signature_path.write_bytes(signature_png)
        meta = _client_meta(request)
        contract.status = ContractStatus.signed.value
        contract.signed_at = signed_at
        contract.client_signed_name = client_signed_name
        contract.client_legal_name = client_legal_name
        contract.client_company_name = client_company_name or None
        contract.client_title = client_title or None
        contract.client_phone = client_phone
        contract.client_name = client_signed_name
        contract.client_email = client_signed_email
        contract.client_signed_address = client_signed_address
        contract.client_signature_png_path = str(relative_path)
        contract.client_ip = meta["ip"]
        contract.client_user_agent = meta["ua"]
        db.add(AuditLog(contract_id=contract.id, event="signed", event_metadata=meta, at=signed_at))
        db.commit()

        try:
            pdf_path = generate_signed_pdf(contract, db)
            contract.signed_pdf_local_path = str(pdf_path)
            db.add(AuditLog(contract_id=contract.id, event="pdf_generated", event_metadata={"path": str(pdf_path)}, at=now_utc()))
            db.commit()

            try:
                storage_path = upload_pdf_to_storage(
                    pdf_path,
                    settings.google_cloud_storage_bucket,
                    signed_pdf_object_name(contract.id),
                )
                contract.signed_pdf_drive_id = storage_path
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="storage_uploaded",
                        event_metadata={"storage_path": storage_path},
                        at=now_utc(),
                    )
                )
                db.commit()
            except Exception as storage_exc:
                db.rollback()
                logger.exception("Failed to upload PDF to Google Cloud Storage for contract %s", contract.id)
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="storage_upload_failed",
                        event_metadata={"error": str(storage_exc), "path": str(pdf_path)},
                        at=now_utc(),
                    )
                )
                db.commit()

            _email_signed_copies(contract, pdf_path, db)
        except Exception as pdf_exc:
            db.rollback()
            logger.exception("Failed to generate PDF for contract %s", contract.id)
            db.add(AuditLog(contract_id=contract.id, event="pdf_generation_failed", event_metadata={"error": str(pdf_exc)}, at=now_utc()))
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to submit signature for contract %s", contract.id)
        raise

    return RedirectResponse(url=f"/signed/{token}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/signed/{token}", response_class=HTMLResponse)
def signed_page(token: str, request: Request, db: Session = Depends(get_session)):
    try:
        contract = _get_contract_or_404(token, db)
    except HTTPException:
        return _render_not_found(request)
    if contract.status == ContractStatus.signed.value:
        return templates.TemplateResponse(
            "signed.html",
            {"request": request, "client_name": contract.client_name, "client_signed_name": contract.client_signed_name},
        )
    return RedirectResponse(url=f"/s/{token}", status_code=status.HTTP_303_SEE_OTHER)
