"""Proyectos: descubrimiento para agentes.

El proyecto es la frontera de contexto y de autorización: el código de un
ticket es único por proyecto, así que un agente tiene que saber en cuál está
antes de operar. Acá vive el descubrimiento: qué proyectos existen, qué
significa cada uno y qué puede hacer el token que pregunta.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.auth.tokens import Principal
from app.domain.models import Project


def _project_row(db: OrmSession, slug: str) -> Project | None:
    return db.scalar(select(Project).where(Project.slug == slug))


def _describe(project: Project, principal: Principal) -> dict[str, Any]:
    slug = project.slug
    writable = principal.can_write_project(slug)
    return {
        "slug": slug,
        "name": project.name,
        "description": project.description,
        "archived": project.archived_at is not None,
        "permissions": {
            "read": True,
            "write": writable,
            "scope": "restricted" if principal.project_scope else "all",
        },
        "allowed_projects": list(principal.project_scope),
    }


def list_projects(
    db: OrmSession,
    principal: Principal,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Proyectos visibles para el principal, con filtro por texto.

    No filtra por alcance: un proyecto fuera del alcance del token se
    muestra igual, pero con `permissions.write` en false. Es más útil que
    esconderlo: el agente sabe que existe y que no puede escribir ahí.
    """
    stmt = select(Project).order_by(Project.slug)
    if query:
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(Project.slug.ilike(needle) | Project.name.ilike(needle))
    rows = list(db.scalars(stmt))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "count": len(page),
        "total": total,
        "next_offset": offset + limit if offset + limit < total else None,
        "items": [_describe(project, principal) for project in page],
    }


def get_project(db: OrmSession, principal: Principal, slug: str) -> dict[str, Any]:
    """Un proyecto por slug, o un error con los nombres posibles."""
    project = _project_row(db, slug.strip())
    if project is None:
        known = [p.slug for p in db.scalars(select(Project).order_by(Project.slug))]
        return {
            "ok": False,
            "error": f"El proyecto '{slug}' no existe.",
            "violations": [
                {
                    "code": "E_UNKNOWN_PROJECT",
                    "field": "project",
                    "message": f"El proyecto '{slug}' no existe.",
                    "fix": "Proyectos existentes: "
                    + (", ".join(known) if known else "(ninguno)")
                    + ". Usá list_projects para verlos con descripción.",
                }
            ],
        }
    return {"ok": True, "project": _describe(project, principal)}
