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
from app.domain.enums import (
    EvidenceKind,
    IntakeMode,
    LinkKind,
    ResolutionKind,
    ReviewVerdict,
    TicketStatus,
)
from app.projects import service as projects
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
    Resolution,
    TicketDraft,
    TicketPatch,
    draft_from_dict,
)

#: Versión del contrato. Si cambia, los clientes que guardan borradores
#: necesitan saberlo antes de que algo falle por forma.
CONTRACT_VERSION = "2"

SKILL_PATH = Path(__file__).resolve().parents[2] / "skill" / "ticket-authoring.md"
WORKFLOW_PATH = Path(__file__).resolve().parents[2] / "skill" / "support-workflow.md"

#: URI del resource con el contrato de redacción. Va en las descripciones de las tools
#: de escritura porque el agente solo lee tools/list si no abre el resource.
SKILL_URI = "opensupport://skill/ticket-authoring"
SESSIONS_URI = "opensupport://sessions"
WORKFLOW_URI = "opensupport://workflow/support"

INSTRUCTIONS = """\
OpenSupport es un board de tickets para equipos donde los agentes de código hacen
el trabajo de analisis. Tu flujo normal es:

1. resources/read de opensupport://skill/ticket-authoring  (el contrato de redaccion)
2. search_tickets() para no duplicar lo que ya esta en el board
3. lint_ticket() con tu borrador, corregir, repetir hasta ok: true
4. create_ticket() o create_tickets_batch() cuando pasa

Hay dos entradas. intake_mode='implementation' es el contrato completo: leiste
el codigo y sabés que hay que cambiar; el ticket nace 'ready'. intake_mode=
'incident' admite un reporte sin acceso al repo (sintoma + evidencia
observable, sin diagnostico inventado); el ticket nace 'open' y hay que
completarlo antes de promotedlo a 'ready'.

Cuando implementes algo: set_status(code, "in_progress"), y al terminar
close_ticket(code, resolution={kind, summary, user_response, verifications}) —
'done' no se puede setear directo, hace falta la resolucion y su verificacion.

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


@mcp.tool(
    description=(
        "Devuelve un ticket por su código (OS-003), con revisiones, contexto de "
        "soporte y el workflow: qué transiciones podés hacer, qué falta y qué "
        "puede hacer tu token. Para recuperar la conversación usá "
        "get_ticket_activity."
    )
)
async def get_ticket(code: str, project: str | None = None) -> dict[str, Any]:
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        ticket = repo.get_ticket_by_code(db, code, project)
        if ticket is None:
            slugs = repo.projects_for_ticket_code(db, code)
            if project is None and len(slugs) > 1:
                return {
                    "ok": False,
                    "error": (
                        f"El ticket '{code}' existe en {len(slugs)} proyectos. "
                        f"Pasá project= con uno de: {', '.join(slugs)}."
                    ),
                    "violations": [
                        {
                            "code": "E_AMBIGUOUS_TICKET",
                            "field": "code",
                            "message": f"El ticket '{code}' existe en varios proyectos.",
                            "fix": f"Pasá project= con uno de: {', '.join(slugs)}.",
                        }
                    ],
                }
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
            "intake_mode": ticket.intake_mode,
            "support_context": ticket.support_context,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        }
        data["review"] = tickets.review_summary(db, ticket)
        data["workflow"] = tickets.workflow_for(ticket, principal)
        data["links"] = [
            {
                "kind": link.kind,
                "other": link.to_ticket_id
                if link.from_ticket_id == ticket.id
                else link.from_ticket_id,
            }
            for link in repo.get_links(db, ticket.id)
        ]
        return {"ok": True, "ticket": data}

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Actividad de un ticket: comentarios y eventos en una sola línea de "
        "tiempo, con autor y fecha. Es lo que usás para retomar un caso sin "
        "leer el chat anterior: otro agente dejó el contexto acá. "
        f"Recorrido completo en {WORKFLOW_URI}."
    )
)
async def get_ticket_activity(
    code: str, project: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        ticket = repo.get_ticket_by_code(db, code, project)
        if ticket is None:
            slugs = repo.projects_for_ticket_code(db, code)
            if project is None and len(slugs) > 1:
                return {
                    "ok": False,
                    "error": (
                        f"El ticket '{code}' existe en {len(slugs)} proyectos. "
                        f"Pasá project= con uno de: {', '.join(slugs)}."
                    ),
                }
            return {"ok": False, "error": f"El ticket '{code}' no existe."}
        return {"ok": True, **tickets.ticket_activity(db, ticket, limit, offset)}

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


@mcp.tool(
    description=(
        "Busca tickets por texto (FTS5) en título, resumen, outcome y criterios. "
        "Podés acotar por proyecto: los códigos se repiten entre proyectos."
    )
)
async def search_tickets(query: str, project: str | None = None, limit: int = 20) -> dict[str, Any]:
    require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        hits = repo.search_tickets(db, query, limit, project=project)
        return {
            "ok": True,
            "items": [
                {
                    "code": t.code,
                    "project": t.project.slug,
                    "title": t.title,
                    "status": t.status,
                    "fragment": frag,
                }
                for t, frag in hits
            ],
        }

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Lista los proyectos del board con nombre, descripción y tus permisos "
        "efectivos. Es el primer paso cuando no sabés en qué proyecto estás: "
        "el código de un ticket es único por proyecto, así que sin esto no se "
        "puede elegir. Filtro por texto con query=."
    )
)
async def list_projects(
    query: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        return {"ok": True, **projects.list_projects(db, principal, query, limit, offset)}

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Devuelve un proyecto por su slug: nombre, descripción, si está archivado "
        "y si tu token puede escribir en él."
    )
)
async def get_project(project: str) -> dict[str, Any]:
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        return projects.get_project(db, principal, project)

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "JSON Schema del contrato de ticket + la tabla de violaciones con su fix. "
        "Incluye las dos modalidades de entrada (incident e implementation) y el "
        "contrato de resolución."
    )
)
async def get_schema() -> dict[str, Any]:
    require_read()
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "json_schema": TicketDraft.model_json_schema(),
        "intake_modes": [str(m) for m in IntakeMode],
        "evidence_kinds": [str(k) for k in EvidenceKind],
        "resolution_kinds": [str(k) for k in ResolutionKind],
        "resolution_schema": Resolution.model_json_schema(),
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
        "examples": _examples(),
        "violations": _violation_table(),
    }


# --------------------------------------------------------------------------- #
# Tools de escritura
# --------------------------------------------------------------------------- #


@mcp.tool(
    description=(
        "Valida un borrador SIN guardarlo. Iterá hasta ok: true antes de crear. "
        "Devuelve las violaciones con su código y la instrucción de corrección.\n\n"
        "intake_mode decide el contrato: 'implementation' (default) exige el ciclo "
        "completo de implementación; 'incident' admite un reporte sin acceso al repo "
        "y pide support_context.symptom más una evidencia observable."
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
    intake_mode: str = "implementation",
    support_context: dict[str, Any] | None = None,
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
        intake_mode=intake_mode,
        support_context=support_context,
    )
    result = tickets.lint_draft(draft)
    return result.to_dict()


@mcp.tool(
    description=(
        "Crea un ticket validado. Idempotente por external_key: si ya existe, "
        "devuelve el existente en lugar de duplicarlo.\n\n"
        "intake_mode='implementation' (default) nace en 'ready': investigaste y "
        "sabés qué cambiar. intake_mode='incident' nace en 'open': reportás un "
        "problema sin acceso al repo, con support_context y evidencia observable. "
        "En ambos casos no hace falta un documento Markdown ni un commit.\n\n"
        "FLUJO: pasá el mismo borrador por lint_ticket hasta que devuelva ok:true, "
        "y recién después crealo acá. El contrato completo de redacción está en el "
        f"resource {SKILL_URI} (leelo una vez con resources/read) y el recorrido "
        f"hasta la resolución en {WORKFLOW_URI}."
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
    intake_mode: str = "implementation",
    support_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    principal = require_write()
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
        intake_mode=intake_mode,
        support_context=support_context,
    )

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.create_ticket(db, principal, draft).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Crea varios tickets de una. Es PARCIAL a propósito: un item inválido no "
        "tira los válidos: devolvé created/rejected por cada uno, en orden.\n\n"
        "FLUJO: linteá cada item antes con lint_ticket, o los rechazados vienen "
        f"sin contexto. Contrato en el resource {SKILL_URI}."
    )
)
async def create_tickets_batch(
    items: list[dict[str, Any]],
    session: str | None = None,
) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        results = tickets.create_tickets_batch(db, principal, list(items), session=session)
        return {"ok": True, "results": [r.to_dict() for r in results]}

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Actualiza un ticket. Mandá expected_updated_at con el valor de get_ticket: "
        "si otro agente lo cambió primero, recibís E_CONFLICT y no pisás su trabajo. "
        f"Revisá {SKILL_URI} si necesitás recordar qué campos pedir."
    )
)
async def update_ticket(
    code: str,
    patch: dict[str, Any],
    expected_updated_at: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    principal = require_write()
    model = TicketPatch(**patch)

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.update_ticket(
            db, principal, code, model, expected_updated_at, project
        ).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        f"Cambia quién responde el ticket. No cambia quién lo escribió. Contrato en {SKILL_URI}."
    )
)
async def assign_ticket(code: str, owner_user: str, project: str | None = None) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.assign_ticket(db, principal, code, owner_user, project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Vincula dos tickets: blocks (dependencia de bloqueo), split_from (nació de "
        "dividir otro) o relates_to (contexto). Acepta code o external_key como destino. "
        "Un ciclo en blocks devuelve E_DEPS_CYCLE. Tipos y ejemplos en "
        f"{SKILL_URI}."
    )
)
async def link_tickets(
    from_code: str, to: str, kind: str = "relates_to", project: str | None = None
) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.link_tickets(db, principal, from_code, to, LinkKind(kind), project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Cambia el estado de un ticket. Acepta ready, in_progress, blocked, open y wont. "
        "NO acepta 'done': para cerrar usá close_ticket con pr o commit y un comando de "
        f"verificación. Transiciones válidas en {SKILL_URI}."
    )
)
async def set_status(
    code: str, status: str, note: str | None = None, project: str | None = None
) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.set_status(db, principal, code, status, note, project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Agrega un comentario al ticket. Cualquiera del equipo puede comentar. "
        "Sirve para dejar lo observado o lo que falta: es parte del recorrido de "
        f"soporte. Contrato en {WORKFLOW_URI}."
    )
)
async def add_comment(code: str, body: str, project: str | None = None) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.add_comment(db, principal, code, body, project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Cierra el ticket con la solución y su evidencia. Es la única forma de "
        "llegar a 'done': set_status(code, 'done') siempre falla.\n\n"
        "resolution={kind, summary, user_response, verifications}: kind es "
        "'code_fix' (exige pr o commit y commands), 'configuration', 'workaround' o "
        "'guidance' (exigen una comprobación observable: qué se verificó y qué se "
        "obtuvo). user_response es lo que queda guardado para el usuario.\n\n"
        "El contrato viejo (pr, commit, commands, notes) sigue funcionando. "
        f"Recorrido en {WORKFLOW_URI}."
    )
)
async def close_ticket(
    code: str,
    resolution: dict[str, Any] | None = None,
    pr: str | None = None,
    commit: str | None = None,
    commands: list[str] | None = None,
    notes: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    principal = require_write()
    verification: dict[str, Any] = (
        dict(resolution)
        if resolution
        else {
            "pr": pr,
            "commit": commit,
            "commands": commands or [],
            "notes": notes,
        }
    )

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.close_ticket(db, principal, code, verification, project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Registra un veredicto de revisión (approved o changes_requested). "
        "NO cambia el status: la revisión informa, no bloquea. "
        "changes_requested exige una nota diciendo qué cambiar. "
        f"El recorrido completo está en {WORKFLOW_URI}."
    )
)
async def review_ticket(
    code: str,
    verdict: str,
    note: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return tickets.review_ticket(db, principal, code, verdict, note, project).to_dict()

    return await _run_in_thread(_run)


# --------------------------------------------------------------------------- #
# Tools de sesiones
# --------------------------------------------------------------------------- #


@mcp.tool(
    description=(
        "Abre una sesión de planificación sobre un tema dentro de un proyecto. "
        "Sirve para que varios agentes trabajen el mismo tema sin pisarse: después "
        "cada uno llama join_session para leer lo ya escrito antes de aportar. "
        f"Sesiones abiertas: {SESSIONS_URI}."
    )
)
async def start_session(project: str, title: str, goal: str) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.start_session(db, principal, project, title, goal).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Entra a una sesión y devuelve su contexto: objetivo, participantes y los "
        "tickets que ya se escribieron. Leé esto ANTES de aportar, para no duplicar "
        "lo que puso otro agente. Es idempotente: podés llamarlo todas las veces. "
        "El código de sesión es único POR PROYECTO: si el mismo código existe en "
        "varios proyectos, pasá project= para elegir, o recibís E_AMBIGUOUS_SESSION. "
        f"Índice de sesiones abiertas: {SESSIONS_URI}."
    )
)
async def join_session(
    code: str, role: str = "contributor", project: str | None = None
) -> dict[str, Any]:
    require_read()
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.join_session(db, principal, code, role, project).to_dict()

    return await _run_in_thread(_run)


@mcp.tool(
    description=(
        "Cierra la sesión. Después, create_ticket con esa sesión da E_SESSION_CLOSED. "
        "El código es único por proyecto: si el mismo código existe en varios, pasá "
        "project= para elegir, o recibís E_AMBIGUOUS_SESSION con la lista. "
        f"Sesiones abiertas: {SESSIONS_URI}."
    )
)
async def close_session(code: str, project: str | None = None) -> dict[str, Any]:
    principal = require_write()

    def _run(db: OrmSession) -> dict[str, Any]:
        return sessions.close_session(db, principal, code, project).to_dict()

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


@mcp.resource(
    "opensupport://workflow/support",
    mime_type="text/markdown",
    description=(
        "El ciclo de soporte completo: descubrir proyecto, consultar, reportar "
        "un incidente o una propuesta, investigar, promover, resolver y cerrar. "
        "Leelo si no sabés cómo seguir."
    ),
)
def support_workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


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


@mcp.resource(
    "opensupport://sessions",
    mime_type="text/markdown",
    description="Sesiones de planificación abiertas. Para entrar a una: join_session.",
)
async def sessions_index() -> str:
    """Existe para que las sesiones aparezcan en resources/list.

    La plantilla opensupport://session/{code} solo se ve en resources/templates/list,
    que muchos clientes nunca llaman. Un agente que no las descubre, no las usa.
    """
    require_read()

    def _run(db: OrmSession) -> str:  # noqa: ANN001
        rows = sessions.list_sessions(db, limit=50)
        if not rows:
            return (
                "# Sesiones de planificación\n\n"
                "No hay sesiones abiertas. Abrí una con `start_session(project, title, goal)`.\n"
            )
        lines = ["# Sesiones abiertas", ""]
        for row in rows:
            count = len(
                [
                    t
                    for t in repo.list_tickets(db, project=row["project"], limit=200)
                    if t.session_id is not None
                ]
            )
            lines.append(f"- **{row['code']}** ({row['project']}) — {row['title']}")
            lines.append(f"  - objetivo: {row['goal']}")
            lines.append(f"  - tickets: {count}")
        lines.append("")
        lines.append("Entrá con `join_session(code)` antes de aportar tickets.")
        return "\n".join(lines)

    return await _run_in_thread(_run)


@mcp.resource("opensupport://meta", mime_type="application/json")
async def meta() -> dict[str, Any]:
    principal = require_read()

    def _run(db: OrmSession) -> dict[str, Any]:
        project_rows = repo.all_projects(db)
        return {
            "ok": True,
            "version": "0.1.0",
            "contract_version": CONTRACT_VERSION,
            "stats": repo.stats(db),
            "projects": [
                {
                    "slug": p.slug,
                    "name": p.name,
                    "description": p.description,
                    "archived": p.archived_at is not None,
                    "can_write": principal.can_write_project(p.slug),
                }
                for p in project_rows
            ],
            "token": {
                "label": principal.label,
                "scopes": list(principal.scopes),
                "project_scope": list(principal.project_scope),
                "scope_note": "project_scope vacío = puede escribir en todos los proyectos.",
            },
            "limits": {
                "max_steps": MAX_STEPS,
                "max_acceptance": MAX_ACCEPTANCE,
                "max_scope_include": MAX_SCOPE_ITEMS,
                "max_title": MAX_TITLE,
            },
            "statuses": [str(s) for s in TicketStatus],
            "skills": [
                "opensupport://skill/ticket-authoring",
                "opensupport://workflow/support",
                "opensupport://schema/ticket",
                "opensupport://schema/violations",
                "opensupport://board/snapshot",
                "opensupport://session/{code}",
                "opensupport://sessions",
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


def _examples() -> dict[str, dict[str, Any]]:
    """Ejemplos mínimos que pasan los validadores reales.

    No son documentación: son los mismos objetos que el linter acepta.
    """
    return {
        "implementation": {
            "project": "mi-tesis",
            "intake_mode": "implementation",
            "title": "Extraer la verificación de JWT a un módulo testeable",
            "summary": (
                "La verificación de JWT vive inline dentro del endpoint /login y no se puede "
                "testear sin levantar toda la app. Moverla a auth/verify.py permite probarla "
                "como función pura y agrega tests unitarios que hoy no existen."
            ),
            "outcome": (
                "auth.verify_token queda como función pura con tests propios y /login la "
                "llama sin cambiar el comportamiento observable."
            ),
            "evidence": [
                {
                    "kind": "code",
                    "path": "backend/app/api/login.py",
                    "lines": "42-88",
                    "note": "verificación inline",
                }
            ],
            "steps": [
                "Crear backend/app/auth/verify.py con verify_token(token, secret).",
                "Mover la lógica actual sin cambiar la semántica observada.",
                "Agregar tests con los casos válido, expirado y firma inválida.",
            ],
            "acceptance": [
                "GET /login con un token expirado devuelve el mismo status que antes.",
            ],
            "scope": {
                "include": ["extracción del módulo"],
                "exclude": ["cambio del formato del token"],
            },
        },
        "incident": {
            "project": "mi-tesis",
            "intake_mode": "incident",
            "type": "bug",
            "title": "El login expira antes de tiempo en la app móvil",
            "summary": (
                "Desde la versión 1.4 la sesión de la app móvil se cierra a los cinco minutos "
                "aunque el usuario siga usando la app. En la web no pasa y con la 1.3 tampoco. "
                "No tuve acceso al código para revisarlo: lo reporto con lo que se ve."
            ),
            "outcome": "",
            "support_context": {
                "symptom": "La sesión móvil se cierra a los 5 minutos aunque la app esté en uso.",
                "observed": "Al volver al foreground la app redirige al login.",
                "impact": "Los usuarios tienen que loguearse de nuevo varias veces por sesión.",
                "environment": "App móvil 1.4.0, iOS 18, backend de staging.",
                "reproduction": [
                    "Entrar con la app 1.4.0.",
                    "Volver al foreground a los 5 minutos.",
                ],
                "unknown": ["No sé si es el TTL del token o un refresh que no llega."],
            },
            "evidence": [
                {"kind": "observation", "note": "La app vuelve al login a los 5 minutos exactos."},
                {"kind": "log", "note": "En el log del cliente: 'session expired'."},
            ],
        },
    }


def _violation_table() -> dict[str, dict[str, str]]:
    return {
        "E_TITLE_MISSING": {
            "field": "title",
            "fix": "Máximo 120 caracteres: verbo + objeto + dónde.",
        },
        "E_SUMMARY_MISSING": {
            "field": "summary",
            "fix": "Describí QUÉ hace y POR QUÉ, en 2 a 5 oraciones (mínimo 80 caracteres).",
        },
        "E_OUTCOME_MISSING": {
            "field": "outcome",
            "fix": "Enunciá el cambio observable, no la tarea (mínimo 40 caracteres).",
        },
        "E_EVIDENCE_MISSING": {
            "field": "evidence",
            "fix": "Cite al menos un path:line que leíste, o una observación concreta de lo que pasa.",
        },
        "E_EVIDENCE_MALFORMED": {
            "field": "evidence[]",
            "fix": "Cada evidencia según su kind: code→path, command→command, reference→url, observation/log→nota.",
        },
        "E_SUPPORT_CONTEXT_MISSING": {
            "field": "support_context.symptom",
            "fix": "Contá el síntoma en una frase. No hace falta la causa.",
        },
        "W_NO_OUTCOME": {
            "field": "outcome",
            "fix": "No bloquea. Si sabés el comportamiento esperado, agregalo.",
        },
        "E_STEPS_MISSING": {"field": "steps", "fix": "Entre 3 y 12 pasos de implementación."},
        "E_STEPS_TOO_MANY": {
            "field": "steps",
            "fix": "Dividí el ticket: más de 12 pasos es más de un ticket.",
        },
        "E_STEPS_VAGUE": {
            "field": "steps[]",
            "fix": "Cada paso arranca con un verbo: Crear, Mover, Agregar, Eliminar.",
        },
        "E_ACCEPTANCE_MISSING": {
            "field": "acceptance",
            "fix": "Al menos un criterio de aceptación.",
        },
        "E_ACCEPTANCE_VAGUE": {
            "field": "acceptance[]",
            "fix": "Cada criterio debe ser comprobable. Nada de 'funciona correctamente'.",
        },
        "E_ACCEPTANCE_TOO_MANY": {
            "field": "acceptance",
            "fix": "Máximo 20 criterios; si son más, son varios tickets.",
        },
        "E_SCOPE_EMPTY": {"field": "scope.include", "fix": "Enumerá qué entra."},
        "E_SCOPE_TOO_BIG": {
            "field": "scope.include",
            "fix": "Dividí el ticket: más de 15 items de scope es más de un ticket.",
        },
        "W_NO_EXCLUDE": {
            "field": "scope.exclude",
            "fix": "No bloquea. Conviene decir qué queda afuera.",
        },
        "W_NO_RISKS": {"field": "risks", "fix": "No bloquea. Conviene decir qué puede romperse."},
        "E_PROJECT_OUT_OF_SCOPE": {
            "field": "project",
            "fix": "Tu token no tiene ese proyecto en su alcance.",
        },
        "E_UNKNOWN_PROJECT": {
            "field": "project",
            "fix": "El proyecto no existe. Usá list_projects o crealo.",
        },
        "E_UNKNOWN_TICKET": {"field": "code", "fix": "El ticket no existe. Usá list_tickets."},
        "E_UNKNOWN_USER": {"field": "owner_user", "fix": "El usuario no existe."},
        "E_NOT_YOUR_TICKET": {
            "field": "code",
            "fix": "Solo el owner, el agente autor o un admin pueden modificarlo.",
        },
        "E_CONFLICT": {
            "field": "expected_updated_at",
            "fix": "Otro agente lo cambió. Releé y reintentá.",
        },
        "E_INVALID_TRANSITION": {
            "field": "status",
            "fix": "Esa transición no está permitida desde el estado actual.",
        },
        "E_PROMOTION_NOT_READY": {
            "field": "status",
            "fix": "Para pasar a 'ready' faltan outcome, scope.include y steps.",
        },
        "E_WONT_REASON_REQUIRED": {
            "field": "note",
            "fix": "Descartar un ticket exige decir por qué.",
        },
        "E_CLOSED_NEEDS_EVIDENCE": {
            "field": "status",
            "fix": "Usá close_ticket con la resolución y su verificación.",
        },
        "E_VERIFICATION_INCOMPLETE": {
            "field": "verification",
            "fix": "Falta pr o commit con comandos, o una verificación observable con resultado.",
        },
        "E_RESOLUTION_INCOMPLETE": {
            "field": "resolution",
            "fix": "Falta la respuesta para el usuario o la forma de la resolución.",
        },
        "E_INVALID_DRAFT": {"field": "items[]", "fix": "El item no tiene la forma de un borrador."},
        "E_TICKET_OTHER_PROJECT": {
            "field": "to",
            "fix": "Ese ticket existe en otro proyecto. Usá el del proyecto actual.",
        },
        "E_REVIEW_NOTE_REQUIRED": {
            "field": "note",
            "fix": "Pedir cambios sin decir cuáles no es una opinión.",
        },
        "E_DEPS_CYCLE": {"field": "to", "fix": "Ese vínculo cerraría un ciclo. Usá relates_to."},
        "E_SELF_LINK": {"field": "to", "fix": "Un ticket no puede vincularse consigo mismo."},
        "E_UNKNOWN_SESSION": {"field": "session", "fix": "La sesión no existe. Usá list_sessions."},
        "E_SESSION_CLOSED": {"field": "session", "fix": "La sesión está cerrada. Abrí una nueva."},
        "E_CONVENCION_PROYECTO": {
            "field": "scope",
            "fix": "El proyecto declara una convención que este ticket no cumple.",
        },
    }


def _draft_from_dict(item: dict[str, Any], session: str | None = None) -> TicketDraft:
    return draft_from_dict(item, session=session)


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
        intake_mode=kwargs.get("intake_mode") or "implementation",
        support_context=kwargs.get("support_context"),
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
