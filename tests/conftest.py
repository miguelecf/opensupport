"""Fixtures compartidos: DB en archivo temporal, app y tokens de test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.orm import Session as OrmSession

from alembic import command

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture()
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = tmp_path / "test.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("OPENSUPPORT_DATABASE_URL", url)

    from app.config import settings

    settings.database_url = url
    settings.database_url = url

    from app.db.base import reset_engine

    reset_engine()

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url
    reset_engine()


@pytest.fixture()
def session(db_url: str) -> Iterator[OrmSession]:
    from app.db.base import session_scope

    with session_scope() as s:
        yield s


@pytest.fixture()
def principal(session: OrmSession):
    """Un agente con token de escritura sobre todos los proyectos."""
    from app.auth.tokens import create_token, ensure_user
    from app.domain.models import Project

    user = ensure_user(session, "fede", is_admin=True)
    project = Project(slug="mi-tesis", name="Mi tesis")
    session.add(project)
    session.flush()
    agent = ensure_agent_for(session, user)
    plain, _ = create_token(
        session,
        user=user,
        name="claude-code-fede",
        agent=agent,
        scopes=["tickets:read", "tickets:write"],
        project_scope=["mi-tesis"],
    )
    principal = _principal_from(session, plain)
    session.commit()
    return principal


def ensure_agent_for(session: OrmSession, user):
    from app.domain.models import Agent

    agent = Agent(user_id=user.id, name="claude-code-fede", client="claude_code")
    session.add(agent)
    session.flush()
    return agent


def _principal_from(session: OrmSession, plain: str):
    from app.auth.tokens import resolve_token

    principal = resolve_token(session, plain)
    assert principal is not None
    return principal


@pytest.fixture()
def reader_principal(session: OrmSession):
    """Token de solo lectura, para probar scopes."""
    from app.auth.tokens import create_token, ensure_user

    user = ensure_user(session, "ana")
    plain, _ = create_token(
        session, user=user, name="solo-lectura", scopes=["tickets:read"], project_scope=[]
    )
    principal = _principal_from(session, plain)
    session.commit()
    return principal


@pytest.fixture()
def token_plain(session: OrmSession, principal) -> str:
    from app.auth.tokens import generate_token

    return generate_token()[0]
