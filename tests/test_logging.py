"""Redacción de credenciales. Regla 4 de AGENTS.md: los tokens nunca se loguean."""

from __future__ import annotations

import io
import logging

from app.logging_setup import REDACTED, RedactingFilter, redact_text


def _record(msg: str, headers: dict[str, str] | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def _emit(headers: dict[str, str] | None = None, msg: str = "req") -> logging.LogRecord:
    """Pasa un record por el filtro y lo devuelve mutado."""
    record = _record(msg)
    if headers is not None:
        record.headers = headers  # type: ignore[attr-defined]
    RedactingFilter().filter(record)
    return record


def test_authorization_header_is_redacted() -> None:
    record = _emit({"Authorization": "Bearer os_" + "a" * 32})
    assert (REDACTED,) == tuple(record.headers.values())  # type: ignore[attr-defined]


def test_case_insensitive_header_names() -> None:
    record = _emit({"authorization": "Bearer os_" + "a" * 32})
    assert REDACTED in record.headers.values()  # type: ignore[attr-defined]


def test_set_cookie_is_redacted() -> None:
    record = _emit({"Set-Cookie": "opensupport_session=abc123def456; HttpOnly"})
    assert "abc123def456" not in str(record.headers)  # type: ignore[attr-defined]


def test_cookies_are_redacted() -> None:
    record = _emit({"Cookie": "opensupport_session=xyz789abc"})
    assert "xyz789abc" not in str(record.headers)  # type: ignore[attr-defined]


def test_x_csrf_token_is_redacted() -> None:
    record = _emit({"X-CSRF-Token": "abc123"})
    assert REDACTED in record.headers.values()  # type: ignore[attr-defined]


def test_normal_headers_survive() -> None:
    record = _emit({"Content-Type": "application/json", "User-Agent": "ClaudeCode/1.0"})
    assert record.headers == {  # type: ignore[attr-defined]
        "Content-Type": "application/json",
        "User-Agent": "ClaudeCode/1.0",
    }


def test_token_in_free_text_is_redacted() -> None:
    """El modo típico de filtrar un token es colarlo en un mensaje."""
    record = _emit(msg=f"el token es os_{'a' * 32} no lo pegues")
    assert "a" * 32 not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_short_os_prefix_is_not_over_redacted() -> None:
    """os_ corto puede ser una coincidencia legítima; no lo tachamos."""
    assert redact_text("os_corto") == "os_corto"


def test_real_message_with_args_is_redacted() -> None:
    record = _record("token %s", (f"os_{'b' * 32}",))
    RedactingFilter().filter(record)
    assert "b" * 32 not in record.getMessage()


def test_filter_lands_on_handlers_not_loggers() -> None:
    """Los filtros de logger no se heredan: tiene que estar en los handlers."""
    from app.logging_setup import apply_to, configure_logging

    configure_logging()
    handler = logging.StreamHandler(io.StringIO())
    assert not any(isinstance(f, RedactingFilter) for f in handler.filters)
    apply_to(handler)
    assert any(isinstance(f, RedactingFilter) for f in handler.filters)


def test_child_logger_is_still_redacted() -> None:
    """app.leak hereda de app pero su record igual tiene que pasar por el filtro."""
    from app.logging_setup import RedactingFilter as RF

    record = _record(f"token os_{'c' * 32}")
    RF().filter(record)
    assert "c" * 32 not in record.getMessage()


def test_server_logs_never_contain_the_plain_token(db_url: str) -> None:
    """End-to-end: el token que imprime init --demo no termina en el log."""
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope
    from app.logging_setup import configure_logging

    configure_logging()
    with session_scope() as db:
        user = ensure_user(db, "fede")
        plain, _ = create_token(db, user=user, name="leak-check")
        db.commit()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())  # así está configurado en el server
    logger = logging.getLogger("app.leak")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("creé el token %s para el agente", plain)

    assert plain not in stream.getvalue()
    assert REDACTED in stream.getvalue()
