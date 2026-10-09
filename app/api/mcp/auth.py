"""Auth MCP: token opaco contra SQLite. Sin OAuth ni JWT (ver docs/PLAN.md).

La spec MCP marca la autorización como OPTIONAL y el flujo OAuth 2.1 como SHOULD.
Acá usamos un token opaco: todos los consumidores son del mismo equipo y ya
saben su token, así que discovery no aporta nada.
"""

from __future__ import annotations

import logging
from typing import Any

from anyio import to_thread
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from app.auth.tokens import Principal, resolve_token
from app.db.base import session_scope

logger = logging.getLogger(__name__)

SCOPE_READ = "tickets:read"
SCOPE_WRITE = "tickets:write"


def _resolve_sync(plain: str) -> Principal | None:
    with session_scope() as db:
        return resolve_token(db, plain)


class SqliteTokenVerifier(TokenVerifier):
    """Valida tokens opacos contra api_tokens."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        # FastMCP exige una base_url válida (se usa en WWW-Authenticate), aunque
        # con un TokenVerifier propio no publicamos metadata de OAuth.
        super().__init__(base_url=base_url, required_scopes=[])

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = await to_thread.run_sync(_resolve_sync, token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id=principal.label,
            scopes=list(principal.scopes),
            claims={
                "user_id": principal.user_id,
                "username": principal.username,
                "is_admin": principal.is_admin,
                "agent_id": principal.agent_id,
                "agent_name": principal.agent_name,
                "project_scope": principal.project_scope,
                "label": principal.label,
            },
        )


def current_principal() -> Principal:
    """Principal de la llamada en curso. Lanza PermissionError si no hay token."""
    access = get_access_token()
    if access is None:
        raise PermissionError("Falta el header Authorization: Bearer os_...")
    claims: dict[str, Any] = dict(access.claims or {})
    return Principal(
        user_id=int(claims.get("user_id", 0)),
        username=str(claims.get("username", "")),
        is_admin=bool(claims.get("is_admin", False)),
        agent_id=claims.get("agent_id"),
        agent_name=claims.get("agent_name"),
        scopes=list(access.scopes or []),
        project_scope=list(claims.get("project_scope", [])),
    )


def enforce_rate_limit(kind: str, principal: Principal | None = None) -> None:
    """Corta un token quemado. El error dice cuánto esperar."""
    from fastmcp.exceptions import ToolError

    from app.ratelimit import get_limiter

    who = principal if principal is not None else current_principal()
    ident = str(who.token_id or who.user_id)
    allowed, retry_after = get_limiter().check(kind, ident)
    if not allowed:
        raise ToolError(
            f"Rate limit: superaste el límite de {kind}. Reintentá en {retry_after}s."
        )


def require_write() -> Principal:
    """Scope de escritura + rate limit de escritura, en un solo lugar.

    Que el límite viva acá y no en cada tool es a propósito: si hay que
    acordarse de llamarlo, algún día se olvida en una tool nueva.
    """
    principal = current_principal()
    if not principal.can_write:
        raise PermissionError(
            "Tu token no tiene el scope 'tickets:write'. "
            "Pedí uno con escritura desde /settings/tokens."
        )
    enforce_rate_limit("write")
    return principal


def require_read() -> Principal:
    principal = current_principal()
    if not principal.can_read:
        raise PermissionError(
            "Tu token no tiene el scope 'tickets:read'. Pedí uno desde /settings/tokens."
        )
    enforce_rate_limit("read")
    return principal
