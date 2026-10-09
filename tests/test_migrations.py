"""Migraciones: suben desde cero, bajan, y el schema tiene lo que dice el plan."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.db.base import Base

TABLES = {
    "users",
    "agents",
    "api_tokens",
    "projects",
    "sessions",
    "session_participants",
    "tickets",
    "ticket_links",
    "ticket_reviews",
    "comments",
    "ticket_events",
}


@pytest.fixture()
def migrated(tmp_path: Path) -> str:
    from app.config import settings
    from app.db.base import reset_engine

    url = f"sqlite:///{tmp_path / 'm.db'}"
    settings.database_url = url
    reset_engine()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url
    reset_engine()
    settings.database_url = f"sqlite:///{tmp_path / 'after.db'}"


def test_all_eleven_tables_exist(migrated: str) -> None:
    from sqlalchemy import create_engine

    names = set(inspect(create_engine(migrated)).get_table_names())
    assert TABLES <= names


def test_fts_table_and_triggers_exist(migrated: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(migrated)
    names = set(inspect(engine).get_table_names())
    assert "tickets_fts" in names
    triggers = {
        row[0]
        for row in engine.connect().execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'")
        ).all()
    }
    assert {"tickets_fts_ai", "tickets_fts_ad", "tickets_fts_au"} <= triggers


def test_fts_indexes_a_created_ticket(migrated: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(migrated)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id,name,is_admin,next_ticket_seq,created_at) "
                "VALUES (1,'fede',1,1,'2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO projects (id,slug,name,next_session_seq,next_ticket_seq,created_at) "
                "VALUES (1,'p','P',1,1,'2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO tickets (code,project_id,owner_user_id,type,priority,status,title,"
                "summary,outcome,evidence,steps,acceptance,scope,risks,tags,content_hash,"
                "created_at,updated_at) VALUES ('OS-001',1,1,'bug','p1','ready','Error de JWT',"
                "'El endpoint falla al verificar','Arreglar la verificacion','[]','[]','[]','{}','[]','[]',"
                "'h','2026-01-01','2026-01-01')"
            )
        )
        hits = conn.execute(
            text("SELECT code FROM tickets_fts WHERE tickets_fts MATCH 'verificacion'")
        ).all()
    assert [h[0] for h in hits] == ["OS-001"]


def test_check_constraints_reject_both_identities(migrated: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id,name,is_admin,next_ticket_seq,created_at) VALUES (1,'a',0,1,'2026-01-01')")
        )
        conn.execute(
            text("INSERT INTO agents (id,user_id,name,client,created_at) VALUES (1,1,'ag','other','2026-01-01')")
        )
        conn.execute(
            text("INSERT INTO sessions (code,project_id,title,goal,status,created_by_user_id,created_at) "
                 "VALUES ('PLN-001',1,'t','g','open',1,'2026-01-01')")
        )
        conn.execute(
            text("INSERT INTO projects (id,slug,name,next_session_seq,next_ticket_seq,created_at) "
                 "VALUES (1,'p','P',1,1,'2026-01-01')")
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            # session_participants con user_id Y agent_id a la vez viola el CHECK.
            conn.execute(
                text("INSERT INTO session_participants (session_id,user_id,agent_id,role,joined_at) "
                     "VALUES (1,1,1,'contributor','2026-01-01')")
            )


def test_downgrade_removes_everything(migrated: str) -> None:
    from sqlalchemy import create_engine

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migrated)
    command.downgrade(cfg, "base")
    names = set(inspect(create_engine(migrated)).get_table_names())
    assert not (TABLES & names)


def test_model_metadata_matches_migrations(migrated: str) -> None:
    """Si agregás una tabla al modelo y no a la migración, esto falla."""
    assert set(Base.metadata.tables) == TABLES
