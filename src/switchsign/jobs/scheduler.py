from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from switchsign.config import settings
from switchsign.db import AuditLog, Contract, ContractStatus, SessionLocal
from switchsign.services.email import send_reminder_to_client
from switchsign.services.storage import signed_pdf_object_name, upload_pdf_to_storage
from switchsign.utils.time import now_utc

logger = logging.getLogger(__name__)

try:
    scheduler_tz = ZoneInfo(settings.tz)
except Exception:
    logger.warning("Invalid TZ %r; falling back to UTC for scheduler", settings.tz)
    scheduler_tz = timezone.utc

scheduler = BackgroundScheduler(timezone=scheduler_tz)
_last_results: dict[str, dict[str, Any]] = {}


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_result(job_id: str, count: int, status: str = "ok", error: str | None = None) -> None:
    result: dict[str, Any] = {
        "status": status,
        "count": count,
        "ran_at": now_utc().isoformat(),
    }
    if error:
        result["error"] = error
    _last_results[job_id] = result


def start_scheduler() -> None:
    if scheduler.running:
        return

    scheduler.add_job(
        run_expiry_sweep,
        IntervalTrigger(hours=1),
        id="expiry_sweep",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_reminder_sweep,
        IntervalTrigger(hours=1),
        id="reminder_sweep",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_drive_retry_sweep,
        IntervalTrigger(hours=6),
        id="drive_retry_sweep",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("APScheduler started with SwitchSign background jobs")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


def jobs_status() -> dict[str, Any]:
    jobs = []
    for job_id in ("expiry_sweep", "reminder_sweep", "drive_retry_sweep"):
        job = scheduler.get_job(job_id)
        jobs.append(
            {
                "id": job_id,
                "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
                "last_result": _last_results.get(job_id),
            }
        )
    return {"running": scheduler.running, "jobs": jobs}


def run_expiry_sweep() -> int:
    logger.info("expiry_sweep starting")
    expired = 0
    now = now_utc()

    with SessionLocal() as db:
        contracts = db.scalars(
            select(Contract).where(Contract.status == ContractStatus.pending.value)
        ).all()

        for contract in contracts:
            expires_at = _as_aware_utc(contract.expires_at)
            if expires_at is None or expires_at > now:
                continue
            try:
                contract.status = ContractStatus.expired.value
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="expired",
                        event_metadata={"source": "expiry_sweep"},
                        at=now_utc(),
                    )
                )
                db.commit()
                expired += 1
            except Exception:
                db.rollback()
                logger.exception("Failed to expire contract %s", contract.id)

    _record_result("expiry_sweep", expired)
    logger.info("expiry_sweep ran, %s expired", expired)
    return expired


def run_reminder_sweep() -> int:
    logger.info("reminder_sweep starting")
    reminders_sent = 0
    now = now_utc()
    reminder_before = now - timedelta(days=settings.reminder_after_days)

    with SessionLocal() as db:
        contracts = db.scalars(
            select(Contract).where(
                Contract.status == ContractStatus.pending.value,
                Contract.reminder_sent_at.is_(None),
            )
        ).all()

        for contract in contracts:
            created_at = _as_aware_utc(contract.created_at)
            expires_at = _as_aware_utc(contract.expires_at)
            if created_at is None or expires_at is None:
                logger.warning("Skipping contract %s with missing dates", contract.id)
                continue
            if created_at > reminder_before or expires_at <= now:
                continue

            try:
                message_id = send_reminder_to_client(contract)
                sent_at = now_utc()
                contract.reminder_sent_at = sent_at
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="reminder_sent",
                        event_metadata={
                            "recipient": contract.client_email,
                            "message_id": message_id,
                            "source": "reminder_sweep",
                        },
                        at=sent_at,
                    )
                )
                db.commit()
                reminders_sent += 1
            except Exception as exc:
                db.rollback()
                logger.exception("Failed to send reminder for contract %s", contract.id)
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="reminder_failed",
                        event_metadata={"recipient": contract.client_email, "error": str(exc)},
                        at=now_utc(),
                    )
                )
                db.commit()

    _record_result("reminder_sweep", reminders_sent)
    logger.info("reminder_sweep ran, %s reminders sent", reminders_sent)
    return reminders_sent


def run_drive_retry_sweep() -> int:
    logger.info("drive_retry_sweep starting")
    uploaded = 0

    with SessionLocal() as db:
        contracts = db.scalars(
            select(Contract).where(
                Contract.status == ContractStatus.signed.value,
                Contract.signed_pdf_drive_id.is_(None),
            )
        ).all()

        for contract in contracts:
            if not contract.signed_pdf_local_path:
                logger.warning("Skipping contract %s with no local PDF path", contract.id)
                continue

            pdf_path = Path(contract.signed_pdf_local_path)
            if not pdf_path.exists():
                logger.warning("Skipping contract %s; PDF missing at %s", contract.id, pdf_path)
                continue

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
                        event_metadata={"storage_path": storage_path, "source": "drive_retry_sweep"},
                        at=now_utc(),
                    )
                )
                db.commit()
                uploaded += 1
            except Exception as exc:
                db.rollback()
                logger.exception("Failed to retry Google Cloud Storage upload for contract %s", contract.id)
                db.add(
                    AuditLog(
                        contract_id=contract.id,
                        event="storage_upload_failed",
                        event_metadata={"error": str(exc), "path": str(pdf_path), "source": "drive_retry_sweep"},
                        at=now_utc(),
                    )
                )
                db.commit()

    _record_result("drive_retry_sweep", uploaded)
    logger.info("drive_retry_sweep ran, %s uploaded", uploaded)
    return uploaded


retry_failed_storage_uploads = run_drive_retry_sweep
retry_failed_drive_uploads = run_drive_retry_sweep
