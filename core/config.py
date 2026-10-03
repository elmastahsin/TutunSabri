from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field("sqlite+aiosqlite:///./data/app.db", alias="DATABASE_URL")
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    podcast_tts_voice: str = Field(
        "tr-TR-AhmetNeural", alias="PODCAST_TTS_VOICE"
    )
    podcast_tts_rate: str = Field("-5%", alias="PODCAST_TTS_RATE")


settings = Settings()
