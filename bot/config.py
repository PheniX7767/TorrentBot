from functools import lru_cache
import logging
import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


def parse_user_ids(*raw_values: object) -> frozenset[int]:
    """Parse Telegram user ids from ints / comma-separated strings."""
    ids: set[int] = set()
    for raw in raw_values:
        if raw is None:
            continue
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            ids.add(raw)
            continue
        text = str(raw).strip().strip("\"'")
        if not text:
            continue
        for part in re.split(r"[\s,;]+", text):
            part = part.strip().strip("\"'")
            if not part:
                continue
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("Skipping invalid user id token: %r", part)
    return frozenset(ids)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    # Preferred: ALLOWED_USER_IDS=111,222
    allowed_user_ids: str = ""
    # Also accepts a single id OR a comma-separated list (common mistake).
    allowed_user_id: str | None = None

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None

    bot_api_base_url: str = "http://telegram-bot-api:8081"

    qbittorrent_url: str = "http://qbittorrent:8080"
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = "adminadmin"

    downloads_dir: str = "/downloads"
    puid: int = 1000
    pgid: int = 1000
    ytdlp_cookies_file: str = "/cookies/cookies.txt"

    max_selected_bytes: int = 20 * 1024**3
    min_free_bytes: int = 2 * 1024**3
    zip_part_bytes: int = 1900 * 1024**2
    stale_job_hours: int = 24
    progress_edit_seconds: int = 20

    @field_validator("allowed_user_id", "allowed_user_ids", mode="before")
    @classmethod
    def _normalize_id_fields(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip().strip("\"'")

    @model_validator(mode="after")
    def _require_allowlist(self) -> "Settings":
        if not self.allowed_ids:
            raise ValueError(
                "Укажите ALLOWED_USER_IDS=111,222 или ALLOWED_USER_ID=111 в .env"
            )
        return self

    @property
    def allowed_ids(self) -> frozenset[int]:
        return parse_user_ids(self.allowed_user_ids, self.allowed_user_id)

    @property
    def bot_api_url(self) -> str:
        return f"{self.bot_api_base_url.rstrip('/')}/bot"

    @property
    def bot_api_file_url(self) -> str:
        return f"{self.bot_api_base_url.rstrip('/')}/file/bot"


@lru_cache
def get_settings() -> Settings:
    return Settings()
