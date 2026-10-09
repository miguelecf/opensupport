"""Engine y sesión de base de datos. PRAGMAs por conexión, un solo worker."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def _apply_pragmas(dbapi_connection: object, _record: object) -> None:
    """PRAGMAs en cada conexión. WAL con fallback a DELETE (volúmenes de red)."""
    cur = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cur.execute("PRAGMA journal_mode = WAL")
        mode = cur.fetchone()[0]
        if str(mode).lower() != "wal":
            logger.warning(
                "journal_mode=WAL no disponible (modo=%s). "
                "Esperado sobre volúmenes de red; ver docs/operations.md.",
                mode,
            )
    except Exception:
        logger.warning("No se pudo activar WAL; continúo con el journal por defecto.", exc_info=True)
    for pragma in ("foreign_keys = ON", "busy_timeout = 5000", "synchronous = NORMAL"):
        cur.execute(f"PRAGMA {pragma}")
    cur.close()


def get_engine() -> Engine:
    global _engine, _sessionmaker
    if _engine is None:
        if settings.database_url.startswith("sqlite:///") and ":memory:" not in settings.database_url:
            settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
            future=True,
        )
        if settings.database_url.startswith("sqlite"):
            event.listen(_engine, "connect", _apply_pragmas)
        _sessionmaker = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional. Commit si sale bien, rollback si lanza."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_is_ready() -> tuple[bool, str]:
    """(listo, detalle). El detalle va a /readyz para que Railway sepa por qué."""
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
            # integrity_check completo en cada readyz sería caro; el plan lo pide
            # porque un disco lleno o un NFS raro producen corrupción silenciosa.
            mode = s.execute(text("PRAGMA journal_mode")).scalar_one()
            size_mb = 0.0
            if settings.database_url.startswith("sqlite:///"):
                path = settings.sqlite_path
                size_mb = path.stat().st_size / 1_048_576 if path.exists() else 0.0
        return True, f"journal={mode} size={size_mb:.1f}MB"
    except Exception:
        logger.warning("DB no accesible", exc_info=True)
        return False, "no conecta"


def db_integrity_check() -> tuple[bool, str]:
    """PRAGMA integrity_check. Caro: se llama desde /readyz, no por request."""
    try:
        with session_scope() as s:
            result = s.execute(text("PRAGMA integrity_check")).scalar_one()
        return result == "ok", str(result)
    except Exception:
        logger.warning("integrity_check falló", exc_info=True)
        return False, "no se pudo ejecutar"


def reset_engine() -> None:
    """Solo para tests: fuerza el recreo del engine."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def ensure_db_path() -> Path:
    path = settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
