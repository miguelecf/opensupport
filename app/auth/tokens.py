"""Tokens opacos: sha256 en DB, nunca el plano. Sin JWT, sin OAuth (ver docs/PLAN.md)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from app.db.base import utcnow
from app.domain.enums import SCOPE_READ, SCOPE_WRITE
from app.domain.models import Agent, ApiToken, Project, User

TOKEN_PREFIX = "os_"


def generate_token() -> tuple[str, str]:
    """Devuelve (token_plano, sha256). El plano se muestra una sola vez."""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Principal:
    """Quién está llamando y qué puede hacer. Es lo que resuelve el TokenVerifier."""

    user_id: int
    username: str
    is_admin: bool
    agent_id: int | None = None
    agent_name: str | None = None
    scopes: list[str] = field(default_factory=list)
    project_scope: list[str] = field(default_factory=list)
    token_id: int | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def can_write(self) -> bool:
        return self.has_scope(SCOPE_WRITE)

    @property
    def can_read(self) -> bool:
        return self.has_scope(SCOPE_READ) or self.can_write

    def can_write_project(self, slug: str) -> bool:
        """Vacío = sin restricción (solo para uso interno)."""
        return not self.project_scope or slug in self.project_scope

    @property
    def label(self) -> str:
        return self.agent_name or self.username


def resolve_token(db: OrmSession, plain: str) -> Principal | None:
    """Lookup del token. Devuelve None si no existe, está revocado o expiró."""
    token = db.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_token(plain))
    )
    if token is None or token.revoked_at is not None:
        return None
    user = db.get(User, token.user_id)
    if user is None:
        return None
    agent = db.get(Agent, token.agent_id) if token.agent_id else None
    db.execute(update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=utcnow()))
    return Principal(
        user_id=user.id,
        username=user.name,
        is_admin=user.is_admin,
        agent_id=token.agent_id,
        agent_name=agent.name if agent else None,
        scopes=list(token.scopes or []),
        project_scope=list(token.project_scope or []),
        token_id=token.id,
    )


def create_token(
    db: OrmSession,
    *,
    user: User,
    name: str,
    agent: Agent | None = None,
    scopes: list[str] | None = None,
    project_scope: list[str] | None = None,
) -> tuple[str, ApiToken]:
    plain, digest = generate_token()
    row = ApiToken(
        user_id=user.id,
        agent_id=agent.id if agent else None,
        name=name,
        token_hash=digest,
        scopes=scopes or [SCOPE_READ, SCOPE_WRITE],
        project_scope=project_scope or [],
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return plain, row


def revoke_token(db: OrmSession, token_id: int) -> None:
    db.execute(
        update(ApiToken).where(ApiToken.id == token_id).values(revoked_at=utcnow())
    )


def list_tokens(db: OrmSession, user_id: int) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )
    )


def get_user(db: OrmSession, name: str) -> User | None:
    return db.scalar(select(User).where(User.name == name))


def ensure_user(db: OrmSession, name: str, *, is_admin: bool = False) -> User:
    user = get_user(db, name)
    if user is None:
        user = User(name=name, is_admin=is_admin, created_at=utcnow())
        db.add(user)
        db.flush()
    return user


def all_projects(db: OrmSession) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.slug)))


def token_last_used_note(row: ApiToken) -> str:
    """Texto para la UI de tokens. Sin esto el admin no sabe cuál revocar."""
    last = row.last_used_at
    if last is None:
        return "nunca usado"
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    minutes = int((utcnow() - last).total_seconds() // 60)
    if minutes < 1:
        return "usado hace instantes"
    if minutes < 60:
        return f"usado hace {minutes} min"
    hours = minutes // 60
    return f"usado hace {hours} h" if hours < 24 else f"usado hace {hours // 24} d"
