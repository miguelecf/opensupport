"""Configuración de la aplicación. Todo sale de variables de entorno con prefijo OPENSUPPORT_."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "opensupport.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENSUPPORT_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{DEFAULT_DB_PATH}"
    secret_key: str = "dev-secret-cambialo-en-produccion"
    session_cookie_name: str = "opensupport_session"
    session_max_age: int = 60 * 60 * 24 * 14  # 14 días
    mcp_required_tokens: bool = True

    # Rate limit por token (requests por minuto)
    rate_limit_writes_per_min: int = 120
    rate_limit_reads_per_min: int = 600

    @property
    def sqlite_path(self) -> Path:
        """Ruta del archivo SQLite. Solo válido para URLs sqlite://."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError(
                "OpenSupport usa SQLite. Si migrás a otro motor, cambiá db/base.py "
                "y revisá el acceso a datos en tickets/repository.py."
            )
        raw = self.database_url[len(prefix) :]
        return Path(raw if raw.startswith("/") else f"./{raw}")


settings = Settings()
