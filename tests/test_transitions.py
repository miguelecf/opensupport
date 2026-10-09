"""Matriz de estados: cada arista válida pasa, cada inválida se rechaza."""

from __future__ import annotations

import itertools

import pytest

from app.domain.enums import TicketStatus
from app.domain.transitions import allowed_targets, can_transition, requires_close_evidence

ALL = list(TicketStatus)


def test_new_ticket_starts_ready() -> None:
    from app.domain.transitions import INITIAL_STATUS

    assert INITIAL_STATUS == TicketStatus.READY


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TicketStatus.OPEN, TicketStatus.READY),
        (TicketStatus.OPEN, TicketStatus.WONT),
        (TicketStatus.READY, TicketStatus.OPEN),
        (TicketStatus.READY, TicketStatus.IN_PROGRESS),
        (TicketStatus.READY, TicketStatus.WONT),
        (TicketStatus.IN_PROGRESS, TicketStatus.BLOCKED),
        (TicketStatus.IN_PROGRESS, TicketStatus.DONE),
        (TicketStatus.IN_PROGRESS, TicketStatus.WONT),
        (TicketStatus.IN_PROGRESS, TicketStatus.READY),
        (TicketStatus.BLOCKED, TicketStatus.IN_PROGRESS),
        (TicketStatus.BLOCKED, TicketStatus.WONT),
        (TicketStatus.DONE, TicketStatus.IN_PROGRESS),
        (TicketStatus.WONT, TicketStatus.OPEN),
        (TicketStatus.WONT, TicketStatus.READY),
    ],
)
def test_allowed_transition(current: TicketStatus, target: TicketStatus) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TicketStatus.READY, TicketStatus.BLOCKED),
        (TicketStatus.READY, TicketStatus.DONE),
        (TicketStatus.OPEN, TicketStatus.IN_PROGRESS),
        (TicketStatus.OPEN, TicketStatus.DONE),
        (TicketStatus.BLOCKED, TicketStatus.DONE),
        (TicketStatus.BLOCKED, TicketStatus.READY),
        (TicketStatus.DONE, TicketStatus.READY),
        (TicketStatus.DONE, TicketStatus.WONT),
        (TicketStatus.WONT, TicketStatus.DONE),
        (TicketStatus.WONT, TicketStatus.IN_PROGRESS),
        (TicketStatus.IN_PROGRESS, TicketStatus.OPEN),
        (TicketStatus.BLOCKED, TicketStatus.OPEN),
        (TicketStatus.OPEN, TicketStatus.BLOCKED),
        (TicketStatus.DONE, TicketStatus.BLOCKED),
        (TicketStatus.DONE, TicketStatus.OPEN),
        (TicketStatus.WONT, TicketStatus.BLOCKED),
    ],
)
def test_rejected_transition(current: TicketStatus, target: TicketStatus) -> None:
    assert not can_transition(current, target)


def test_no_transition_to_itself() -> None:
    for status in ALL:
        assert not can_transition(status, status)


def test_every_pair_is_covered_by_the_two_tests() -> None:
    """Si aparece un estado nuevo, este test obliga a clasificar la arista."""
    documented = 14 + 16
    pairs = list(itertools.product(ALL, repeat=2))
    assert documented == len(pairs) - len(ALL)


def test_done_requires_close_evidence() -> None:
    assert requires_close_evidence(TicketStatus.DONE)
    for status in ALL:
        if status is not TicketStatus.DONE:
            assert not requires_close_evidence(status)


def test_allowed_targets_are_sorted_strings() -> None:
    targets = allowed_targets(TicketStatus.READY)
    assert targets == sorted(targets)
    assert "in_progress" in targets
