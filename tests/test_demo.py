"""`init --demo` y `lint-stats`: el arranque no puede dejar un board vacío."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.demo import DEMO_TICKET_KEY, demo_draft, seed
from app.tickets.linter import errors, lint
from app.tickets.schema import MAX_ACCEPTANCE, MAX_SCOPE_ITEMS, MAX_STEPS

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_demo_draft_passes_our_own_linter() -> None:
    """Si el ejemplo no pasa el linter, el ejemplo miente."""
    assert lint(demo_draft()) == []


def test_demo_draft_is_under_the_thresholds() -> None:
    """El ejemplo tiene que ser holgado: los tickets reales pueden ser más chicos."""
    draft = demo_draft()
    assert len(draft.steps) <= MAX_STEPS
    assert len(draft.acceptance) <= MAX_ACCEPTANCE
    assert len(draft.scope.include) <= MAX_SCOPE_ITEMS


def test_seed_creates_ticket_and_session(session: OrmSession, principal) -> None:  # noqa: ANN001
    result = seed(session, principal)
    assert result["ticket"] == "OS-001"
    assert result["session"] == "PLN-001"


def test_seed_is_idempotent(session: OrmSession, principal) -> None:  # noqa: ANN001
    seed(session, principal)
    second = seed(session, principal)
    from app.domain.models import Ticket

    assert session.query(Ticket).count() == 1
    assert second["ticket"] is not None


def test_demo_ticket_is_findable_by_search(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets import repository as repo

    seed(session, principal)
    hits = repo.search_tickets(session, "JWT", limit=5)
    assert hits
    assert hits[0][0].external_key == DEMO_TICKET_KEY


def test_lint_stats_reports_nothing_on_a_clean_board(db_url: str, capsys) -> None:  # noqa: ANN001
    from app.cli import main

    assert main(["lint-stats"]) == 0
    out = capsys.readouterr().out
    assert "Sin rechazos registrados" in out


def test_lint_stats_counts_rejections(db_url: str, session: OrmSession, principal, capsys) -> None:  # noqa: ANN001
    from app.cli import main
    from tests.fakes.valid_draft import make_invalid_draft

    for i in range(3):
        service_result = make_invalid_draft(external_key=f"x:{i}")
        from app.tickets import service

        service.create_ticket(session, principal, service_result)
    session.commit()

    assert main(["lint-stats"]) == 0
    out = capsys.readouterr().out
    assert "E_ACCEPTANCE_VAGUE" in out
    assert "Rechazos del linter:" in out


@pytest.mark.parametrize(
    "code",
    ["E_SUMMARY_MISSING", "E_STEPS_MISSING", "E_ACCEPTANCE_VAGUE"],
)
def test_demo_draft_would_trigger(code: str) -> None:
    """Documenta qué reglas cubre el ejemplo: si cambia, este test avisa."""
    from tests.fakes.valid_draft import make_invalid_draft

    found = {v.code for v in errors(lint(make_invalid_draft()))}
    assert code in found
