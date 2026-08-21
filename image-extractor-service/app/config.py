import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    max_pdf_size_mb: int = 50
    default_dpi: int = 200
    max_dpi: int = 300
    max_pixels_before_tile: int = 20_000_000
    max_pages: int = 100
    port: int = 8002

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
