"""Máquina de estados de tickets. Función pura, sin DB: TDD estricto."""

from __future__ import annotations

from app.domain.enums import TicketStatus

_ALLOWED: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset({TicketStatus.READY, TicketStatus.WONT}),
    TicketStatus.READY: frozenset({TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.WONT}),
    TicketStatus.IN_PROGRESS: frozenset(
        {TicketStatus.BLOCKED, TicketStatus.DONE, TicketStatus.WONT, TicketStatus.READY}
    ),
    TicketStatus.BLOCKED: frozenset({TicketStatus.IN_PROGRESS, TicketStatus.WONT}),
    TicketStatus.DONE: frozenset({TicketStatus.IN_PROGRESS}),
    TicketStatus.WONT: frozenset({TicketStatus.OPEN, TicketStatus.READY}),
}

#: `done` solo se alcanza vía close_ticket con verificación, nunca con set_status.
NEEDS_CLOSE_EVIDENCE = TicketStatus.DONE

INITIAL_STATUS = TicketStatus.READY


def can_transition(current: TicketStatus, target: TicketStatus) -> bool:
    """¿La transición está permitida por la matriz?"""
    return target in _ALLOWED[current]


def allowed_targets(current: TicketStatus) -> list[str]:
    return sorted(str(s) for s in _ALLOWED[current])


def requires_close_evidence(target: TicketStatus) -> bool:
    return target == NEEDS_CLOSE_EVIDENCE
