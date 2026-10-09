"""Export a markdown. Dos formatos: humano-legible y compatible con `gh issue create`."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.tickets import repository as repo


def _ticket_body(ticket: Any) -> str:  # noqa: ANN401
    lines: list[str] = []
    lines.append("## Resumen")
    lines.append("")
    lines.append(ticket.summary)
    lines.append("")
    if ticket.problem:
        lines += ["## Problema", "", ticket.problem, ""]
    lines += ["## Resultado esperado", "", ticket.outcome, ""]
    lines += ["## Evidencia", ""]
    for item in ticket.evidence or []:
        ref = item.get("path", "")
        if item.get("lines"):
            ref += f":{item['lines']}"
        note = f" — {item['note']}" if item.get("note") else ""
        lines.append(f"- `{ref}`{note}")
    lines += ["", "## Pasos", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(ticket.steps or [], start=1)]
    lines += ["", "## Criterios de aceptación", ""]
    lines += [f"- [ ] {a}" for a in ticket.acceptance or []]
    lines += ["", "## Scope", "", "**Entra:**", ""]
    lines += [f"- {i}" for i in (ticket.scope or {}).get("include", [])]
    lines += ["", "**Queda afuera:**", ""]
    lines += [f"- {e}" for e in (ticket.scope or {}).get("exclude", [])]
    if ticket.risks:
        lines += ["", "## Riesgos", ""]
        lines += [f"- {r}" for r in ticket.risks]
    return "\n".join(lines) + "\n"


def export_github_markdown(db: OrmSession, project: str | None = None) -> str:
    """Markdown con frontmatter YAML. Pipeable a `gh issue create -F -`."""
    out: list[str] = []
    for ticket in repo.list_tickets(db, project=project, limit=500):
        labels = [ticket.type, ticket.priority]
        if ticket.project.slug:
            labels.append(ticket.project.slug)
        for tag in ticket.tags or []:
            labels.append(tag)
        out.append("---")
        out.append(f'title: "{ticket.code} — {ticket.title}"')
        out.append("labels:")
        for label in dict.fromkeys(labels):
            out.append(f"  - {label}")
        out.append("---")
        out.append("")
        out.append(_ticket_body(ticket))
        out.append("")
    return "\n".join(out) + "\n"


def export_markdown(db: OrmSession, project: str | None = None) -> str:
    """Vista del board completa, para commitear a git y releer sin el server."""
    out: list[str] = ["# Board de OpenSupport", ""]
    if project:
        out.append(f"Proyecto: **{project}**")
        out.append("")
    tickets = repo.list_tickets(db, project=project, limit=1000)
    if not tickets:
        out.append("_Sin tickets._")
        return "\n".join(out) + "\n"
    by_status: dict[str, list[Any]] = {}
    for ticket in tickets:
        by_status.setdefault(ticket.status, []).append(ticket)
    for status in sorted(by_status):
        out.append(f"## {status}")
        out.append("")
        for ticket in by_status[status]:
            out.append(f"### {ticket.code} — {ticket.title}")
            out.append("")
            out.append(f"- tipo: {ticket.type} · prioridad: {ticket.priority}")
            out.append(f"- proyecto: {ticket.project.slug}")
            if ticket.session:
                out.append(f"- sesión: {ticket.session.code}")
            if ticket.tags:
                out.append(f"- tags: {', '.join(ticket.tags)}")
            out.append("")
            out.append(_ticket_body(ticket))
    return "\n".join(out) + "\n"
