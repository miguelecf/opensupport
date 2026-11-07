"""Redacción de credenciales en los logs.

Regla 4 de AGENTS.md: `Authorization` y `Set-Cookie` nunca se loguean. El
filtro corre sobre headers y también sobre texto libre, porque el modo típico
de filtrar un token es colarlo en un mensaje.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

#: Headers que nunca se emiten tal cual.
SENSITIVE_HEADERS = {
    "authorization",
    "set-cookie",
    "cookie",
    "x-api-key",
    "proxy-authorization",
    "x-csrf-token",
}

#: Tokens opacos de OpenSupport: os_<urlsafe>. Aparecen en logs por accidente
#: cuando alguien depura y pega el valor.
_TOKEN_RE = re.compile(r"\bos_[A-Za-z0-9_-]{16,}")


def redact_text(text: str) -> str:
    """Tacha los tokens opacos que aparezcan en texto libre."""
    return _TOKEN_RE.sub(REDACTED, text)


class RedactingFilter(logging.Filter):
    """Limpia headers sensibles y tokens sueltos antes de que se emitan."""

    def filter(self, record: logging.LogRecord) -> bool:
        # El mensaje se redacta tal cual: si es una plantilla con %s, el token
        # entero vive en los args y se redacta ahí. No aplanamos msg+args ni
        # vaciamos `args`: uvicorn.access espera una tupla y su formatter la
        # desempaqueta — aplanarla rompe el access log con un ValueError.
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: (redact_text(value) if isinstance(value, str) else value)
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_text(value) if isinstance(value, str) else value
                    for value in record.args
                )

        headers = getattr(record, "headers", None)
        if isinstance(headers, dict):
            record.headers = {
                key: (REDACTED if key.lower() in SENSITIVE_HEADERS else redact_text(str(value)))
                for key, value in headers.items()
            }
        return True


_installed = False


def _apply(handler: logging.Handler) -> None:
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())


def configure_logging() -> None:
    """Instala la redacción en los handlers. Idempotente.

    Ojo con el mecanismo: un `logging.Filter` agregado a un **logger** solo
    filtra los records que pasan por ese logger exacto — los hijos no lo heredan.
    El filtro tiene que ir en los **handlers**, que son los que escriben.
    """
    global _installed
    if _installed:
        return
    root = logging.getLogger()
    for handler in root.handlers:
        _apply(handler)
    for name in ("app", "uvicorn", "uvicorn.access", "uvicorn.error", "fastmcp", "mcp"):
        for handler in logging.getLogger(name).handlers:
            _apply(handler)
    _installed = True


def apply_to(handler: logging.Handler) -> None:
    """Para handlers que se agregan después de configure_logging()."""
    _apply(handler)


def filter_installed() -> bool:
    return _installed
