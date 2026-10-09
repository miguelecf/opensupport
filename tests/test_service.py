"""Tests del servicio: idempotencia, códigos, estados, links y ownership."""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from app.domain.enums import LinkKind, TicketStatus
from app.tickets import service
from tests.fakes.valid_draft import make_invalid_draft, make_valid_draft


def codes(result) -> set[str]:  # noqa: ANN001
    return {v.code for v in result.violations}


# --------------------------------------------------------------------------- #
# Creación e idempotencia
# --------------------------------------------------------------------------- #


def test_create_ticket_starts_ready(session: OrmSession, principal) -> None:  # noqa: ANN001
    result = service.create_ticket(session, principal, make_valid_draft())
    assert result.ok
    assert result.data["status"] == "created"
    assert result.data["code"] == "OS-001"


def test_create_is_idempotent_by_external_key(session: OrmSession, principal) -> None:  # noqa: ANN001
    first = service.create_ticket(session, principal, make_valid_draft())
    second = service.create_ticket(session, principal, make_valid_draft())
    assert first.data["status"] == "created"
    assert second.data["status"] == "existing"
    assert first.data["code"] == second.data["code"]


def test_codes_increment_per_project(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Project

    session.add(Project(slug="otro", name="Otro"))
    session.flush()
    a1 = service.create_ticket(session, principal, make_valid_draft(external_key="k1"))
    a2 = service.create_ticket(session, principal, make_valid_draft(external_key="k2"))
    assert (a1.data["code"], a2.data["code"]) == ("OS-001", "OS-002")


def test_two_projects_can_each_have_os_001(session: OrmSession, principal) -> None:  # noqa: ANN001
    """Cada proyecto arranca su propia secuencia: mi-tesis OS-001 y otro OS-001.

    El código se numera por proyecto pero el UNIQUE de tickets.code era global,
    así que el primer ticket del segundo proyecto moría con IntegrityError.
    """
    from app.auth.tokens import create_token, ensure_user
    from app.domain.models import Project, Ticket

    first = service.create_ticket(session, principal, make_valid_draft(external_key="k1"))

    session.add(Project(slug="otro", name="Otro"))
    session.flush()

    # Token sin project_scope: vacío significa "todos los proyectos".
    wide = ensure_user(session, "ana")
    plain, _ = create_token(
        session, user=wide, name="ancho", scopes=["tickets:read", "tickets:write"], project_scope=[]
    )
    from app.auth.tokens import resolve_token

    scoped = resolve_token(session, plain)
    assert scoped is not None

    second = service.create_ticket(
        session, scoped, make_valid_draft(project="otro", external_key="k2")
    )

    assert first.ok and second.ok
    assert (first.data["code"], second.data["code"]) == ("OS-001", "OS-001")
    rows = session.query(Ticket).filter(Ticket.code == "OS-001").all()
    assert {r.project_id for r in rows} == {1, 2}


def test_invalid_draft_writes_no_row(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Ticket

    result = service.create_ticket(session, principal, make_invalid_draft())
    assert not result.ok
    assert session.query(Ticket).count() == 0


def test_lint_ticket_does_not_persist(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Ticket

    result = service.lint_draft(make_valid_draft())
    assert result.ok
    assert session.query(Ticket).count() == 0


def test_lint_draft_reports_violations(session: OrmSession) -> None:  # noqa: ANN001
    result = service.lint_draft(make_invalid_draft())
    assert not result.ok
    assert "E_ACCEPTANCE_VAGUE" in codes(result)


# --------------------------------------------------------------------------- #
# Batch parcial
# --------------------------------------------------------------------------- #


def test_batch_is_partial(session: OrmSession, principal) -> None:  # noqa: ANN001
    good = make_valid_draft(external_key="b-good")
    bad = make_invalid_draft(external_key="b-bad")
    results = service.create_tickets_batch(session, principal, [good, bad, good.model_copy(update={"external_key": "b-good2"})])
    statuses = [r.ok for r in results]
    assert statuses == [True, False, True]
    assert results[1].data["index"] == 1
    assert "E_ACCEPTANCE_VAGUE" in codes(results[1])


# --------------------------------------------------------------------------- #
# Estados
# --------------------------------------------------------------------------- #


def test_done_requires_close_ticket(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    result = service.set_status(session, principal, code, "done")
    assert not result.ok
    assert "E_CLOSED_NEEDS_EVIDENCE" in codes(result)


def test_close_with_evidence_sets_done(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    service.set_status(session, principal, code, "in_progress")
    result = service.close_ticket(
        session, principal, code, {"pr": "https://github.com/x/y/pull/1", "commands": ["uv run pytest"]}
    )
    assert result.ok
    assert result.data["status"] == TicketStatus.DONE


def test_close_without_verification_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    result = service.close_ticket(session, principal, created.data["code"], {})
    assert not result.ok
    assert "E_VERIFICATION_INCOMPLETE" in codes(result)


def test_invalid_transition_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    result = service.set_status(session, principal, created.data["code"], "blocked")
    assert not result.ok
    assert "E_INVALID_TRANSITION" in codes(result)


def test_optimistic_lock_conflict(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.schema import TicketPatch

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    stale = "2000-01-01T00:00:00+00:00"
    result = service.update_ticket(session, principal, code, TicketPatch(title="Nuevo título"), stale)
    assert not result.ok
    assert "E_CONFLICT" in codes(result)


def test_update_changes_content_hash(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code
    from app.tickets.schema import TicketPatch

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    before = get_ticket_by_code(session, code).content_hash
    service.update_ticket(session, principal, code, TicketPatch(summary="Un resumen totalmente nuevo con suficiente largo para pasar el mínimo."), None)
    after = get_ticket_by_code(session, code).content_hash
    assert before != after


# --------------------------------------------------------------------------- #
# Doble atribución
# --------------------------------------------------------------------------- #


def test_default_owner_is_the_agent_owner(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    ticket = get_ticket_by_code(session, created.data["code"])
    assert ticket.owner_user_id == principal.user_id
    assert ticket.author_agent_id == principal.agent_id


def test_assign_changes_owner_not_author(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.auth.tokens import ensure_user
    from app.tickets.repository import get_ticket_by_code

    ensure_user(session, "ana")
    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    author_before = get_ticket_by_code(session, code).author_agent_id
    result = service.assign_ticket(session, principal, code, "ana")
    assert result.ok
    ticket = get_ticket_by_code(session, code)
    assert ticket.owner.name == "ana"
    assert ticket.author_agent_id == author_before


# --------------------------------------------------------------------------- #
# Scope de proyecto
# --------------------------------------------------------------------------- #


def test_out_of_scope_project_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Project

    session.add(Project(slug="otro", name="Otro"))
    session.flush()
    result = service.create_ticket(session, principal, make_valid_draft(project="otro"))
    assert not result.ok
    assert "E_PROJECT_OUT_OF_SCOPE" in codes(result)
    assert "mi-tesis" in result.violations[0].fix


def test_unknown_project_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    principal.project_scope = []
    result = service.create_ticket(session, principal, make_valid_draft(project="no-existe"))
    assert not result.ok
    assert "E_UNKNOWN_PROJECT" in codes(result)


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #


def test_not_your_ticket_on_update(session: OrmSession, principal, reader_principal) -> None:  # noqa: ANN001
    from app.tickets.schema import TicketPatch

    created = service.create_ticket(session, principal, make_valid_draft())
    result = service.update_ticket(session, reader_principal, created.data["code"], TicketPatch(title="hack"), None)
    assert not result.ok
    assert "E_NOT_YOUR_TICKET" in codes(result)


def test_read_is_not_restricted(session: OrmSession, principal, reader_principal) -> None:  # noqa: ANN001
    from app.tickets import repository as repo

    created = service.create_ticket(session, principal, make_valid_draft())
    found = repo.get_ticket_by_code(session, created.data["code"])
    assert found is not None


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


def test_link_cycle_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    a = service.create_ticket(session, principal, make_valid_draft(external_key="a"))
    b = service.create_ticket(session, principal, make_valid_draft(external_key="b"))
    first = service.link_tickets(session, principal, a.data["code"], b.data["code"], LinkKind.BLOCKS)
    assert first.ok
    second = service.link_tickets(session, principal, b.data["code"], a.data["code"], LinkKind.BLOCKS)
    assert not second.ok
    assert "E_DEPS_CYCLE" in codes(second)


def test_relates_to_does_not_cycle_check(session: OrmSession, principal) -> None:  # noqa: ANN001
    a = service.create_ticket(session, principal, make_valid_draft(external_key="a"))
    b = service.create_ticket(session, principal, make_valid_draft(external_key="b"))
    service.link_tickets(session, principal, a.data["code"], b.data["code"], LinkKind.BLOCKS)
    back = service.link_tickets(session, principal, b.data["code"], a.data["code"], LinkKind.RELATES_TO)
    assert back.ok


def test_self_link_rejected(session: OrmSession, principal) -> None:  # noqa: ANN001
    a = service.create_ticket(session, principal, make_valid_draft())
    result = service.link_tickets(session, principal, a.data["code"], a.data["code"], LinkKind.BLOCKS)
    assert not result.ok
    assert "E_SELF_LINK" in codes(result)


def test_link_by_external_key(session: OrmSession, principal) -> None:  # noqa: ANN001
    a = service.create_ticket(session, principal, make_valid_draft(external_key="dep-a"))
    service.create_ticket(session, principal, make_valid_draft(external_key="dep-b"))
    result = service.link_tickets(session, principal, a.data["code"], "dep-b", LinkKind.BLOCKS)
    assert result.ok


# --------------------------------------------------------------------------- #
# Revisión
# --------------------------------------------------------------------------- #


def test_review_does_not_change_status(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    result = service.review_ticket(session, principal, created.data["code"], "approved", "Está bien")
    assert result.ok
    assert get_ticket_by_code(session, created.data["code"]).status == TicketStatus.READY


def test_changes_requested_requires_note(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    result = service.review_ticket(session, principal, created.data["code"], "changes_requested", None)
    assert not result.ok
    assert "E_REVIEW_NOTE_REQUIRED" in codes(result)


def test_reviews_accumulate_rows(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import TicketReview

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    service.review_ticket(session, principal, code, "changes_requested", "Falta el paso de tests")
    service.review_ticket(session, principal, code, "approved", "Ya está")
    assert session.query(TicketReview).count() == 2


def test_stale_review_detected_after_edit(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code
    from app.tickets.schema import TicketPatch

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    service.review_ticket(session, principal, code, "changes_requested", "Falta algo")
    service.update_ticket(session, principal, code, TicketPatch(summary="Resumen corregido que cambia el hash del ticket por completo."), None)
    summary = service.review_summary(session, get_ticket_by_code(session, code))
    assert summary["needs_review"] is True
    assert summary["historical_count"] == 1
    assert summary["changes_requested"] == 0


def test_ambiguous_ticket_code_asks_for_project(session: OrmSession, principal) -> None:  # noqa: ANN001
    """Dos proyectos con su propio OS-001: sin project=, no se elige por vos.

    Con el UNIQUE por proyecto, ambos códigos existen. Si set_status resolviera
    el primero que encuentre, movería el ticket del proyecto equivocado.
    """
    from app.auth.tokens import create_token, ensure_user
    from app.domain.models import Project, Ticket

    session.add(Project(slug="otro", name="Otro"))
    session.flush()
    principal.project_scope = []
    service.create_ticket(session, principal, make_valid_draft(external_key="k1"))

    wide = ensure_user(session, "ana")
    plain, _ = create_token(session, user=wide, name="ancho", project_scope=[])
    from app.auth.tokens import resolve_token

    scoped = resolve_token(session, plain)
    assert scoped is not None
    service.create_ticket(session, scoped, make_valid_draft(project="otro", external_key="k2"))

    rejected = service.set_status(session, scoped, "OS-001", "in_progress")
    assert not rejected.ok
    violation = rejected.violations[0]
    assert violation.code == "E_AMBIGUOUS_TICKET"
    assert "mi-tesis" in violation.fix and "otro" in violation.fix

    ok = service.set_status(session, scoped, "OS-001", "in_progress", project="otro")
    assert ok.ok

    rows = {t.project_id: str(t.status) for t in session.query(Ticket).all()}
    assert rows == {1: "ready", 2: "in_progress"}
