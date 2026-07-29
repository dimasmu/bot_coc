"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "COC_", "env_file": ".env", "extra": "ignore"}

    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "storage/coc_bot.db"
    template_dir: str = "storage/templates"
    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    screen_width: int = 1280
    screen_height: int = 720
    screen_dpi: int = 240
    screencap_fps: int = 15
    log_level: str = "INFO"


settings = Settings()
