"""Sesiones de planificación. Contexto compartido que los agentes leen y escriben."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.auth.tokens import Principal
from app.db.base import utcnow
from app.domain.enums import ParticipantRole, SessionStatus
from app.domain.models import Session as SessionRow
from app.domain.models import SessionParticipant
from app.tickets import repository as repo
from app.tickets import service as tickets
from app.tickets.schema import Violation


def _v(code: str, field: str, message: str, fix: str = "") -> Violation:
    return Violation(code=code, field=field, message=message, fix=fix)


def start_session(
    db: OrmSession, principal: Principal, project: str, title: str, goal: str
) -> tickets.Result:
    resolved = tickets._resolve_project(db, principal, project)
    if isinstance(resolved, tickets.Result):
        return resolved
    row = SessionRow(
        code=repo.next_session_code(db, resolved),
        project_id=resolved.id,
        title=title.strip(),
        goal=goal.strip(),
        status=SessionStatus.OPEN,
        created_by_user_id=principal.user_id,
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    # Quien abre la sesión ya participa como facilitador.
    _add_participant(db, row.id, principal, ParticipantRole.FACILITATOR)
    return tickets.Result(ok=True, data={"code": row.code, "status": row.status})


def _add_participant(
    db: OrmSession, session_id: int, principal: Principal, role: str
) -> SessionParticipant:
    existing = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.agent_id == principal.agent_id
            if principal.agent_id
            else SessionParticipant.user_id == principal.user_id,
        )
    )
    if existing is not None:
        existing.role = role
        return existing
    row = SessionParticipant(
        session_id=session_id,
        user_id=None if principal.agent_id else principal.user_id,
        agent_id=principal.agent_id,
        role=role,
        joined_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def join_session(
    db: OrmSession, principal: Principal, code: str, role: str = ParticipantRole.CONTRIBUTOR
) -> tickets.Result:
    """Idempotente por diseño: el agente puede llamar esto todas veces que quiera."""
    row = repo.get_session_by_code(db, code)
    if row is None:
        return tickets.Result(
            ok=False,
            violations=[_v("E_UNKNOWN_SESSION", "code", f"La sesión '{code}' no existe.", "Usá list_sessions.")],
        )
    _add_participant(db, row.id, principal, role)
    participants = [
        {"kind": "agent" if p.agent_id else "user", "role": p.role, "id": p.agent_id or p.user_id}
        for p in db.scalars(select(SessionParticipant).where(SessionParticipant.session_id == row.id))
    ]
    session_tickets = [
        {
            "code": t.code,
            "title": t.title,
            "status": t.status,
            "type": t.type,
            "priority": t.priority,
            "owner_user_id": t.owner_user_id,
        }
        for t in repo.list_tickets(db, project=_slug(db, row.project_id), limit=200)
        if t.session_id == row.id
    ]
    return tickets.Result(
        ok=True,
        data={
            "code": row.code,
            "title": row.title,
            "goal": row.goal,
            "status": row.status,
            "read_only": row.status == SessionStatus.CLOSED,
            "participants": participants,
            "tickets": session_tickets,
        },
    )


def close_session(
    db: OrmSession, principal: Principal, code: str  # noqa: ARG001 - cualquiera con write cierra
) -> tickets.Result:
    row = repo.get_session_by_code(db, code)
    if row is None:
        return tickets.Result(
            ok=False,
            violations=[_v("E_UNKNOWN_SESSION", "code", f"La sesión '{code}' no existe.", "Revisá el código.")],
        )
    if row.status == SessionStatus.CLOSED:
        return tickets.Result(ok=True, data={"code": row.code, "status": row.status, "tickets_count": 0})
    count = len([t for t in repo.list_tickets(db, project=_slug(db, row.project_id), limit=500) if t.session_id == row.id])
    row.status = SessionStatus.CLOSED
    row.closed_at = utcnow()
    return tickets.Result(ok=True, data={"code": row.code, "status": row.status, "tickets_count": count})


def list_sessions(
    db: OrmSession, project: str | None = None, status: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    rows = repo.open_sessions(db) if status is None else [
        r for r in repo.open_sessions(db) if r.status == status
    ]
    out = []
    for row in rows:
        if project and _slug(db, row.project_id) != project:
            continue
        out.append({"code": row.code, "title": row.title, "goal": row.goal, "status": row.status})
    return out[:limit]


def _slug(db: OrmSession, project_id: int) -> str:
    from app.domain.models import Project

    project = db.get(Project, project_id)
    return project.slug if project else ""


def snapshot_markdown(db: OrmSession, principal: Principal, code: str) -> str:  # noqa: ARG001
    """El resource opensupport://session/{code}: mismo dato, en markdown."""
    context = join_session(db, principal, code)
    if not context.ok:
        return f"# Sesión {code}\n\nNo existe.\n"
    lines: list[str] = [
        f"# {context.data['code']} — {context.data['title']}",
        "",
        f"**Objetivo:** {context.data['goal']}",
        "",
        f"**Estado:** {context.data['status']}"
        + (" (solo lectura)" if context.data["read_only"] else ""),
        "",
        "## Participantes",
        "",
    ]
    if context.data["participants"]:
        for p in context.data["participants"]:
            lines.append(f"- {p['kind']} #{p['id']} — {p['role']}")
    else:
        lines.append("(ninguno)")
    lines += ["", "## Tickets de la sesión", ""]
    if context.data["tickets"]:
        for t in context.data["tickets"]:
            lines.append(f"- **{t['code']}** [{t['status']}] {t['title']}")
    else:
        lines.append("(ninguno todavía)")
    return "\n".join(lines) + "\n"
