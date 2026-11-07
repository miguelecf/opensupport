"""Carga del batch. La promesa es "parcial": un item malo no tira los buenos.

Si esto se rompe, un agente que recorre un repo grande pierde media hora de
trabajo y no se entera de por qué.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.auth.tokens import Principal
from app.db.base import utcnow
from app.domain.models import Project, Ticket, User
from app.tickets import service
from tests.fakes.valid_draft import make_invalid_draft, make_valid_draft


def _principal(db: OrmSession) -> Principal:
    user = db.query(User).filter_by(name="fede").one()
    return Principal(
        user_id=user.id,
        username=user.name,
        is_admin=True,
        agent_id=1,
        agent_name="claude-code-fede",
        scopes=["tickets:read", "tickets:write"],
        project_scope=[],
    )


def _board(db: OrmSession) -> Principal:
    if db.query(Project).filter_by(slug="mi-tesis").one_or_none() is None:
        db.add(Project(slug="mi-tesis", name="Mi tesis", created_at=utcnow()))
        db.flush()
    return _principal(db)


def test_batch_of_100_creates_100_tickets(session: OrmSession, principal) -> None:  # noqa: ANN001
    drafts = [
        make_valid_draft(external_key=f"carga:{i}", title=f"Ticket de carga número {i} con detalle")
        for i in range(100)
    ]
    results = service.create_tickets_batch(session, principal, drafts)
    assert len(results) == 100
    assert all(r.ok for r in results)
    assert session.query(Ticket).count() == 100


def test_one_bad_item_among_100_keeps_the_99(session: OrmSession, principal) -> None:  # noqa: ANN001
    """Este es el caso que importa: un typo no puede tirar el trabajo del agente."""
    drafts = []
    for i in range(100):
        if i == 42:
            drafts.append(make_invalid_draft(external_key=f"carga2:{i}"))
        else:
            drafts.append(
                make_valid_draft(
                    external_key=f"carga2:{i}", title=f"Ticket {i} con detalle suficiente"
                )
            )
    results = service.create_tickets_batch(session, principal, drafts)
    assert len(results) == 100
    created = [r for r in results if r.ok]
    rejected = [r for r in results if not r.ok]
    assert len(created) == 99
    assert len(rejected) == 1
    assert rejected[0].data["index"] == 42
    assert session.query(Ticket).count() == 99


def test_bad_items_are_interleaved_not_just_last(session: OrmSession, principal) -> None:  # noqa: ANN001
    bad_at = {0, 1, 50, 98, 99}
    drafts = [
        make_invalid_draft(external_key=f"carga3:{i}")
        if i in bad_at
        else make_valid_draft(
            external_key=f"carga3:{i}", title=f"Ticket {i} con detalle suficiente"
        )
        for i in range(100)
    ]
    results = service.create_tickets_batch(session, principal, drafts)
    assert sum(1 for r in results if not r.ok) == len(bad_at)
    assert session.query(Ticket).count() == 100 - len(bad_at)
    # El orden se respeta: cada resultado corresponde a su índice.
    for index, result in enumerate(results):
        assert result.data["index"] == index


def test_batch_is_idempotent_when_retried(session: OrmSession, principal) -> None:  # noqa: ANN001
    """Un agente que se corta y vuelve no duplica nada."""
    drafts = [
        make_valid_draft(external_key=f"retry:{i}", title=f"Ticket {i} con detalle suficiente")
        for i in range(20)
    ]
    first = service.create_tickets_batch(session, principal, drafts)
    second = service.create_tickets_batch(session, principal, drafts)
    assert all(r.data["status"] == "created" for r in first)
    assert all(r.data["status"] == "existing" for r in second)
    assert session.query(Ticket).count() == 20


def test_empty_batch_is_fine(session: OrmSession, principal) -> None:  # noqa: ANN001
    assert service.create_tickets_batch(session, principal, []) == []


def test_all_invalid_batch_creates_nothing(session: OrmSession, principal) -> None:  # noqa: ANN001
    drafts = [make_invalid_draft(external_key=f"allbad:{i}") for i in range(10)]
    results = service.create_tickets_batch(session, principal, drafts)
    assert all(not r.ok for r in results)
    assert session.query(Ticket).count() == 0


@pytest.mark.slow
def test_batch_of_200_completes_quickly(session: OrmSession, principal) -> None:  # noqa: ANN001
    """200 tickets tienen que entrar en segundos. Si no, SQLite o el índice se traba."""
    drafts = [
        make_valid_draft(external_key=f"perf:{i}", title=f"Ticket de performance {i} con detalle")
        for i in range(200)
    ]
    start = time.monotonic()
    results = service.create_tickets_batch(session, principal, drafts)
    elapsed = time.monotonic() - start
    assert all(r.ok for r in results)
    assert session.query(Ticket).count() == 200
    assert elapsed < 20, f"200 tickets tardaron {elapsed:.1f}s"


def test_codes_are_sequential_across_a_batch(session: OrmSession, principal) -> None:  # noqa: ANN001
    """El contador no se pisa dentro de un lote: OS-001..OS-050, sin huecos."""
    drafts = [
        make_valid_draft(external_key=f"seq:{i}", title=f"Ticket {i} con detalle suficiente")
        for i in range(50)
    ]
    service.create_tickets_batch(session, principal, drafts)
    codes = sorted(t.code for t in session.query(Ticket).all())
    assert codes == [f"OS-{i:03d}" for i in range(1, 51)]


def test_fts_index_survives_a_large_batch(session: OrmSession, principal) -> None:  # noqa: ANN001
    """Cada insert dispara los triggers de FTS; con 200 filas, el índice tiene que quedar bien."""
    from app.tickets import repository as repo

    drafts = [
        make_valid_draft(external_key=f"fts:{i}", title=f"Marcador único ftslote{i} con detalle")
        for i in range(60)
    ]
    service.create_tickets_batch(session, principal, drafts)
    session.commit()
    hits = repo.search_tickets(session, "ftslote42", limit=5)
    assert [t.external_key for t, _ in hits] == ["fts:42"]
