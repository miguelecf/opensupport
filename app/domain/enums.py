"""Enums del dominio. Los valores son identificadores en inglés; los mensajes van en español."""

from __future__ import annotations

from enum import StrEnum


class TicketStatus(StrEnum):
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    OPEN = "open"
    DONE = "done"
    WONT = "wont"


class TicketType(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    REFACTOR = "refactor"
    CHORE = "chore"
    DOCS = "docs"
    TEST = "test"
    PERF = "perf"
    SECURITY = "security"


class Priority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class LinkKind(StrEnum):
    BLOCKS = "blocks"
    SPLIT_FROM = "split_from"
    RELATES_TO = "relates_to"


class ReviewVerdict(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ParticipantRole(StrEnum):
    FACILITATOR = "facilitator"
    CONTRIBUTOR = "contributor"
    OBSERVER = "observer"


class AgentClient(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    OPENCODE = "opencode"
    OTHER = "other"


class EventKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    COMMENTED = "commented"
    CLOSED = "closed"
    LINT_FAILED = "lint_failed"
    RELINKED = "relinked"
    REVIEWED = "reviewed"


SCOPE_READ = "tickets:read"
SCOPE_WRITE = "tickets:write"
