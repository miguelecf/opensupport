"""Acceso a datos de tickets. Único lugar donde hay SQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.orm import Session as OrmSession

from app.domain.enums import SessionStatus, TicketStatus
from app.domain.models import (
    Comment,
    Project,
    Session,
    Ticket,
    TicketEvent,
    TicketLink,
    TicketReview,
    User,
)


class RepoError(Exception):
    """Error de acceso a datos con código para el agente."""

    def __init__(self, code: str, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix


def get_project_by_slug(db: OrmSession, slug: str) -> Project | None:
    return db.scalar(select(Project).where(Project.slug == slug))


def get_ticket_by_code(db: OrmSession, code: str, project: str | None = None) -> Ticket | None:
    """Busca un ticket por código, acotando a un proyecto si se pasa.

    El código es único por proyecto, no global: sin `project` y con más de una
    coincidencia devolvemos None, y la capa web avisa en vez de mostrar el
    ticket del proyecto equivocado.
    """
    stmt = select(Ticket).where(Ticket.code == code.upper())
    if project is not None:
        stmt = stmt.join(Project).where(Project.slug == project)
        return db.scalar(stmt)
    rows = list(db.scalars(stmt))
    return rows[0] if len(rows) == 1 else None


def projects_for_ticket_code(db: OrmSession, code: str) -> list[str]:
    """Slugs de los proyectos que tienen un ticket con este código."""
    return list(
        db.scalars(
            select(Project.slug)
            .join(Ticket, Ticket.project_id == Project.id)
            .where(Ticket.code == code.upper())
            .order_by(Project.slug)
        )
    )


def tickets_by_code(db: OrmSession, code: str) -> list[tuple[str, Ticket]]:
    """Todos los tickets con este código, con el slug de su proyecto.

    A diferencia de get_ticket_by_code, no desambigua: sirve para explicar
    que el código existe pero en otro proyecto.
    """
    rows = db.execute(
        select(Project.slug, Ticket)
        .join(Ticket, Ticket.project_id == Project.id)
        .where(Ticket.code == code.upper())
        .order_by(Project.slug)
    ).all()
    return [(str(slug), ticket) for slug, ticket in rows]


def get_ticket_by_external_key(db: OrmSession, project_id: int, external_key: str) -> Ticket | None:
    return db.scalar(
        select(Ticket).where(
            and_(Ticket.project_id == project_id, Ticket.external_key == external_key)
        )
    )


def all_projects(db: OrmSession) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.slug)))


def get_user_by_name(db: OrmSession, name: str) -> User | None:
    return db.scalar(select(User).where(User.name == name))


def next_ticket_code(db: OrmSession, project: Project) -> str:
    """Incrementa el contador del proyecto y lo persiste en la transacción actual."""
    seq = project.next_ticket_seq
    project.next_ticket_seq = seq + 1
    db.flush()
    return f"OS-{seq:03d}"


def next_session_code(db: OrmSession, project: Project) -> str:
    """Igual que next_ticket_code, para sesiones."""
    seq = project.next_session_seq
    project.next_session_seq = seq + 1
    db.flush()
    return f"PLN-{seq:03d}"


def get_session_by_code(db: OrmSession, code: str, project: str | None = None) -> Session | None:
    """Busca una sesión por código, acotando a un proyecto si se pasa.

    El código es único por proyecto, no global: dos proyectos pueden tener su
    propia PLN-001. Sin `project` y con más de una coincidencia devolvemos None
    y que la capa de servicio reporte la ambigüedad, en vez de devolver la
    primera y colgar tickets de la sesión equivocada.
    """
    stmt = select(Session).where(Session.code == code.upper())
    if project is not None:
        stmt = stmt.join(Project).where(Project.slug == project)
        return db.scalar(stmt)
    rows = list(db.scalars(stmt))
    return rows[0] if len(rows) == 1 else None


def session_projects_for_code(db: OrmSession, code: str) -> list[str]:
    """Slugs de los proyectos que tienen una sesión con este código."""
    return list(
        db.scalars(
            select(Project.slug)
            .join(Session, Session.project_id == Project.id)
            .where(Session.code == code.upper())
            .order_by(Project.slug)
        )
    )


def _base_query() -> Select[tuple[Ticket]]:
    return select(Ticket)


def list_tickets(
    db: OrmSession,
    *,
    status: str | None = None,
    ticket_type: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    author_agent: str | None = None,
    tag: str | None = None,
    needs_review: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Ticket]:
    query = _base_query().join(Project, Ticket.project_id == Project.id)
    if status:
        query = query.where(Ticket.status == status)
    if ticket_type:
        query = query.where(Ticket.type == ticket_type)
    if priority:
        query = query.where(Ticket.priority == priority)
    if project:
        query = query.where(Project.slug == project)
    if owner:
        query = query.join(User, Ticket.owner_user_id == User.id).where(User.name == owner)
    if author_agent:
        from app.domain.models import Agent

        query = query.join(Agent, Ticket.author_agent_id == Agent.id).where(
            Agent.name == author_agent
        )
    if tag:
        query = query.where(Ticket.tags.contains(tag))
    if needs_review:
        # Tickets sin revisión vigente sobre el content_hash actual.
        reviewed = select(TicketReview.ticket_id).where(
            TicketReview.ticket_content_hash == Ticket.content_hash
        )
        query = query.where(Ticket.id.not_in(reviewed))
    query = query.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(query))


def search_tickets(
    db: OrmSession, query_text: str, limit: int = 20, project: str | None = None
) -> list[tuple[Ticket, str]]:
    """Búsqueda full-text con FTS5. Devuelve (ticket, fragmento).

    `project` acota la búsqueda: los códigos se repiten entre proyectos y
    un hit sin proyecto no se puede usar sin contexto.
    """
    safe = query_text.replace('"', " ")
    params: dict[str, Any] = {"q": safe, "lim": limit}
    extra = ""
    if project:
        extra = "AND t.project_id = (SELECT id FROM projects WHERE slug = :project)"
        params["project"] = project
    rows = db.execute(
        text(
            f"""
            SELECT t.id, snippet(tickets_fts, 1, '<<', '>>', '…', 12) AS frag
            FROM tickets_fts f JOIN tickets t ON t.id = f.rowid
            WHERE tickets_fts MATCH :q {extra}
            ORDER BY rank
            LIMIT :lim
            """
        ),
        params,
    ).all()
    out: list[tuple[Ticket, str]] = []
    for row in rows:
        ticket = db.get(Ticket, row.id)
        if ticket is not None:
            out.append((ticket, row.frag or ""))
    return out


def get_links(db: OrmSession, ticket_id: int) -> list[TicketLink]:
    return list(
        db.scalars(
            select(TicketLink).where(
                or_(TicketLink.from_ticket_id == ticket_id, TicketLink.to_ticket_id == ticket_id)
            )
        )
    )


def get_comments(db: OrmSession, ticket_id: int) -> list[Comment]:
    """Conversación del ticket, en orden cronológico."""
    return list(
        db.scalars(
            select(Comment)
            .where(Comment.ticket_id == ticket_id)
            .order_by(Comment.created_at, Comment.id)
        )
    )


def get_events(
    db: OrmSession, ticket_id: int, limit: int = 100, offset: int = 0
) -> list[TicketEvent]:
    """Historial del ticket, del más reciente al más viejo."""
    return list(
        db.scalars(
            select(TicketEvent)
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def get_reviews(db: OrmSession, ticket_id: int) -> list[TicketReview]:
    return list(
        db.scalars(
            select(TicketReview)
            .where(TicketReview.ticket_id == ticket_id)
            .order_by(TicketReview.created_at.desc())
        )
    )


def blocks_graph(db: OrmSession, project_id: int) -> dict[int, set[int]]:
    """Grafo de dependencias 'blocks': from_ticket_id bloquea a to_ticket_id."""
    rows = db.execute(
        select(TicketLink.from_ticket_id, TicketLink.to_ticket_id)
        .join(Ticket, TicketLink.from_ticket_id == Ticket.id)
        .where(TicketLink.kind == "blocks", Ticket.project_id == project_id)
    ).all()
    graph: dict[int, set[int]] = {}
    for src, dst in rows:
        graph.setdefault(src, set()).add(dst)
    return graph


def open_sessions(db: OrmSession, project_id: int | None = None) -> list[Session]:
    query = select(Session).where(Session.status == SessionStatus.OPEN)
    if project_id is not None:
        query = query.where(Session.project_id == project_id)
    return list(db.scalars(query.order_by(Session.created_at.desc())))


def stats(db: OrmSession, project_id: int | None = None) -> dict[str, Any]:
    query = select(Ticket.status, func.count()).group_by(Ticket.status)
    if project_id is not None:
        query = query.where(Ticket.project_id == project_id)
    by_status = {str(s): int(c) for s, c in db.execute(query).all()}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "ready": by_status.get(TicketStatus.READY, 0),
    }
