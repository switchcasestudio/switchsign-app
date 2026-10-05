from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from google.cloud import storage

from switchsign.config import settings


@lru_cache(maxsize=1)
def get_storage_client() -> storage.Client:
    if not settings.google_service_account_json_path.exists():
        raise FileNotFoundError(f"Google service account file not found: {settings.google_service_account_json_path}")
    return storage.Client.from_service_account_json(str(settings.google_service_account_json_path))


def signed_pdf_object_name(contract_id: str) -> str:
    return f"signed-pdfs/{contract_id}.pdf"


def upload_pdf_to_storage(pdf_path: Path, bucket_name: str, object_name: str) -> str:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if not bucket_name:
        raise ValueError("Google Cloud Storage bucket is not configured")
    if not object_name:
        raise ValueError("Google Cloud Storage object name is not configured")

    bucket = get_storage_client().bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(pdf_path), content_type="application/pdf")
    return f"gs://{bucket_name}/{object_name}"
