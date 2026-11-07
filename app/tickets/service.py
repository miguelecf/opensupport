"""Servicio de tickets: orquesta linter, idempotencia, estados y eventos.

Ni MCP ni web conocen esta capa: es la única fuente de verdad de qué es un
ticket válido y qué se puede hacer con él.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session as OrmSession

from app.auth.tokens import Principal
from app.db.base import ensure_write_transaction, utcnow
from app.domain.enums import (
    AgentClient,
    EventKind,
    IntakeMode,
    LinkKind,
    ResolutionKind,
    ReviewVerdict,
    SessionStatus,
    TicketStatus,
)
from app.domain.models import (
    Agent,
    Comment,
    Project,
    Session,
    Ticket,
    TicketEvent,
    TicketLink,
    TicketReview,
    User,
)
from app.domain.transitions import allowed_targets, can_transition, requires_close_evidence
from app.tickets import repository as repo
from app.tickets.linter import errors, lint, warnings
from app.tickets.schema import (
    MIN_OUTCOME,
    Resolution,
    TicketDraft,
    TicketPatch,
    Violation,
    compute_content_hash,
    draft_from_dict,
)

NOT_YOUR_TICKET = (
    "E_NOT_YOUR_TICKET",
    "No sos el owner ni el autor de este ticket.",
    "Pedile al owner que te lo reasigne con assign_ticket, o esperá a que lo haga.",
)
OUT_OF_SCOPE = "E_PROJECT_OUT_OF_SCOPE"


@dataclass(slots=True)
class Result:
    """Respuesta uniforme: nunca una excepción para errores de validación."""

    ok: bool
    violations: list[Violation] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [v.model_dump() for v in self.violations],
            **self.data,
        }


def _v(code: str, field_name: str, message: str, fix: str = "") -> Violation:
    return Violation(code=code, field=field_name, message=message, fix=fix)


def _deny() -> Result:
    return Result(ok=False, violations=[_v(*NOT_YOUR_TICKET)])


# --------------------------------------------------------------------------- #
# Creación
# --------------------------------------------------------------------------- #


def lint_draft(draft: TicketDraft) -> Result:
    """Solo valida. No persiste. Es la tool `lint_ticket`."""
    found = lint(draft)
    return Result(
        ok=not errors(found),
        violations=found,
        data={"warnings": [w.model_dump() for w in warnings(found)]},
    )


def _resolve_project(db: OrmSession, principal: Principal, slug: str) -> Project | Result:
    if not principal.can_write_project(slug):
        allowed = ", ".join(principal.project_scope) or "(ninguno)"
        return Result(
            ok=False,
            violations=[
                _v(
                    OUT_OF_SCOPE,
                    "project",
                    f"El agente '{principal.label}' no tiene alcance sobre '{slug}'.",
                    f"Proyectos permitidos: {allowed}. Pedí que lo agreguen desde /settings/tokens.",
                )
            ],
        )
    project = repo.get_project_by_slug(db, slug)
    if project is None:
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_UNKNOWN_PROJECT",
                    "project",
                    f"El proyecto '{slug}' no existe.",
                    f"Proyectos existentes: {', '.join(p.slug for p in repo.all_projects(db)) or '(ninguno)'}.",
                )
            ],
        )
    return project


def _resolve_owner(db: OrmSession, principal: Principal, owner_name: str | None) -> User | None:
    if owner_name:
        return repo.get_user_by_name(db, owner_name)
    return db.get(User, principal.user_id)


def create_ticket(db: OrmSession, principal: Principal, draft: TicketDraft) -> Result:
    found = lint(draft)
    if errors(found):
        # Telemetría: queda el motivo del rechazo aunque no haya fila de ticket.
        _record_lint_failure(db, principal, found)
        return Result(ok=False, violations=found)

    resolved = _resolve_project(db, principal, draft.project)
    if isinstance(resolved, Result):
        return resolved
    project = resolved

    if draft.external_key:
        existing = repo.get_ticket_by_external_key(db, project.id, draft.external_key)
        if existing is not None:
            return Result(ok=True, data={"code": existing.code, "status": "existing"})

    owner = _resolve_owner(db, principal, draft.owner_user)
    if owner is None:
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_UNKNOWN_USER",
                    "owner_user",
                    f"El usuario '{draft.owner_user}' no existe.",
                    "Usá un nombre de usuario existente, o omitilo para que quede como owner el dueño del agente.",
                )
            ],
        )

    session_row = None
    if draft.session:
        # La sesión se resuelve dentro del proyecto del ticket: el código es
        # único por proyecto, así que sin esto un PLN-001 puede colgar el ticket
        # de la sesión del proyecto equivocado.
        session_row = repo.get_session_by_code(db, draft.session, draft.project)
        if session_row is None:
            slugs = repo.session_projects_for_code(db, draft.session)
            if draft.project not in slugs and len(slugs) > 1:
                return Result(
                    ok=False,
                    violations=[
                        _v(
                            "E_AMBIGUOUS_SESSION",
                            "session",
                            f"La sesión '{draft.session}' existe en {len(slugs)} proyectos.",
                            f"El ticket es de '{draft.project}' pero la sesión también existe en: "
                            f"{', '.join(slugs)}. Usá el código de la sesión de tu proyecto.",
                        )
                    ],
                )
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_UNKNOWN_SESSION",
                        "session",
                        f"La sesión '{draft.session}' no existe.",
                        "Usá list_sessions para ver las disponibles, o creala con start_session.",
                    )
                ],
            )
        if session_row.status == SessionStatus.CLOSED:
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_SESSION_CLOSED",
                        "session",
                        f"La sesión '{draft.session}' está cerrada.",
                        "Abrí una sesión nueva con start_session para seguir aportar tickets.",
                    )
                ],
            )

    code = repo.next_ticket_code(db, project)
    payload = draft.model_dump(mode="json")
    hash_parts = {
        k: payload.get(k)
        for k in (
            "title",
            "summary",
            "outcome",
            "problem",
            "evidence",
            "steps",
            "acceptance",
            "scope",
            "risks",
            "tags",
            "intake_mode",
            "support_context",
        )
    }

    # Un incidente nace 'open': nadie investigó todavía. Una propuesta
    # investigada nace 'ready'. Es la diferencia entre algo que pasa y algo
    # que ya se sabe qué hay que cambiar.
    initial_status = (
        TicketStatus.OPEN if draft.intake_mode is IntakeMode.INCIDENT else TicketStatus.READY
    )

    ticket = Ticket(
        code=code,
        project_id=project.id,
        session_id=session_row.id if session_row else None,
        owner_user_id=owner.id,
        author_agent_id=principal.agent_id,
        author_user_id=None if principal.agent_id else principal.user_id,
        external_key=draft.external_key,
        type=draft.type,
        priority=draft.priority,
        status=initial_status,
        intake_mode=draft.intake_mode,
        support_context=payload.get("support_context"),
        title=draft.title.strip(),
        summary=draft.summary.strip(),
        problem=(draft.problem or "").strip() or None,
        outcome=draft.outcome.strip(),
        evidence=payload["evidence"],
        steps=payload["steps"],
        acceptance=payload["acceptance"],
        scope=payload["scope"],
        risks=payload["risks"],
        tags=payload["tags"],
        estimate=draft.estimate,
        content_hash=compute_content_hash(hash_parts),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(ticket)
    db.flush()

    _add_event(db, ticket, EventKind.CREATED, principal, {"code": code})
    for dep in draft.deps:
        link_result = link_tickets(db, principal, code, dep, LinkKind.BLOCKS, project.slug)
        if not link_result.ok:
            # No se hace db.rollback(): eso mataría los tickets válidos del lote.
            # Se borra solo este ticket, con lo suyo, y se sigue.
            _discard_ticket(db, ticket)
            return link_result

    return Result(
        ok=True,
        violations=warnings(found),
        data={
            "code": code,
            "status": "created",
            "ticket_status": initial_status,
            "warnings": [w.model_dump() for w in warnings(found)],
        },
    )


def _discard_ticket(db: OrmSession, ticket: Ticket) -> None:
    """Borra un ticket recién creado sin tocar el resto de la transacción.

    Es lo que permite que un lote sea parcial: un item que falla por una
    dependencia inválida no puede dejar un ticket a medias ni confirmar lo que
    ya se había creado.
    """
    db.execute(delete(TicketEvent).where(TicketEvent.ticket_id == ticket.id))
    db.delete(ticket)
    db.flush()


def create_tickets_batch(
    db: OrmSession,
    principal: Principal,
    items: list[TicketDraft | dict[str, Any]],
    session: str | None = None,
) -> list[Result]:
    """Parcial por diseño: un item malo no tira los válidos.

    Un item mal formado (ni siquiera es un borrador) se rechaza con su
    mensaje, y el resto del lote sigue adelante.
    """
    results: list[Result] = []
    # Un savepoint necesita una transacción física abierta: si no, SQLite lo
    # toma como la transacción más externa y su RELEASE la confirma.
    ensure_write_transaction(db)
    for index, raw in enumerate(items):
        if isinstance(raw, TicketDraft):
            item = raw
        else:
            try:
                item = draft_from_dict(raw, session=session)
            except Exception as exc:  # noqa: BLE001 - se reporta al agente
                results.append(
                    Result(
                        ok=False,
                        violations=[
                            _v(
                                "E_INVALID_DRAFT",
                                f"items[{index}]",
                                "El item no tiene la forma de un borrador.",
                                str(exc)[:200],
                            )
                        ],
                        data={"index": index, "status": "rejected"},
                    )
                )
                continue
        effective = item
        if session and not item.session:
            effective = item.model_copy(update={"session": session})
        savepoint = db.begin_nested()
        try:
            outcome = create_ticket(db, principal, effective)
        except Exception as exc:  # noqa: BLE001 - se reporta al agente, no se propaga
            savepoint.rollback()
            results.append(
                Result(
                    ok=False,
                    violations=[
                        _v(
                            "E_INTERNAL",
                            f"items[{index}]",
                            "No se pudo guardar el ticket.",
                            str(exc)[:200],
                        )
                    ],
                    data={"index": index, "status": "rejected"},
                )
            )
            continue
        if outcome.ok:
            savepoint.commit()
        else:
            # Un item rechazado no deja ticket: se deshace lo que se hizo.
            savepoint.rollback()
        outcome.data["index"] = index
        # Contrato uniforme: cada item trae status, creada o rechazada.
        outcome.data.setdefault("status", "created" if outcome.ok else "rejected")
        results.append(outcome)
    return results


def _record_lint_failure(db: OrmSession, principal: Principal, found: list[Violation]) -> None:
    """Un draft rechazado deja rastro para recalibrar umbrales, pero no fila de ticket."""
    for violation in errors(found):
        db.add(
            TicketEvent(
                ticket_id=None,
                kind=EventKind.LINT_FAILED,
                actor_user_id=principal.user_id,
                actor_agent_id=principal.agent_id,
                payload={"code": violation.code, "field": violation.field},
                created_at=utcnow(),
            )
        )
    db.flush()


# --------------------------------------------------------------------------- #
# Mutaciones
# --------------------------------------------------------------------------- #


def _can_mutate(ticket: Ticket, principal: Principal) -> bool:
    if principal.is_admin or ticket.owner_user_id == principal.user_id:
        return True
    return principal.agent_id is not None and ticket.author_agent_id == principal.agent_id


def _scope_denied(ticket: Ticket, principal: Principal) -> Result | None:
    """El alcance del token se chequea en el servicio, no en el adaptador.

    Si un token solo puede escribir en 'mi-tesis', no puede comentar ni
    revisar un ticket de 'otro-proyecto' aunque sea el owner.
    """
    slug = ticket.project.slug
    if principal.can_write_project(slug):
        return None
    allowed = ", ".join(principal.project_scope) or "(ninguno)"
    return Result(
        ok=False,
        violations=[
            _v(
                OUT_OF_SCOPE,
                "project",
                f"El agente '{principal.label}' no tiene alcance sobre '{slug}'.",
                f"Proyectos permitidos: {allowed}.",
            )
        ],
    )


def _resolve_ticket(
    db: OrmSession, code: str, project: str | None
) -> tuple[Ticket | None, Result | None]:
    """Devuelve (ticket, error). El código es único por proyecto, no global.

    Sin `project` y con el mismo código en varios proyectos devolvemos None:
    elegir el primero que aparezca cambiaría el ticket equivocado sin avisar.
    """
    ticket = repo.get_ticket_by_code(db, code, project)
    if ticket is not None:
        return ticket, None
    slugs = repo.projects_for_ticket_code(db, code)
    if project is None and len(slugs) > 1:
        return None, Result(
            ok=False,
            violations=[
                _v(
                    "E_AMBIGUOUS_TICKET",
                    "code",
                    f"El ticket '{code}' existe en {len(slugs)} proyectos y no se puede elegir.",
                    f"Pasá project= con uno de: {', '.join(slugs)}.",
                )
            ],
        )
    return None, Result(
        ok=False,
        violations=[
            _v(
                "E_UNKNOWN_TICKET",
                "code",
                f"El ticket '{code}' no existe.",
                "Usá list_tickets para ver los existentes.",
            )
        ],
    )


def _load_mutable(
    db: OrmSession, principal: Principal, code: str, project: str | None = None
) -> Ticket | Result:
    ticket, error = _resolve_ticket(db, code, project)
    if ticket is None:
        assert error is not None
        return error
    denied = _scope_denied(ticket, principal)
    if denied is not None:
        return denied
    if not _can_mutate(ticket, principal):
        return _deny()
    return ticket


def _draft_from_ticket(ticket: Ticket, changes: dict[str, Any]) -> TicketDraft:
    """Arma el borrador resultante de aplicar `changes` sobre el ticket.

    Es lo que permite validar una actualización sin mutar: se valida el
    resultado combinado, no el parche suelto.
    """
    data: dict[str, Any] = {
        "project": ticket.project.slug,
        "title": ticket.title,
        "summary": ticket.summary,
        "problem": ticket.problem,
        "outcome": ticket.outcome,
        "type": ticket.type,
        "priority": ticket.priority,
        "intake_mode": ticket.intake_mode,
        "support_context": ticket.support_context,
        "evidence": ticket.evidence or [],
        "steps": ticket.steps or [],
        "acceptance": ticket.acceptance or [],
        "scope": ticket.scope or {},
        "risks": ticket.risks or [],
        "tags": ticket.tags or [],
        "estimate": ticket.estimate,
    }
    for key, value in changes.items():
        if key in data:
            data[key] = value
    return TicketDraft(**data)


def update_ticket(
    db: OrmSession,
    principal: Principal,
    code: str,
    patch: TicketPatch,
    expected_updated_at: str | None,
    project: str | None = None,
) -> Result:
    ticket = _load_mutable(db, principal, code, project)
    if isinstance(ticket, Result):
        return ticket

    if expected_updated_at is not None:
        actual = ticket.updated_at.isoformat() if ticket.updated_at else ""
        if actual != expected_updated_at:
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_CONFLICT",
                        "expected_updated_at",
                        "El ticket cambió desde que lo leíste.",
                        f"Releé el ticket y reintentá. updated_at actual: {actual}",
                    )
                ],
                data={"current_updated_at": actual},
            )

    data = patch.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    changed = {k: v for k, v in data.items() if k != "deps"}
    if changed:
        # Se valida el resultado combinado: una actualización que rompe el
        # contrato no se guarda, y no deja evento ni cambia el hash.
        merged = _draft_from_ticket(ticket, changed)
        found = lint(merged)
        if errors(found):
            return Result(ok=False, violations=found)
    for key, value in changed.items():
        setattr(ticket, key, value)
    ticket.updated_at = utcnow()

    hash_parts = {
        "title": ticket.title,
        "summary": ticket.summary,
        "outcome": ticket.outcome,
        "problem": ticket.problem,
        "evidence": ticket.evidence,
        "steps": ticket.steps,
        "acceptance": ticket.acceptance,
        "scope": ticket.scope,
        "risks": ticket.risks,
        "tags": ticket.tags,
        "intake_mode": ticket.intake_mode,
        "support_context": ticket.support_context,
    }
    ticket.content_hash = compute_content_hash(hash_parts)
    _add_event(db, ticket, EventKind.UPDATED, principal, {"fields": list(changed)})
    return Result(ok=True, data={"code": ticket.code, "updated_at": ticket.updated_at.isoformat()})


def missing_for_ready(ticket: Ticket) -> list[dict[str, str]]:
    """Qué le falta a un ticket para poder empezar a trabajarlo.

    Un incidente nace 'open' porque nadie investigó todavía. Para promotedlo
    a 'ready' hace falta un objetivo comprobable, un alcance y un plan: sin
    eso, 'ready' solo miente.
    """
    missing: list[dict[str, str]] = []
    if len((ticket.outcome or "").strip()) < MIN_OUTCOME:
        missing.append(
            {
                "field": "outcome",
                "fix": "Enunciá el resultado observable: qué tiene que pasar cuando esté resuelto.",
            }
        )
    scope = ticket.scope or {}
    if not scope.get("include"):
        missing.append(
            {
                "field": "scope.include",
                "fix": "Enumerá qué entra: archivos, comportamientos o superficie afectada.",
            }
        )
    if not (ticket.steps or []):
        missing.append(
            {
                "field": "steps",
                "fix": "Escribí los pasos del diagnóstico o de la intervención.",
            }
        )
    return missing


def set_status(
    db: OrmSession,
    principal: Principal,
    code: str,
    status: str,
    note: str | None = None,
    project: str | None = None,
) -> Result:
    ticket = _load_mutable(db, principal, code, project)
    if isinstance(ticket, Result):
        return ticket

    target = TicketStatus(status)
    if requires_close_evidence(target):
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_CLOSED_NEEDS_EVIDENCE",
                    "status",
                    "No se puede setear 'done' directamente.",
                    "Usá close_ticket con la resolución y su verificación para cerrar.",
                )
            ],
        )
    if not can_transition(TicketStatus(ticket.status), target):
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_INVALID_TRANSITION",
                    "status",
                    f"No se puede pasar de '{ticket.status}' a '{target}'.",
                    f"Transiciones válidas desde '{ticket.status}': {', '.join(allowed_targets(TicketStatus(ticket.status)))}",
                )
            ],
        )
    if target is TicketStatus.WONT and not (note or "").strip():
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_WONT_REASON_REQUIRED",
                    "note",
                    "Descartar un ticket sin decir por qué no se pierde el motivo.",
                    "Escribí en 'note' por qué no se va a trabajar: no aplica, duplicado, sin impacto.",
                )
            ],
        )
    if target is TicketStatus.READY:
        missing = missing_for_ready(ticket)
        if missing:
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_PROMOTION_NOT_READY",
                        "status",
                        "El ticket todavía no está listo para trabajarse.",
                        "Completá: " + "; ".join(f"{m['field']} ({m['fix']})" for m in missing),
                    )
                ],
                data={"missing": missing},
            )

    previous = ticket.status
    ticket.status = target
    ticket.updated_at = utcnow()
    payload: dict[str, Any] = {"from": previous, "to": target, "note": note}
    if previous == TicketStatus.DONE and ticket.verification:
        # La resolución anterior queda como historia, no como solución vigente.
        payload["previous_resolution"] = ticket.verification
        ticket.verification = None
    _add_event(
        db,
        ticket,
        EventKind.STATUS_CHANGED,
        principal,
        payload,
    )
    return Result(ok=True, data={"code": ticket.code, "status": target})


def _parse_resolution(verification: dict[str, Any]) -> tuple[Resolution | None, Result | None]:
    """Normaliza el cierre al contrato nuevo.

    El contrato viejo (pr/commit/commands/notes) se acepta tal cual: un
    cliente que ya cierra así no se rompe. El nuevo trae `kind`, resumen,
    respuesta para el usuario y verificaciones observables.
    """
    if "kind" not in verification:
        legacy = Resolution(
            kind=ResolutionKind.CODE_FIX,
            summary=str(verification.get("notes") or ""),
            user_response=str(verification.get("notes") or ""),
            pr=verification.get("pr"),
            commit=verification.get("commit"),
            commands=list(verification.get("commands") or []),
        )
        return legacy, None
    try:
        return Resolution(**verification), None
    except Exception as exc:  # noqa: BLE001 - se reporta como violación
        return None, Result(
            ok=False,
            violations=[
                _v(
                    "E_RESOLUTION_INCOMPLETE",
                    "resolution",
                    "La resolución no tiene la forma esperada.",
                    str(exc)[:200],
                )
            ],
        )


def close_ticket(
    db: OrmSession,
    principal: Principal,
    code: str,
    verification: dict[str, Any],
    project: str | None = None,
) -> Result:
    ticket = _load_mutable(db, principal, code, project)
    if isinstance(ticket, Result):
        return ticket

    resolution, parse_error = _parse_resolution(verification)
    if parse_error is not None:
        return parse_error
    assert resolution is not None
    legacy = "kind" not in verification

    if resolution.kind is ResolutionKind.CODE_FIX:
        has_ref = bool(resolution.pr or resolution.commit)
        if not has_ref or not resolution.commands:
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_VERIFICATION_INCOMPLETE",
                        "verification",
                        "Cerrar un ticket exige evidencia de que se implementó.",
                        "Mandá verification={pr o commit, commands: ['uv run pytest ...']}, "
                        "o usá kind='configuration'/'workaround'/'guidance' con una "
                        "comprobación observable.",
                    )
                ],
            )
    else:
        if not resolution.verifications or not all(
            v.result.strip() for v in resolution.verifications
        ):
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_VERIFICATION_INCOMPLETE",
                        "resolution.verifications",
                        "Una solución sin cambios de código se cierra con una comprobación "
                        "observable: qué se verificó y qué se obtuvo.",
                        "Mandá resolution={kind, summary, user_response, "
                        "verifications: [{result: '...', command: '...', ran_at: '...'}]}.",
                    )
                ],
            )
    if not legacy and not resolution.user_response.strip():
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_RESOLUTION_INCOMPLETE",
                    "resolution.user_response",
                    "Falta la respuesta para el usuario.",
                    "Escribí qué pasó y qué tiene que hacer: es lo que queda guardado del ticket.",
                )
            ],
        )
    if not can_transition(TicketStatus(ticket.status), TicketStatus.DONE):
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_INVALID_TRANSITION",
                    "status",
                    f"No se puede pasar de '{ticket.status}' a 'done'.",
                    f"Transiciones válidas: {', '.join(allowed_targets(TicketStatus(ticket.status)))}",
                )
            ],
        )

    previous = ticket.status
    ticket.status = TicketStatus.DONE
    ticket.verification = resolution.model_dump(mode="json")
    ticket.updated_at = utcnow()
    _add_event(
        db,
        ticket,
        EventKind.CLOSED,
        principal,
        {"from": previous, "resolution": ticket.verification},
    )
    return Result(
        ok=True,
        data={
            "code": ticket.code,
            "status": TicketStatus.DONE,
            "resolution": ticket.verification,
        },
    )


def assign_ticket(
    db: OrmSession, principal: Principal, code: str, owner_user: str, project: str | None = None
) -> Result:
    ticket = _load_mutable(db, principal, code, project)
    if isinstance(ticket, Result):
        return ticket
    owner = repo.get_user_by_name(db, owner_user)
    if owner is None:
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_UNKNOWN_USER",
                    "owner_user",
                    f"El usuario '{owner_user}' no existe.",
                    "Usá un nombre existente.",
                )
            ],
        )
    previous = ticket.owner_user_id
    ticket.owner_user_id = owner.id
    ticket.updated_at = utcnow()
    _add_event(
        db, ticket, EventKind.ASSIGNED, principal, {"from_user": previous, "to_user": owner.name}
    )
    return Result(ok=True, data={"code": ticket.code, "owner_user": owner.name})


def add_comment(
    db: OrmSession, principal: Principal, code: str, body: str, project: str | None = None
) -> Result:
    ticket, error = _resolve_ticket(db, code, project)
    if ticket is None:
        assert error is not None
        return error
    denied = _scope_denied(ticket, principal)
    if denied is not None:
        return denied
    comment = Comment(
        ticket_id=ticket.id,
        author_user_id=None if principal.agent_id else principal.user_id,
        author_agent_id=principal.agent_id,
        body=body.strip(),
        created_at=utcnow(),
    )
    db.add(comment)
    db.flush()
    _add_event(db, ticket, EventKind.COMMENTED, principal, {"comment_id": comment.id})
    return Result(ok=True, data={"comment_id": comment.id})


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


def _would_cycle(db: OrmSession, project_id: int, from_id: int, to_id: int) -> bool:
    """'from' bloquea a 'to'. Cicla si 'to' ya alcanza a 'from'."""
    graph = repo.blocks_graph(db, project_id)
    seen: set[int] = set()
    stack = [to_id]
    while stack:
        node = stack.pop()
        if node == from_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return False


def link_tickets(
    db: OrmSession,
    principal: Principal,
    from_code: str,
    to_ref: str,
    kind: str,
    project: str | None = None,
) -> Result:
    src, src_error = _resolve_ticket(db, from_code, project)
    if src is None:
        assert src_error is not None
        return src_error
    denied = _scope_denied(src, principal)
    if denied is not None:
        return denied
    # El destino se busca DENTRO del proyecto del origen: los códigos se
    # repiten entre proyectos y un OS-001 de otro proyecto no es el destino.
    dst = repo.get_ticket_by_code(db, to_ref, src.project.slug)
    if dst is None:
        dst = repo.get_ticket_by_external_key(db, src.project_id, to_ref)
    if dst is None:
        otros = repo.tickets_by_code(db, to_ref)
        if otros:
            slugs = ", ".join(slug for slug, _ in otros)
            return Result(
                ok=False,
                violations=[
                    _v(
                        "E_TICKET_OTHER_PROJECT",
                        "to",
                        f"'{to_ref}' existe en el proyecto '{otros[0][0]}', no en '{src.project.slug}'.",
                        f"Usá el código del proyecto '{src.project.slug}' o un external_key "
                        f"del mismo proyecto. El código existe en: {slugs}.",
                    )
                ],
            )
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_UNKNOWN_TICKET",
                    "to",
                    f"No existe el ticket '{to_ref}'.",
                    "Usá su code (OS-003) o su external_key.",
                )
            ],
        )
    if src.id == dst.id:
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_SELF_LINK",
                    "to",
                    "Un ticket no puede vincularse consigo mismo.",
                    "Elegí otro ticket.",
                )
            ],
        )
    if kind == LinkKind.BLOCKS and _would_cycle(db, src.project_id, src.id, dst.id):
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_DEPS_CYCLE",
                    "to",
                    f"'{src.code}' ya está bloqueado (directa o indirectamente) por '{dst.code}'.",
                    "Usá relates_to si querés registrar la relación sin implication de orden.",
                )
            ],
        )
    link = TicketLink(from_ticket_id=src.id, to_ticket_id=dst.id, kind=kind, created_at=utcnow())
    db.add(link)
    db.flush()
    _add_event(db, src, EventKind.RELINKED, principal, {"to": dst.code, "kind": kind})
    return Result(ok=True, data={"from": src.code, "to": dst.code, "kind": kind})


# --------------------------------------------------------------------------- #
# Revisión
# --------------------------------------------------------------------------- #


def review_ticket(
    db: OrmSession,
    principal: Principal,
    code: str,
    verdict: str,
    note: str | None,
    project: str | None = None,
) -> Result:
    ticket, error = _resolve_ticket(db, code, project)
    if ticket is None:
        assert error is not None
        return error
    denied = _scope_denied(ticket, principal)
    if denied is not None:
        return denied
    if verdict == ReviewVerdict.CHANGES_REQUESTED and not (note or "").strip():
        return Result(
            ok=False,
            violations=[
                _v(
                    "E_REVIEW_NOTE_REQUIRED",
                    "note",
                    "Pedir cambios sin decir cuáles no es una opinión.",
                    "Escribí qué hay que cambiar para que el ticket sirva.",
                )
            ],
        )
    review = TicketReview(
        ticket_id=ticket.id,
        reviewer_user_id=None if principal.agent_id else principal.user_id,
        reviewer_agent_id=principal.agent_id,
        verdict=verdict,
        note=(note or "").strip() or None,
        ticket_content_hash=ticket.content_hash,
        created_at=utcnow(),
    )
    db.add(review)
    db.flush()
    _add_event(db, ticket, EventKind.REVIEWED, principal, {"verdict": verdict})
    return Result(
        ok=True,
        data={
            "code": ticket.code,
            "review_id": review.id,
            "stale": False,
            "current_content_hash": ticket.content_hash,
        },
    )


def _actor_label(user: User | None, agent: Agent | None) -> str:
    if agent is not None:
        return agent.name
    if user is not None:
        return user.name
    return "?"


def ticket_activity(
    db: OrmSession, ticket: Ticket, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Conversación + historial, en una sola línea de tiempo.

    Es lo que le permite a un agente nuevo retomar el caso sin leer el chat
    anterior: comentarios y eventos, con autor y fecha.
    """
    comments = repo.get_comments(db, ticket.id)
    events = repo.get_events(db, ticket.id, limit=200, offset=0)
    items: list[dict[str, Any]] = []
    for comment in comments:
        items.append(
            {
                "kind": "comment",
                "at": comment.created_at.isoformat() if comment.created_at else None,
                "author": _actor_label(comment.author_user, comment.author_agent),
                "author_kind": "agent" if comment.author_agent_id else "user",
                "body": comment.body,
            }
        )
    for event in events:
        items.append(
            {
                "kind": "event",
                "at": event.created_at.isoformat() if event.created_at else None,
                "author": _actor_label(event.actor_user, event.actor_agent),
                "author_kind": "agent" if event.actor_agent_id else "user",
                "event": event.kind,
                "payload": event.payload,
            }
        )
    items.sort(key=lambda item: (item["at"] or "", item["kind"]))
    window = items[offset : offset + limit]
    return {
        "code": ticket.code,
        "project": ticket.project.slug,
        "count": len(window),
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "items": window,
    }


def workflow_for(ticket: Ticket, principal: Principal) -> dict[str, Any]:
    """Qué puede hacer este actor con este ticket, y qué le falta.

    Los datos se calculan en el servicio: el adaptador MCP no decide reglas.
    """
    status = TicketStatus(ticket.status)
    targets = allowed_targets(status)
    missing = missing_for_ready(ticket)
    in_scope = principal.can_write_project(ticket.project.slug)
    can_mutate = in_scope and _can_mutate(ticket, principal)
    actions: list[dict[str, Any]] = []
    for target in targets:
        if target == str(TicketStatus.READY):
            if missing:
                actions.append(
                    {
                        "action": "set_status",
                        "target": "ready",
                        "available": False,
                        "reason": "Faltan: " + ", ".join(m["field"] for m in missing),
                    }
                )
                continue
            actions.append({"action": "set_status", "target": "ready", "available": True})
        elif target == str(TicketStatus.WONT):
            actions.append(
                {
                    "action": "set_status",
                    "target": "wont",
                    "available": True,
                    "reason": "Exige una nota con el motivo.",
                }
            )
        else:
            actions.append({"action": "set_status", "target": target, "available": True})
    return {
        "status": status,
        "allowed_transitions": targets,
        "missing_for_ready": missing,
        "capabilities": {
            "in_scope": in_scope,
            "can_mutate": can_mutate,
            "can_comment": in_scope,
            "can_review": in_scope,
            "can_close": can_transition(status, TicketStatus.DONE),
        },
        "actions": actions,
        "contract": "opensupport://workflow/support",
    }


def review_summary(db: OrmSession, ticket: Ticket) -> dict[str, Any]:
    """Estado de revisión vigente vs histórico, según el content_hash actual."""
    reviews = repo.get_reviews(db, ticket.id)
    current: dict[str, TicketReview] = {}  # clave: reviewer_agent_id o reviewer_user_id
    historical: list[TicketReview] = []
    for review in reviews:
        if review.ticket_content_hash == ticket.content_hash:
            reviewer = str(review.reviewer_agent_id or review.reviewer_user_id)
            if reviewer not in current or review.created_at > current[reviewer].created_at:
                current[reviewer] = review
        else:
            historical.append(review)
    changes = sum(1 for r in current.values() if r.verdict == ReviewVerdict.CHANGES_REQUESTED)
    approvals = sum(1 for r in current.values() if r.verdict == ReviewVerdict.APPROVED)
    return {
        "current_verdicts": [
            {"verdict": r.verdict, "note": r.note, "created_at": r.created_at.isoformat()}
            for r in current.values()
        ],
        "historical_count": len(historical),
        "approvals": approvals,
        "changes_requested": changes,
        "reviewed": bool(current),
        "needs_review": not current,
        "historical": [
            {"verdict": r.verdict, "note": r.note, "created_at": r.created_at.isoformat()}
            for r in historical
        ],
    }


def _add_event(
    db: OrmSession,
    ticket: Ticket,
    kind: EventKind,
    principal: Principal,
    payload: dict[str, Any],
) -> None:
    db.add(
        TicketEvent(
            ticket_id=ticket.id if ticket.id and ticket.id > 0 else 0,
            kind=kind,
            actor_user_id=None if principal.agent_id else principal.user_id,
            actor_agent_id=principal.agent_id,
            payload=payload,
            created_at=utcnow(),
        )
    )
    db.flush()


def touch(ticket: Ticket) -> datetime:
    ticket.updated_at = utcnow().astimezone(UTC)
    return ticket.updated_at


def ensure_agent(
    db: OrmSession, principal: Principal, name: str, client: str = AgentClient.OTHER
) -> Agent:
    from sqlalchemy import select

    agent = db.scalar(select(Agent).where(Agent.name == name))
    if agent is None:
        agent = Agent(user_id=principal.user_id, name=name, client=client, created_at=utcnow())
        db.add(agent)
        db.flush()
    return agent


def get_session_row(db: OrmSession, code: str) -> Session | None:
    return repo.get_session_by_code(db, code)
