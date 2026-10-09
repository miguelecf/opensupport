"""Sesiones de planificación: contexto compartido, join idempotente, cierre."""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from app.domain.enums import ParticipantRole, SessionStatus
from app.sessions import service as sessions
from app.tickets import service as tickets
from tests.fakes.valid_draft import make_valid_draft


def test_start_session_creates_pln_code(session: OrmSession, principal) -> None:  # noqa: ANN001
    result = sessions.start_session(session, principal, "mi-tesis", "Auth: migrar a JWT", "Que /login no use sesiones en memoria")
    assert result.ok
    assert result.data["code"] == "PLN-001"


def test_session_codes_are_per_project(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Project

    session.add(Project(slug="otro", name="Otro"))
    session.flush()
    principal.project_scope = []
    a = sessions.start_session(session, principal, "mi-tesis", "A", "objetivo a")
    b = sessions.start_session(session, principal, "otro", "B", "objetivo b")
    assert a.data["code"] == "PLN-001"
    assert b.data["code"] == "PLN-001"


def _two_sessions_with_same_code(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Project

    session.add(Project(slug="otro", name="Otro"))
    session.flush()
    principal.project_scope = []
    sessions.start_session(session, principal, "mi-tesis", "A", "objetivo a")
    sessions.start_session(session, principal, "otro", "B", "objetivo b")


def test_ambiguous_session_code_is_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    """PLN-001 existe en dos proyectos: sin project no se puede elegir."""
    _two_sessions_with_same_code(session, principal)

    result = sessions.join_session(session, principal, "PLN-001")

    assert not result.ok
    violation = result.violations[0]
    assert violation.code == "E_AMBIGUOUS_SESSION"
    assert "mi-tesis" in violation.fix and "otro" in violation.fix


def test_project_disambiguates_session_code(session: OrmSession, principal) -> None:  # noqa: ANN001
    _two_sessions_with_same_code(session, principal)

    assert sessions.join_session(session, principal, "PLN-001", project="otro").ok
    assert sessions.join_session(session, principal, "PLN-001", project="mi-tesis").ok


def test_close_session_needs_project_when_ambiguous(session: OrmSession, principal) -> None:  # noqa: ANN001
    _two_sessions_with_same_code(session, principal)

    rejected = sessions.close_session(session, principal, "PLN-001")
    assert not rejected.ok
    assert rejected.violations[0].code == "E_AMBIGUOUS_SESSION"

    closed = sessions.close_session(session, principal, "PLN-001", project="otro")
    assert closed.ok
    # La de mi-tesis sigue abierta: cerrarla era la de car-adm.
    assert sessions.close_session(session, principal, "PLN-001", project="mi-tesis").ok


def test_ticket_uses_the_session_of_its_own_project(session: OrmSession, principal) -> None:  # noqa: ANN001
    """create_ticket resuelve `session` con el proyecto del draft, no el primero que encuentre."""
    _two_sessions_with_same_code(session, principal)

    result = tickets.create_ticket(
        session, principal, make_valid_draft(project="otro", external_key="k1", session="PLN-001")
    )
    assert result.ok

    from app.domain.models import Session, Ticket

    ticket = session.query(Ticket).filter(Ticket.code == "OS-001").one()
    owner = session.query(Session).filter(Session.id == ticket.session_id).one()
    assert owner.project_id == 2


def test_join_session_is_idempotent(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    code = created.data["code"]
    first = sessions.join_session(session, principal, code)
    second = sessions.join_session(session, principal, code)
    assert first.ok and second.ok
    assert first.data["participants"] == second.data["participants"]
    assert len(second.data["participants"]) == 1


def test_participant_is_user_xor_agent(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    sessions.join_session(session, principal, created.data["code"], ParticipantRole.CONTRIBUTOR)
    from app.domain.models import SessionParticipant

    row = session.query(SessionParticipant).one()
    assert (row.user_id is None) != (row.agent_id is None)
    assert row.agent_id == principal.agent_id


def test_join_returns_existing_tickets(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    code = created.data["code"]
    sessions.join_session(session, principal, code)
    draft = make_valid_draft(session=code, external_key="k1")
    tickets.create_ticket(session, principal, draft)
    context = sessions.join_session(session, principal, code)
    assert context.ok
    assert len(context.data["tickets"]) == 1
    assert context.data["tickets"][0]["title"].startswith("Extraer")


def test_ticket_in_closed_session_is_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    code = created.data["code"]
    sessions.close_session(session, principal, code)
    result = tickets.create_ticket(session, principal, make_valid_draft(session=code))
    assert not result.ok
    assert "E_SESSION_CLOSED" in {v.code for v in result.violations}


def test_join_closed_session_is_read_only(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    code = created.data["code"]
    tickets.create_ticket(session, principal, make_valid_draft(session=code, external_key="k1"))
    sessions.close_session(session, principal, code)
    context = sessions.join_session(session, principal, code)
    assert context.ok
    assert context.data["read_only"] is True
    assert len(context.data["tickets"]) == 1


def test_unknown_session_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    result = tickets.create_ticket(session, principal, make_valid_draft(session="PLN-999"))
    assert not result.ok
    assert "E_UNKNOWN_SESSION" in {v.code for v in result.violations}


def test_session_out_of_project_scope(session: OrmSession, principal) -> None:  # noqa: ANN001
    result = sessions.start_session(session, principal, "otro", "T", "objetivo")
    assert not result.ok
    assert "E_PROJECT_OUT_OF_SCOPE" in {v.code for v in result.violations}


def test_close_session_sets_status(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = sessions.start_session(session, principal, "mi-tesis", "T", "objetivo")
    result = sessions.close_session(session, principal, created.data["code"])
    assert result.ok
    assert result.data["status"] == SessionStatus.CLOSED
