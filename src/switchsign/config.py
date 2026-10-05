from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    switchsign_api_key: str = Field(alias="SWITCHSIGN_API_KEY")
    switchsign_base_url: str = Field(alias="SWITCHSIGN_BASE_URL")
    switchsign_db_path: Path = Field(alias="SWITCHSIGN_DB_PATH")
    google_cloud_storage_bucket: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLOUD_STORAGE_BUCKET", "GCS_BUCKET"),
    )
    google_service_account_json_path: Path = Field(alias="GOOGLE_SERVICE_ACCOUNT_JSON_PATH")
    resend_api_key: str = Field(alias="RESEND_API_KEY")
    email_from_address: str = Field(alias="EMAIL_FROM_ADDRESS")
    email_from_name: str = Field(alias="EMAIL_FROM_NAME")
    email_notify_owner: str = Field(alias="EMAIL_NOTIFY_OWNER")
    link_expiry_days: int = Field(default=7, alias="LINK_EXPIRY_DAYS")
    reminder_after_days: int = Field(default=3, alias="REMINDER_AFTER_DAYS")
    tz: str = Field(default="America/Los_Angeles", alias="TZ")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    brand_style: bool = Field(default=True, alias="BRAND_STYLE")
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")

    model_config = SettingsConfigDict(env_file="/app/.env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
