"""Server credentials and the fixed audio contract."""

from functools import lru_cache
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

V1_SAMPLE_RATE: Final[int] = 16000


class Settings(BaseSettings):
    """Secrets and server settings; behavior lives in the local runtime config."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT", ge=1, le=65535)
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY", repr=False)
    sarvam_api_key: str = Field(alias="SARVAM_API_KEY", min_length=1, repr=False)
    cerebras_api_key: str = Field(alias="CEREBRAS_API_KEY", min_length=1, repr=False)
    cartesia_api_key: str = Field(default="", alias="CARTESIA_API_KEY", repr=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached server settings."""
    return Settings()
