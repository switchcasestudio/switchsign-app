from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from switchsign import __version__
from switchsign.config import settings
from switchsign.db import AuditLog, Contract, ContractStatus, get_session
from switchsign.services.email import send_signed_copy_to_client, send_signed_copy_to_owner
from switchsign.services.pdf import generate_signed_pdf, signed_pdf_filename
from switchsign.jobs.scheduler import jobs_status, run_drive_retry_sweep, run_expiry_sweep, run_reminder_sweep
from switchsign.services.storage import signed_pdf_object_name, upload_pdf_to_storage
from switchsign.services.tokens import generate_signing_token, verify_api_key
from switchsign.utils.time import now_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["admin"])

DbSession = Annotated[Session, Depends(get_session)]


class ErrorResponse(BaseModel):
    error: str


class CreateContractRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=200)
    client_email: EmailStr
    contract_title: str = Field(min_length=1, max_length=200)
    markdown_body: str = Field(min_length=1, max_length=100_000)
    # Optional — prefill the signing form's Business fields when known up front.
    client_company_name: str | None = Field(default=None, max_length=255)
    client_title: str | None = Field(default=None, max_length=255)


class ResendEmailRequest(BaseModel):
    to: Literal["owner", "client"]


class JobsStatusResponse(BaseModel):
    running: bool
    jobs: list[dict[str, Any]]


class AgentPingResponse(BaseModel):
    ok: bool
    service: str
    version: str


class ExpirySweepResponse(BaseModel):
    expired: int


class ReminderSweepResponse(BaseModel):
    reminders_sent: int


class DriveRetrySweepResponse(BaseModel):
    uploaded: int


class CreateContractResponse(BaseModel):
    id: str
    signing_url: str
    expires_at: datetime


class AuditLogEntry(BaseModel):
    id: int
    event: str
    metadata: dict[str, Any] | None
    at: datetime


class ContractSummary(BaseModel):
    id: str
    client_name: str
    client_email: str
    contract_title: str
    status: str
    created_at: datetime
    expires_at: datetime
    signed_at: datetime | None
    signing_url: str


class ContractListResponse(BaseModel):
    total: int
    items: list[ContractSummary]


class ContractDetail(ContractSummary):
    markdown_body: str
    reminder_sent_at: datetime | None
    signed_pdf_storage_path: str | None
    signed_pdf_storage_url: str | None
    signed_pdf_drive_id: str | None
    signed_pdf_drive_url: str | None
    signed_pdf_local_path: str | None
    client_signature_png_path: str | None
    client_signed_name: str | None
    client_signed_address: str | None
    client_ip: str | None
    client_user_agent: str | None
    audit_log: list[AuditLogEntry]


def _error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message})


def _signing_url(token: str) -> str:
    return f"{settings.switchsign_base_url.rstrip('/')}/s/{token}"


def _storage_url(storage_path: str | None) -> str | None:
    if not storage_path:
        return None
    return storage_path


def _summary(contract: Contract) -> ContractSummary:
    return ContractSummary(
        id=contract.id,
        client_name=contract.client_name,
        client_email=contract.client_email,
        contract_title=contract.contract_title,
        status=str(contract.status),
        created_at=contract.created_at,
        expires_at=contract.expires_at,
        signed_at=contract.signed_at,
        signing_url=_signing_url(contract.signing_token),
    )


def _detail(contract: Contract) -> ContractDetail:
    summary = _summary(contract).model_dump()
    return ContractDetail(
        **summary,
        markdown_body=contract.markdown_body,
        reminder_sent_at=contract.reminder_sent_at,
        signed_pdf_storage_path=contract.signed_pdf_drive_id,
        signed_pdf_storage_url=_storage_url(contract.signed_pdf_drive_id),
        signed_pdf_drive_id=contract.signed_pdf_drive_id,
        signed_pdf_drive_url=_storage_url(contract.signed_pdf_drive_id),
        signed_pdf_local_path=contract.signed_pdf_local_path,
        client_signature_png_path=contract.client_signature_png_path,
        client_signed_name=contract.client_signed_name,
        client_signed_address=contract.client_signed_address,
        client_ip=contract.client_ip,
        client_user_agent=contract.client_user_agent,
        audit_log=[
            AuditLogEntry(
                id=entry.id,
                event=entry.event,
                metadata=entry.event_metadata,
                at=entry.at,
            )
            for entry in sorted(contract.audit_entries, key=lambda item: item.at)
        ],
    )


def require_api_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
    if not x_api_key:
        raise _error(status.HTTP_401_UNAUTHORIZED, "Missing API key")
    if not verify_api_key(x_api_key):
        raise _error(status.HTTP_403_FORBIDDEN, "Invalid API key")


@router.get("/agent/ping", response_model=AgentPingResponse, dependencies=[Depends(require_api_key)])
def agent_ping() -> AgentPingResponse:
    return AgentPingResponse(ok=True, service="switchsign", version=__version__)


@router.get("/jobs/status", response_model=JobsStatusResponse, dependencies=[Depends(require_api_key)])
def get_jobs_status() -> JobsStatusResponse:
    return JobsStatusResponse(**jobs_status())


@router.post("/jobs/expiry-sweep", response_model=ExpirySweepResponse, dependencies=[Depends(require_api_key)])
def trigger_expiry_sweep() -> ExpirySweepResponse:
    return ExpirySweepResponse(expired=run_expiry_sweep())


@router.post("/jobs/reminder-sweep", response_model=ReminderSweepResponse, dependencies=[Depends(require_api_key)])
def trigger_reminder_sweep() -> ReminderSweepResponse:
    return ReminderSweepResponse(reminders_sent=run_reminder_sweep())


@router.post("/jobs/drive-retry-sweep", response_model=DriveRetrySweepResponse, dependencies=[Depends(require_api_key)])
def trigger_drive_retry_sweep() -> DriveRetrySweepResponse:
    return DriveRetrySweepResponse(uploaded=run_drive_retry_sweep())


@router.post(
    "/contracts",
    response_model=CreateContractResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    dependencies=[Depends(require_api_key)],
)
def create_contract(payload: CreateContractRequest, request: Request, db: DbSession) -> CreateContractResponse:
    try:
        contract_id = str(uuid.uuid4())
        token = generate_signing_token()
        created_at = now_utc()
        expires_at = created_at + timedelta(days=settings.link_expiry_days)
        client_ip = request.client.host if request.client else None

        contract = Contract(
            id=contract_id,
            client_name=payload.client_name,
            client_email=str(payload.client_email),
            contract_title=payload.contract_title,
            markdown_body=payload.markdown_body,
            signing_token=token,
            status=ContractStatus.pending.value,
            created_at=created_at,
            expires_at=expires_at,
            client_company_name=payload.client_company_name or None,
            client_title=payload.client_title or None,
        )
        db.add(contract)
        db.add(AuditLog(contract_id=contract_id, event="created", event_metadata={"ip": client_ip}, at=created_at))
        db.commit()

        return CreateContractResponse(id=contract_id, signing_url=_signing_url(token), expires_at=expires_at)
    except Exception:
        db.rollback()
        logger.exception("Failed to create contract")
        raise


@router.get("/contracts", response_model=ContractListResponse, dependencies=[Depends(require_api_key)])
def list_contracts(
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContractListResponse:
    stmt: Select[tuple[Contract]] = select(Contract)
    count_stmt = select(func.count()).select_from(Contract)
    if status_filter:
        stmt = stmt.where(Contract.status == status_filter)
        count_stmt = count_stmt.where(Contract.status == status_filter)

    total = db.scalar(count_stmt) or 0
    contracts = db.scalars(stmt.order_by(Contract.created_at.desc()).limit(limit).offset(offset)).all()
    return ContractListResponse(total=total, items=[_summary(contract) for contract in contracts])


@router.get("/contracts/{contract_id}", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
def get_contract(contract_id: str, db: DbSession) -> ContractDetail:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    return _detail(contract)


@router.post("/contracts/{contract_id}/cancel", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
def cancel_contract(contract_id: str, db: DbSession) -> ContractDetail:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    if contract.status != ContractStatus.pending.value:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot cancel contract with status '{contract.status}'")

    try:
        contract.status = ContractStatus.cancelled.value
        db.add(AuditLog(contract_id=contract.id, event="cancelled", event_metadata=None, at=now_utc()))
        db.commit()
        db.refresh(contract)
        # Reload relationship after commit so response includes the new audit row.
        contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
        assert contract is not None
        return _detail(contract)
    except Exception:
        db.rollback()
        logger.exception("Failed to cancel contract %s", contract_id)
        raise


def _upload_contract_pdf_to_storage(contract: Contract, pdf_path: Path, db: Session) -> None:
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


@router.post("/contracts/{contract_id}/regenerate-pdf", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
def regenerate_pdf(contract_id: str, db: DbSession) -> ContractDetail:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    if contract.status != ContractStatus.signed.value:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot regenerate PDF for contract with status '{contract.status}'")

    try:
        pdf_path = generate_signed_pdf(contract, db)
        contract.signed_pdf_local_path = str(pdf_path)
        db.add(AuditLog(contract_id=contract.id, event="pdf_regenerated", event_metadata={"path": str(pdf_path)}, at=now_utc()))
        db.commit()

        _upload_contract_pdf_to_storage(contract, pdf_path, db)

        contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
        assert contract is not None
        return _detail(contract)
    except Exception:
        db.rollback()
        logger.exception("Failed to regenerate PDF for contract %s", contract_id)
        raise


@router.post("/contracts/{contract_id}/retry-storage-upload", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
@router.post("/contracts/{contract_id}/retry-drive-upload", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
def retry_storage_upload(contract_id: str, db: DbSession) -> ContractDetail:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    if contract.status != ContractStatus.signed.value:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot upload PDF for contract with status '{contract.status}'")
    if contract.signed_pdf_drive_id:
        raise _error(status.HTTP_409_CONFLICT, "PDF has already been uploaded to storage")
    if not contract.signed_pdf_local_path:
        raise _error(status.HTTP_404_NOT_FOUND, "PDF has not been generated")

    pdf_path = Path(contract.signed_pdf_local_path)
    if not pdf_path.exists():
        raise _error(status.HTTP_404_NOT_FOUND, "PDF file not found on disk")

    _upload_contract_pdf_to_storage(contract, pdf_path, db)
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    assert contract is not None
    return _detail(contract)

@router.post("/contracts/{contract_id}/resend-email", response_model=ContractDetail, dependencies=[Depends(require_api_key)])
def resend_contract_email(contract_id: str, payload: ResendEmailRequest, db: DbSession) -> ContractDetail:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    if contract.status != ContractStatus.signed.value:
        raise _error(status.HTTP_409_CONFLICT, f"Cannot resend email for contract with status '{contract.status}'")
    if not contract.signed_pdf_local_path:
        raise _error(status.HTTP_404_NOT_FOUND, "PDF has not been generated")

    pdf_path = Path(contract.signed_pdf_local_path)
    if not pdf_path.exists():
        raise _error(status.HTTP_404_NOT_FOUND, "PDF file not found on disk")

    try:
        if payload.to == "owner":
            message_id = send_signed_copy_to_owner(contract, pdf_path)
            recipient = settings.email_notify_owner
        else:
            message_id = send_signed_copy_to_client(contract, pdf_path)
            recipient = contract.client_email

        db.add(
            AuditLog(
                contract_id=contract.id,
                event="email_resent",
                event_metadata={"to": payload.to, "recipient": recipient, "message_id": message_id},
                at=now_utc(),
            )
        )
        db.commit()
        contract = db.scalar(select(Contract).where(Contract.id == contract_id).options(selectinload(Contract.audit_entries)))
        assert contract is not None
        return _detail(contract)
    except Exception:
        db.rollback()
        logger.exception("Failed to resend %s email for contract %s", payload.to, contract.id)
        raise


@router.get("/contracts/{contract_id}/pdf", dependencies=[Depends(require_api_key)])
def download_pdf(contract_id: str, db: DbSession) -> FileResponse:
    contract = db.scalar(select(Contract).where(Contract.id == contract_id))
    if contract is None:
        raise _error(status.HTTP_404_NOT_FOUND, "Contract not found")
    if not contract.signed_pdf_local_path:
        raise _error(status.HTTP_404_NOT_FOUND, "PDF has not been generated")
    pdf_path = Path(contract.signed_pdf_local_path)
    if not pdf_path.exists():
        raise _error(status.HTTP_404_NOT_FOUND, "PDF file not found on disk")

    return FileResponse(path=pdf_path, media_type="application/pdf", filename=signed_pdf_filename(contract))
