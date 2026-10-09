"""Vistas web: HTML para humanos. No es una API (ver docs/PLAN.md)."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session as OrmSession

from app.auth.tokens import Principal, resolve_token
from app.config import settings
from app.db.base import session_scope
from app.domain.models import Project, User
from app.sessions import service as sessions
from app.tickets import repository as repo
from app.tickets import service as tickets

router = APIRouter()
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="opensupport.session")


# --------------------------------------------------------------------------- #
# Sesión web (cookie firmada). Distinta del token MCP por diseño.
# --------------------------------------------------------------------------- #


def make_cookie(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_cookie(request: Request) -> int | None:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        data = _serializer.loads(raw, max_age=settings.session_max_age)
    except BadSignature:
        return None
    uid = data.get("uid")
    return int(uid) if uid is not None else None


def current_user(db: OrmSession, request: Request) -> User | None:
    uid = read_cookie(request)
    return db.get(User, uid) if uid is not None else None


def require_user(db: OrmSession, request: Request) -> User | None:
    return current_user(db, request)


CSRF_COOKIE = "opensupport_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _csrf(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE, "")


def require_csrf(request: Request) -> None:
    """Toda mutación web exige el token CSRF en el header.

    La web se autentica por cookie, así que un agente con browser (computer-use)
    navegando a una página hostil podría disparar acciones con tu sesión.
    `SameSite=Lax` cubre el caso común; esto cubre el resto.
    """
    expected = _csrf(request)
    given = request.headers.get(CSRF_HEADER, "")
    if not expected or not secrets.compare_digest(expected, given):
        raise HTTPException(
            status_code=403,
            detail="Falta el token CSRF. Recargá la página y volvé a intentar.",
        )


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, token: str = Form()) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    plain = token.strip()
    with session_scope() as db:
        principal = resolve_token(db, plain)
        if principal is None:
            return templates.TemplateResponse(
                request,
                "login.html", {"request": request, "error": "Token inválido o revocado."}
            )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        make_cookie(principal.user_id),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.session_max_age,
    )
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), samesite="lax", httponly=False)
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    require_csrf(request)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


# --------------------------------------------------------------------------- #
# Board
# --------------------------------------------------------------------------- #


def _principal_for(user: User) -> Principal:
    """El browser no tiene token: armamos un Principal desde la cookie."""
    return Principal(
        user_id=user.id,
        username=user.name,
        is_admin=user.is_admin,
        scopes=["tickets:read", "tickets:write"],
        project_scope=[],
    )


@router.get("/", response_class=HTMLResponse)
async def board(
    request: Request,
    status: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    needs_review: bool = False,
) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if q:
            rows = [t for t, _ in repo.search_tickets(db, q, limit=50)]
            fragments = {t.id: frag for t, frag in repo.search_tickets(db, q, limit=50)}
        else:
            rows = repo.list_tickets(
                db, status=status, project=project, owner=owner, tag=tag, needs_review=needs_review, limit=200
            )
            fragments = {}
        items = [
            {
                "code": t.code,
                "title": t.title,
                "status": t.status,
                "type": t.type,
                "priority": t.priority,
                "project": t.project.slug,
                "session": t.session.code if t.session else None,
                "owner": t.owner.name,
                "tags": t.tags,
                "fragment": fragments.get(t.id, ""),
                "review": tickets.review_summary(db, t),
                "updated_at": t.updated_at,
            }
            for t in rows
        ]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "items": items,
                "projects": [p.slug for p in repo.all_projects(db)],
                "filters": {
                    "status": status,
                    "project": project,
                    "owner": owner,
                    "tag": tag,
                    "q": q,
                    "needs_review": needs_review,
                },
            },
        )


@router.get("/_ui/board", response_class=HTMLResponse)
async def board_partial(
    request: Request,
    status: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    needs_review: bool = False,
) -> Any:  # noqa: ANN401
    """Fragmento HTMX. Es plumbing de UI, no un contrato programable."""
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return HTMLResponse("", status_code=401)
        rows = repo.list_tickets(
            db, status=status, project=project, owner=owner, tag=tag, needs_review=needs_review, limit=200
        )
        items = [
            {
                "code": t.code,
                "title": t.title,
                "status": t.status,
                "type": t.type,
                "priority": t.priority,
                "project": t.project.slug,
                "session": t.session.code if t.session else None,
                "owner": t.owner.name,
                "tags": t.tags,
                "fragment": "",
                "review": tickets.review_summary(db, t),
                "updated_at": t.updated_at,
            }
            for t in rows
        ]
        return templates.TemplateResponse(request, "partials/_board.html", {"items": items})


# --------------------------------------------------------------------------- #
# Detalle de ticket
# --------------------------------------------------------------------------- #


@router.get("/t/{code}", response_class=HTMLResponse)
async def ticket_detail(
    request: Request, code: str, project: str | None = None
) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        ticket = repo.get_ticket_by_code(db, code, project)
        if ticket is None:
            slugs = repo.projects_for_ticket_code(db, code)
            if project is None and len(slugs) > 1:
                links = " · ".join(f'<a href="/t/{code}?project={s}">{s}</a>' for s in slugs)
                return HTMLResponse(
                    f"<h1>{code} existe en varios proyectos</h1><p>Elegí: {links}</p>", status_code=409
                )
            return HTMLResponse("<h1>404</h1><p>Ese ticket no existe.</p>", status_code=404)
        from app.domain.models import Comment, TicketEvent

        comments = list(
            db.query(Comment).filter(Comment.ticket_id == ticket.id).order_by(Comment.created_at)
        )
        events = list(
            db.query(TicketEvent)
            .filter(TicketEvent.ticket_id == ticket.id)
            .order_by(TicketEvent.created_at.desc())
        )
        return templates.TemplateResponse(
            request,
            "ticket.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "ticket": ticket,
                "comments": comments,
                "events": events,
                "review": tickets.review_summary(db, ticket),
                "links": repo.get_links(db, ticket.id),
            },
        )


@router.post("/t/{code}/status")
async def change_status(
    request: Request, code: str, status: str = Form(), note: str = Form("")
) -> Any:  # noqa: ANN401
    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        principal = _principal_for(user)
        tickets.set_status(db, principal, code, status, note or None)
    return RedirectResponse(f"/t/{code.upper()}", status_code=303)


@router.post("/t/{code}/review")
async def post_review(
    request: Request, code: str, verdict: str = Form(), note: str = Form("")
) -> Any:  # noqa: ANN401
    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        principal = _principal_for(user)
        result = tickets.review_ticket(db, principal, code, verdict, note or None)
        message = "" if result.ok else result.violations[0].message
    return RedirectResponse(f"/t/{code.upper()}?msg={message}", status_code=303)


@router.post("/t/{code}/comment")
async def post_comment(request: Request, code: str, body: str = Form()) -> Any:  # noqa: ANN401
    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        principal = _principal_for(user)
        tickets.add_comment(db, principal, code, body)
    return RedirectResponse(f"/t/{code.upper()}", status_code=303)


# --------------------------------------------------------------------------- #
# Sesiones
# --------------------------------------------------------------------------- #


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(request: Request, project: str | None = None) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        rows = [
            {"code": r.code, "title": r.title, "goal": r.goal, "status": r.status, "project": r.project.slug}
            for r in repo.open_sessions(db)
        ]
        if project:
            rows = [r for r in rows if r["project"] == project]
        return templates.TemplateResponse(
            request,
            "sessions.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "sessions": rows,
                "projects": [p.slug for p in repo.all_projects(db)],
            },
        )


@router.get("/s/{code}", response_class=HTMLResponse)
async def session_detail(
    request: Request, code: str, project: str | None = None
) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        principal = _principal_for(user)
        context = sessions.join_session(db, principal, code, project=project)
        if not context.ok:
            return HTMLResponse(
                f"<h1>404</h1><p>{context.violations[0].message}</p>", status_code=404
            )
        return templates.TemplateResponse(
            request,
            "session.html", {"request": request, "csrf_token": _csrf(request), "user": user, "ctx": context.data}
        )


# --------------------------------------------------------------------------- #
# Tokens y proyectos
# --------------------------------------------------------------------------- #


@router.get("/settings/tokens", response_class=HTMLResponse)
async def tokens_page(request: Request) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        from app.auth.tokens import list_tokens

        rows = list_tokens(db, user.id)
        return templates.TemplateResponse(
            request,
            "tokens.html",
            {"request": request, "csrf_token": _csrf(request), "user": user, "tokens": rows, "projects": [p.slug for p in repo.all_projects(db)]},
        )


@router.post("/settings/tokens")
async def create_token(
    request: Request,
    name: str = Form(),
    projects: str = Form(""),
    agent_name: str = Form(""),
    client: str = Form("other"),
) -> Any:  # noqa: ANN401
    from app.auth.tokens import create_token
    from app.tickets.service import ensure_agent

    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        agent = None
        if agent_name.strip():
            agent = ensure_agent(db, _principal_for(user), agent_name.strip(), client)
        scope = [s.strip() for s in projects.split(",") if s.strip()]
        plain, _row = create_token(
            db, user=user, name=name.strip() or "token", agent=agent, project_scope=scope
        )
        db.commit()
    return RedirectResponse(f"/settings/tokens?new={plain}", status_code=303)


@router.post("/settings/tokens/{token_id}/revoke")
async def revoke(request: Request, token_id: int) -> Any:  # noqa: ANN401
    from app.auth.tokens import revoke_token

    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        revoke_token(db, token_id)
    return RedirectResponse("/settings/tokens", status_code=303)


@router.get("/settings/projects", response_class=HTMLResponse)
async def projects_page(request: Request) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "projects.html", {"request": request, "csrf_token": _csrf(request), "user": user, "projects": repo.all_projects(db)}
        )


@router.post("/settings/projects")
async def create_project(request: Request, slug: str = Form(), name: str = Form()) -> Any:  # noqa: ANN401
    from app.db.base import utcnow

    require_csrf(request)
    with session_scope() as db:
        user = current_user(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        clean = slug.strip().lower().replace(" ", "-")
        if clean and repo.get_project_by_slug(db, clean) is None:
            db.add(Project(slug=clean, name=name.strip() or clean, created_at=utcnow()))
    return RedirectResponse("/settings/projects", status_code=303)
