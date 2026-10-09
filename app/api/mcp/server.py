"""Servidor MCP: 17 tools + 6 resources. Montado en /mcp dentro de la app FastAPI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from anyio import to_thread
from fastmcp import FastMCP
from sqlalchemy.orm import Session as OrmSession

from app.api.mcp.auth import SqliteTokenVerifier, require_read, require_write
from app.db.base import session_scope
from app.domain.enums import LinkKind, ReviewVerdict, TicketStatus
from app.sessions import service as sessions
from app.tickets import repository as repo
from app.tickets import service as tickets
from app.tickets.schema import (
    MAX_ACCEPTANCE,
    MAX_SCOPE_ITEMS,
    MAX_STEPS,
    MAX_TITLE,
    MIN_OUTCOME,
    MIN_SUMMARY,
    TicketDraft,
    TicketPatch,
)

SKILL_PATH = Path(__file__).resolve().parents[2] / "skill" / "ticket-authoring.md"

INSTRUCTIONS = """\
OpenSupport es un board de tickets para equipos donde los agentes de código hacen
el trabajo de analisis. Tu flujo normal es:

1. resources/read de opensupport://skill/ticket-authoring  (el contrato de redaccion)
2. search_tickets() para no duplicar lo que ya esta en el board
3. lint_ticket() con tu borrador, corregir, repetir hasta ok: true
4. create_ticket() o create_tickets_batch() cuando pasa

Cuando implementes algo: set_status(code, "in_progress"), y al terminar
close_ticket(code, verification={pr|commit, commands: [...]}) — 'done' no se
puede setear directo, hace falta evidencia.

Las violaciones de validacion no son excepciones: vienen en el resultado con
{code, field, message, fix}. Leelas y corregi el borrador.
"""

mcp = FastMCP(
    name="OpenSupport",
    instructions=INSTRUCTIONS,
    version="0.1.0",
    auth=SqliteTokenVerifier(),
)


async def _run_in_thread[T](fn: Callable[[OrmSession], T]) -> T:
    """Ejecuta trabajo de DB en un hilo. Nunca bloquea el event loop.

    Recibe un callable que toma la sesión y devuelve el resultado. El closure
    corre completo dentro del hilo, con su propia transacción.
    """
    def _call() -> T:
        with session_scope() as session:
            return fn(session)

    return await to_thread.run_sync(_call)


# --------------------------------------------------------------------------- #
# Tools de lectura
# --------------------------------------------------------------------------- #


@mcp.tool(description="Devuelve un ticket por su código (OS-003), con revisiones e historial.")
async def get_ticket(code: str) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        ticket = repo.get_ticket_by_code(db, code)
        if ticket is None:
            return {"ok": False, "error": f"El ticket '{code}' no existe."}
        data = {
            "code": ticket.code,
            "project": ticket.project.slug,
            "session": ticket.session.code if ticket.session else None,
            "status": ticket.status,
            "type": ticket.type,
            "priority": ticket.priority,
            "title": ticket.title,
            "summary": ticket.summary,
            "problem": ticket.problem,
            "outcome": ticket.outcome,
            "evidence": ticket.evidence,
            "steps": ticket.steps,
            "acceptance": ticket.acceptance,
            "scope": ticket.scope,
            "risks": ticket.risks,
            "tags": ticket.tags,
            "estimate": ticket.estimate,
            "verification": ticket.verification,
            "owner_user_id": ticket.owner_user_id,
            "author_agent_id": ticket.author_agent_id,
            "external_key": ticket.external_key,
            "content_hash": ticket.content_hash,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        }
        data["review"] = tickets.review_summary(db, ticket)
        data["links"] = [
            {"kind": link.kind, "other": link.to_ticket_id if link.from_ticket_id == ticket.id else link.from_ticket_id}
            for link in repo.get_links(db, ticket.id)
        ]
        return {"ok": True, "ticket": data}

    return await _run_in_thread(_run)


@mcp.tool(description="Lista tickets con filtros. El equipo lee todos los tickets del board.")
async def list_tickets(
    status: str | None = None,
    type: str | None = None,  # noqa: A002 - es el nombre del parámetro público
    priority: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    author_agent: str | None = None,
    tag: str | None = None,
    needs_review: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        rows = repo.list_tickets(
            db,
            status=status,
            ticket_type=type,
            priority=priority,
            project=project,
            owner=owner,
            author_agent=author_agent,
            tag=tag,
            needs_review=needs_review,
            limit=limit,
            offset=offset,
        )
        return {
            "ok": True,
            "count": len(rows),
            "items": [
                {
                    "code": t.code,
                    "title": t.title,
                    "status": t.status,
                    "type": t.type,
                    "priority": t.priority,
                    "project": t.project.slug,
                    "session": t.session.code if t.session else None,
                    "owner_user_id": t.owner_user_id,
                    "tags": t.tags,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in rows
            ],
        }

    return await _run_in_thread(_run)


@mcp.tool(description="Busca tickets por texto (FTS5) en título, resumen, outcome y criterios.")
async def search_tickets(query: str, limit: int = 20) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        hits = repo.search_tickets(db, query, limit)
        return {
            "ok": True,
            "items": [
                {"code": t.code, "title": t.title, "status": t.status, "fragment": frag}
                for t, frag in hits
            ],
        }

    return await _run_in_thread(_run)


@mcp.tool(description="JSON Schema del contrato de ticket + la tabla de violaciones con su fix.")
async def get_schema() -> dict[str, Any]:
    require_read()
    return {
        "ok": True,
        "json_schema": TicketDraft.model_json_schema(),
        "thresholds": {
            "max_title": MAX_TITLE,
            "min_summary": MIN_SUMMARY,
            "min_outcome": MIN_OUTCOME,
            "max_steps": MAX_STEPS,
            "max_acceptance": MAX_ACCEPTANCE,
            "max_scope_include": MAX_SCOPE_ITEMS,
        },
        "statuses": [str(s) for s in TicketStatus],
        "link_kinds": [str(k) for k in LinkKind],
        "verdicts": [str(v) for v in ReviewVerdict],
        "violations": _violation_table(),
    }


# --------------------------------------------------------------------------- #
# Tools de escritura
# --------------------------------------------------------------------------- #


@mcp.tool(
    description=(
        "Valida un borrador SIN guardarlo. Iterá hasta ok: true antes de crear. "
        "Devuelve las violaciones con su código y la instrucción de corrección."
    )
)
async def lint_ticket(
    project: str,
    title: str,
    summary: str,
    outcome: str,
    evidence: list[dict[str, Any]] | None = None,
    steps: list[str] | None = None,
    acceptance: list[str] | None = None,
    scope: dict[str, list[str]] | None = None,
    problem: str | None = None,
    type: str = "feature",  # noqa: A002
    priority: str = "p2",
    risks: list[str] | None = None,
    tags: list[str] | None = None,
    external_key: str | None = None,
    owner_user: str | None = None,
    estimate: str | None = None,
    deps: list[str] | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    """Acepta exactamente los mismos campos que create_ticket.

    Si agregás un parámetro acá, agregalo también allá. Un agente lintea el
    mismo objeto que va a crear: si lint_ticket rechaza un campo que
    create_ticket acepta, el loop se rompe.
    """
    require_write()
    draft = _draft_from_args(
        project=project,
        title=title,
        summary=summary,
        outcome=outcome,
        evidence=evidence,
        steps=steps,
        acceptance=acceptance,
        scope=scope,
        problem=problem,
        type=type,
        priority=priority,
        risks=risks,
        tags=tags,
        external_key=external_key,
        owner_user=owner_user,
        estimate=estimate,
        deps=deps,
        session=session,
    )
    result = tickets.lint_draft(draft)
    return result.to_dict()


@mcp.tool(
    description=(
        "Crea un ticket validado. Idempotente por external_key: si ya existe, "
        "devuelve el existente en lugar de duplicarlo. Nace en estado 'ready'."
    )
)
async def create_ticket(
    project: str,
    title: str,
    summary: str,
    outcome: str,
    evidence: list[dict[str, Any]] | None = None,
    steps: list[str] | None = None,
    acceptance: list[str] | None = None,
    scope: dict[str, list[str]] | None = None,
    problem: str | None = None,
    type: str = "feature",  # noqa: A002
    priority: str = "p2",
    risks: list[str] | None = None,
    tags: list[str] | None = None,
    external_key: str | None = None,
    owner_user: str | None = None,
    session: str | None = None,
    estimate: str | None = None,
    deps: list[str] | None = None,
) -> dict[str, Any]:
    require_write()
    draft = _draft_from_args(
        project=project,
        title=title,
        summary=summary,
        outcome=outcome,
        evidence=evidence,
        steps=steps,
        acceptance=acceptance,
        scope=scope,
        problem=problem,
        type=type,
        priority=priority,
        risks=risks,
        tags=tags,
        external_key=external_key,
        owner_user=owner_user,
        session=session,
        estimate=estimate,
        deps=deps,
    )
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.create_ticket(db, principal, draft).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Crea varios tickets de una. Es PARCIAL a propósito: un item inválido no "
        "tira los válidos. Devuelve un resultado por item, en el mismo orden."
    )
)
async def create_tickets_batch(
    items: list[dict[str, Any]],
    session: str | None = None,
) -> dict[str, Any]:
    require_write()
    principal = require_write()
    drafts = [_draft_from_dict(item, session=session) for item in items]

    def _run(db: OrmSession) -> dict[str, Any]:
        results = tickets.create_tickets_batch(db, principal, drafts, session=session)
        return {"ok": True, "results": [r.to_dict() for r in results]}

    return await _run_in_thread(_run)


@mcp.tool(description="Actualiza un ticket. Mandá expected_updated_at para evitar pisar a otro agente.")
async def update_ticket(
    code: str,
    patch: dict[str, Any],
    expected_updated_at: str | None = None,
) -> dict[str, Any]:
    require_write()
    principal = require_write()
    model = TicketPatch(**patch)

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.update_ticket(db, principal, code, model, expected_updated_at).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(description="Cambia quién responde el ticket. No cambia quién lo escribió.")
async def assign_ticket(code: str, owner_user: str) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.assign_ticket(db, principal, code, owner_user).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(description="Vincula dos tickets: blocks (dependencia), split_from o relates_to.")
async def link_tickets(from_code: str, to: str, kind: str = "relates_to") -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.link_tickets(db, principal, from_code, to, LinkKind(kind)).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Cambia el estado. No acepta 'done': para cerrar usá close_ticket con evidencia."
    )
)
async def set_status(code: str, status: str, note: str | None = None) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.set_status(db, principal, code, status, note).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(description="Agrega un comentario al ticket. Cualquiera del equipo puede comentar.")
async def add_comment(code: str, body: str) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.add_comment(db, principal, code, body).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Cierra el ticket con evidencia de que se implementó. "
        "Exige pr o commit, y al menos un comando de verificación."
    )
)
async def close_ticket(
    code: str,
    pr: str | None = None,
    commit: str | None = None,
    commands: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    require_write()
    principal = require_write()
    verification = {
        "pr": pr,
        "commit": commit,
        "commands": commands or [],
        "notes": notes,
    }

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.close_ticket(db, principal, code, verification).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Registra un veredicto de revisión (approved o changes_requested). "
        "NO cambia el status: la revisión informa, no bloquea. "
        "changes_requested exige una nota diciendo qué cambiar."
    )
)
async def review_ticket(
    code: str,
    verdict: str,
    note: str | None = None,
) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.review_ticket(db, principal, code, verdict, note).to_dict()

    return await _run_in_thread(_run)


# --------------------------------------------------------------------------- #
# Tools de sesiones
# --------------------------------------------------------------------------- #


@mcp.tool(description="Abre una sesión de planificación sobre un tema dentro de un proyecto.")
async def start_session(project: str, title: str, goal: str) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.start_session(db, principal, project, title, goal).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Entra a una sesión y devuelve su contexto: objetivo, participantes y los "
        "tickets que ya se escribieron. Leé esto ANTES de aportar, para no duplicar "
        "lo que puso otro agente. Es idempotente: podés llamarlo todas las veces."
    )
)
async def join_session(code: str, role: str = "contributor") -> dict[str, Any]:
    require_read()
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.join_session(db, principal, code, role).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(description="Cierra la sesión. Después, create_ticket con esa sesión da E_SESSION_CLOSED.")
async def close_session(code: str) -> dict[str, Any]:
    require_write()
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.close_session(db, principal, code).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(description="Lista las sesiones de planificación, opcionalmente por proyecto o estado.")
async def list_sessions(
    project: str | None = None, status: str | None = None, limit: int = 20
) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        items = sessions.list_sessions(db, project=project, status=status, limit=limit)
        return {"ok": True, "count": len(items), "items": items}

    return await _run_in_thread(_run)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #


@mcp.resource(
    "opensupport://skill/ticket-authoring",
    mime_type="text/markdown",
    description="El contrato de redacción de tickets. Leelo antes de crear el primero.",
)
def skill_ticket_authoring() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@mcp.resource("opensupport://schema/ticket", mime_type="application/json")
def schema_ticket() -> dict[str, Any]:
    return TicketDraft.model_json_schema()


@mcp.resource("opensupport://schema/violations", mime_type="application/json")
def schema_violations() -> dict[str, Any]:
    return _violation_table()


@mcp.resource("opensupport://board/snapshot", mime_type="text/markdown")
async def board_snapshot() -> str:
    require_read()

    def _run(db: OrmSession) -> str:
        rows = repo.list_tickets(db, limit=200)
        lines = ["# Board de OpenSupport", ""]
        by_status: dict[str, list[str]] = {}
        for t in rows:
            by_status.setdefault(t.status, []).append(
                f"- **{t.code}** [{t.type}/{t.priority}] {t.title}  — proyecto: {t.project.slug}"
            )
        if not rows:
            return "# Board de OpenSupport\n\nTodavía no hay tickets.\n"
        for status in sorted(by_status):
            lines.append(f"## {status} ({len(by_status[status])})")
            lines.append("")
            lines.extend(by_status[status])
            lines.append("")
        return "\n".join(lines)

    return await _run_in_thread(_run)


@mcp.resource("opensupport://meta", mime_type="application/json")
async def meta() -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        return {
            "ok": True,
            "version": "0.1.0",
            "stats": repo.stats(db),
            "projects": [p.slug for p in repo.all_projects(db)],
            "limits": {
                "max_steps": MAX_STEPS,
                "max_acceptance": MAX_ACCEPTANCE,
                "max_scope_include": MAX_SCOPE_ITEMS,
                "max_title": MAX_TITLE,
            },
            "statuses": [str(s) for s in TicketStatus],
            "skills": [
                "opensupport://skill/ticket-authoring",
                "opensupport://schema/ticket",
                "opensupport://schema/violations",
                "opensupport://board/snapshot",
                "opensupport://session/{code}",
                "opensupport://meta",
            ],
        }

    return await _run_in_thread(_run)


@mcp.resource(
    "opensupport://session/{code}",
    mime_type="text/markdown",
    description="Contexto de una sesión: objetivo, participantes y tickets ya escritos.",
)
async def session_snapshot(code: str) -> str:
    require_read()
    principal = require_read()

    def _run(db: OrmSession) -> str:
        return sessions.snapshot_markdown(db, principal, code)

    return await _run_in_thread(_run)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _violation_table() -> dict[str, dict[str, str]]:
    return {
        "E_TITLE_MISSING": {"field": "title", "fix": "Máximo 120 caracteres: verbo + objeto + dónde."},
        "E_SUMMARY_MISSING": {"field": "summary", "fix": "Describí QUÉ hace y POR QUÉ, en 2 a 5 oraciones (mínimo 80 caracteres)."},
        "E_OUTCOME_MISSING": {"field": "outcome", "fix": "Enunciá el cambio observable, no la tarea (mínimo 40 caracteres)."},
        "E_EVIDENCE_MISSING": {"field": "evidence", "fix": "Cite al menos un path:line que leíste antes de escribir esto."},
        "E_EVIDENCE_MALFORMED": {"field": "evidence[].path", "fix": "Path relativo al repo, sin ruta absoluta ni '..'."},
        "E_STEPS_MISSING": {"field": "steps", "fix": "Entre 3 y 12 pasos de implementación."},
        "E_STEPS_TOO_MANY": {"field": "steps", "fix": "Dividí el ticket: más de 12 pasos es más de un ticket."},
        "E_STEPS_VAGUE": {"field": "steps[]", "fix": "Cada paso arranca con un verbo: Crear, Mover, Agregar, Eliminar."},
        "E_ACCEPTANCE_MISSING": {"field": "acceptance", "fix": "Al menos un criterio de aceptación."},
        "E_ACCEPTANCE_VAGUE": {"field": "acceptance[]", "fix": "Cada criterio debe ser comprobable. Nada de 'funciona correctamente'."},
        "E_ACCEPTANCE_TOO_MANY": {"field": "acceptance", "fix": "Máximo 20 criterios; si son más, son varios tickets."},
        "E_SCOPE_EMPTY": {"field": "scope.include", "fix": "Enumerá qué entra."},
        "E_SCOPE_TOO_BIG": {"field": "scope.include", "fix": "Dividí el ticket: más de 15 items de scope es más de un ticket."},
        "W_NO_EXCLUDE": {"field": "scope.exclude", "fix": "No bloquea. Conviene decir qué queda afuera."},
        "W_NO_RISKS": {"field": "risks", "fix": "No bloquea. Conviene decir qué puede romperse."},
        "E_PROJECT_OUT_OF_SCOPE": {"field": "project", "fix": "Tu token no tiene ese proyecto en su alcance."},
        "E_UNKNOWN_PROJECT": {"field": "project", "fix": "El proyecto no existe. Usá list_projects o crealo."},
        "E_UNKNOWN_TICKET": {"field": "code", "fix": "El ticket no existe. Usá list_tickets."},
        "E_UNKNOWN_USER": {"field": "owner_user", "fix": "El usuario no existe."},
        "E_NOT_YOUR_TICKET": {"field": "code", "fix": "Solo el owner, el agente autor o un admin pueden modificarlo."},
        "E_CONFLICT": {"field": "expected_updated_at", "fix": "Otro agente lo cambió. Releé y reintentá."},
        "E_INVALID_TRANSITION": {"field": "status", "fix": "Esa transición no está permitida desde el estado actual."},
        "E_CLOSED_NEEDS_EVIDENCE": {"field": "status", "fix": "Usá close_ticket con pr/commit y commands."},
        "E_VERIFICATION_INCOMPLETE": {"field": "verification", "fix": "Falta pr o commit, o falta al menos un comando de verificación."},
        "E_REVIEW_NOTE_REQUIRED": {"field": "note", "fix": "Pedir cambios sin decir cuáles no es una opinión."},
        "E_DEPS_CYCLE": {"field": "to", "fix": "Ese vínculo cerraría un ciclo. Usá relates_to."},
        "E_SELF_LINK": {"field": "to", "fix": "Un ticket no puede vincularse consigo mismo."},
        "E_UNKNOWN_SESSION": {"field": "session", "fix": "La sesión no existe. Usá list_sessions."},
        "E_SESSION_CLOSED": {"field": "session", "fix": "La sesión está cerrada. Abrí una nueva."},
        "E_CONVENCION_PROYECTO": {"field": "scope", "fix": "El proyecto declara una convención que este ticket no cumple."},
    }


def _draft_from_dict(item: dict[str, Any], session: str | None = None) -> TicketDraft:
    return _draft_from_args(
        project=item.get("project", ""),
        title=item.get("title", ""),
        summary=item.get("summary", ""),
        outcome=item.get("outcome", ""),
        evidence=item.get("evidence"),
        steps=item.get("steps"),
        acceptance=item.get("acceptance"),
        scope=item.get("scope"),
        problem=item.get("problem"),
        type=item.get("type", "feature"),
        priority=item.get("priority", "p2"),
        risks=item.get("risks"),
        tags=item.get("tags"),
        external_key=item.get("external_key"),
        owner_user=item.get("owner_user"),
        session=item.get("session", session),
        estimate=item.get("estimate"),
        deps=item.get("deps"),
    )


def _draft_from_args(**kwargs: Any) -> TicketDraft:
    scope = kwargs.get("scope") or {}
    return TicketDraft(
        project=kwargs.get("project") or "",
        title=kwargs.get("title") or "",
        summary=kwargs.get("summary") or "",
        outcome=kwargs.get("outcome") or "",
        problem=kwargs.get("problem"),
        type=kwargs.get("type") or "feature",
        priority=kwargs.get("priority") or "p2",
        evidence=kwargs.get("evidence") or [],
        steps=kwargs.get("steps") or [],
        acceptance=kwargs.get("acceptance") or [],
        scope={"include": scope.get("include", []), "exclude": scope.get("exclude", [])},
        risks=kwargs.get("risks") or [],
        tags=kwargs.get("tags") or [],
        estimate=kwargs.get("estimate"),
        deps=kwargs.get("deps") or [],
        external_key=kwargs.get("external_key"),
        owner_user=kwargs.get("owner_user"),
        session=kwargs.get("session"),
    )
