"""Vistas web: HTML para humanos. No es una API (ver docs/PLAN.md).

Reglas que esta capa respeta:
- Todo acceso a DB ocurre en un hilo: las vistas son async y SQLite es sync.
- Toda mutación exige CSRF, por header o por campo de formulario.
- El proyecto viaja explícito: los códigos de ticket se repiten entre
  proyectos, así que la URL y los formularios lo llevan siempre.
- El token nuevo no viaja en la URL: viaja en una cookie de un solo uso.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from anyio import to_thread
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from markupsafe import Markup, escape
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

CSRF_COOKIE = "opensupport_csrf"
CSRF_HEADER = "X-CSRF-Token"
NEW_TOKEN_COOKIE = "opensupport_new_token"


async def _db[T](fn: Callable[[OrmSession], T]) -> T:
    """Corre el trabajo de DB en un hilo. Nunca bloquea el event loop."""

    def _call() -> T:
        with session_scope() as db:
            return fn(db)

    return await to_thread.run_sync(_call)


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


def _csrf(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE, "")


def csrf_field(token: str) -> Markup:
    """Input hidden con el token CSRF. Se inyecta en cada formulario.

    Los formularios de HTML normal no mandan headers: el token va en el
    cuerpo. Es lo que permite que un POST de navegador funcione sin depender
    de JavaScript.
    """
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(token)}">')


async def require_csrf(request: Request) -> None:
    """Toda mutación web exige el token CSRF.

    La web se autentica por cookie, así que un agente con browser (computer-use)
    navegando a una página hostil podría disparar acciones con tu sesión.
    `SameSite=Lax` cubre el caso común; esto cubre el resto.

    El token puede venir en el header (lo que manda htmx) o en el campo
    `csrf_token` del formulario (lo que manda un POST normal de HTML).
    """
    expected = _csrf(request)
    given = request.headers.get(CSRF_HEADER, "")
    if not given:
        token_field = ""
        try:
            form = await request.form()
            token_field = str(form.get("csrf_token", "") or "")
        except Exception:  # noqa: BLE001 - un body que no es form no tiene token
            token_field = ""
        given = token_field
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

    def _run(db: OrmSession) -> Principal | None:
        return resolve_token(db, plain)

    principal = await _db(_run)
    if principal is None:
        return templates.TemplateResponse(
            request, "login.html", {"request": request, "error": "Token inválido o revocado."}
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
    await require_csrf(request)
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


def _board_items(db: OrmSession, **filters: Any) -> list[dict[str, Any]]:
    """Filas del board. La búsqueda por texto y los filtros se combinan."""
    query = filters.get("q")
    if query:
        hits = repo.search_tickets(db, query, limit=50, project=filters.get("project"))
        rows = [t for t, _ in hits]
        fragments = {t.id: frag for t, frag in hits}
        status = filters.get("status")
        if status:
            rows = [t for t in rows if t.status == status]
    else:
        rows = repo.list_tickets(
            db,
            status=filters.get("status"),
            project=filters.get("project"),
            owner=filters.get("owner"),
            tag=filters.get("tag"),
            needs_review=bool(filters.get("needs_review")),
            limit=200,
        )
        fragments = {}
    return [
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


def _board_groups(db: OrmSession, **filters: Any) -> list[dict[str, Any]]:
    """Tickets agrupados por proyecto, con metadata y conteos.

    El proyecto es el contenedor: cada grupo trae su nombre, descripción,
    responsables y los tickets que coinciden con el filtro. Con un filtro
    activo, los proyectos sin coincidencias no se muestran; en el board
    limpio se ven todos, aunque estén vacíos.
    """
    items = _board_items(db, **filters)
    projects = {p.slug: p for p in repo.all_projects(db)}
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_slug.setdefault(it["project"], []).append(it)

    has_filter = bool(
        filters.get("project")
        or filters.get("q")
        or filters.get("status")
        or filters.get("owner")
        or filters.get("tag")
        or filters.get("needs_review")
    )

    groups: list[dict[str, Any]] = []
    ordered = sorted(projects.values(), key=lambda p: (p.name or p.slug).lower())
    for project in ordered:
        tickets = by_slug.get(project.slug, [])
        if has_filter and not tickets:
            continue
        status_counts: dict[str, int] = {}
        for t in tickets:
            status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
        groups.append(
            {
                "slug": project.slug,
                "name": project.name,
                "description": project.description,
                "archived": project.archived_at is not None,
                "owners": sorted({t["owner"] for t in tickets}),
                "status_counts": status_counts,
                "needs_review": sum(1 for t in tickets if t["review"]["needs_review"]),
                "tickets": tickets,
            }
        )
    return groups


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

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "groups": _board_groups(
                    db,
                    status=status,
                    project=project,
                    owner=owner,
                    tag=tag,
                    q=q,
                    needs_review=needs_review,
                ),
                "projects": [{"slug": p.slug, "name": p.name} for p in repo.all_projects(db)],
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

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    return response


@router.get("/_ui/board", response_class=HTMLResponse)
async def board_partial(
    request: Request,
    status: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    needs_review: bool = False,
) -> Any:  # noqa: ANN401
    """Fragmento HTMX. Es plumbing de UI, no un contrato programable."""
    templates = request.app.state.templates

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        return templates.TemplateResponse(
            request,
            "partials/_board.html",
            {
                "groups": _board_groups(
                    db,
                    status=status,
                    project=project,
                    owner=owner,
                    tag=tag,
                    q=q,
                    needs_review=needs_review,
                )
            },
        )

    response = await _db(_run)
    if response is None:
        return HTMLResponse("", status_code=401)
    return response


# --------------------------------------------------------------------------- #
# Detalle de ticket
# --------------------------------------------------------------------------- #


@router.get("/t/{code}", response_class=HTMLResponse)
async def ticket_detail(request: Request, code: str, project: str | None = None) -> Any:  # noqa: ANN401
    templates = request.app.state.templates

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        ticket = repo.get_ticket_by_code(db, code, project)
        if ticket is None:
            slugs = repo.projects_for_ticket_code(db, code)
            if project is None and len(slugs) > 1:
                links = " · ".join(f'<a href="/t/{code}?project={s}">{s}</a>' for s in slugs)
                return HTMLResponse(
                    f"<h1>{code} existe en varios proyectos</h1><p>Elegí: {links}</p>",
                    status_code=409,
                )
            return HTMLResponse("<h1>404</h1><p>Ese ticket no existe.</p>", status_code=404)
        comments = repo.get_comments(db, ticket.id)
        events = repo.get_events(db, ticket.id, limit=100, offset=0)
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

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    return response


async def _mutate(request: Request, fn: Callable[[OrmSession, Principal], Any]) -> Any:  # noqa: ANN401
    """Aplica una mutación web y devuelve el mensaje de resultado."""

    def _run(db: OrmSession) -> tuple[Principal | None, Any]:
        user = current_user(db, request)
        if user is None:
            return None, None
        return _principal_for(user), fn(db, _principal_for(user))

    principal, result = await _db(_run)
    if principal is None:
        return None
    return result


@router.post("/t/{code}/status")
async def change_status(
    request: Request,
    code: str,
    status: str = Form(),
    note: str = Form(""),
    project: str | None = None,
) -> Any:  # noqa: ANN401
    await require_csrf(request)
    result = await _mutate(
        request,
        lambda db, principal: tickets.set_status(
            db, principal, code, status, note or None, project
        ),
    )
    if result is None:
        return RedirectResponse("/login", status_code=303)
    message = "" if result.ok else result.violations[0].message
    target = f"/t/{code.upper()}"
    if project:
        target += f"?project={project}"
    if message:
        target += f"&msg={message}"
    return RedirectResponse(target, status_code=303)


@router.post("/t/{code}/review")
async def post_review(
    request: Request,
    code: str,
    verdict: str = Form(),
    note: str = Form(""),
    project: str | None = None,
) -> Any:  # noqa: ANN401
    await require_csrf(request)
    result = await _mutate(
        request,
        lambda db, principal: tickets.review_ticket(
            db, principal, code, verdict, note or None, project
        ),
    )
    if result is None:
        return RedirectResponse("/login", status_code=303)
    message = "" if result.ok else result.violations[0].message
    target = f"/t/{code.upper()}"
    if project:
        target += f"?project={project}"
    if message:
        target += f"&msg={message}"
    return RedirectResponse(target, status_code=303)


@router.post("/t/{code}/comment")
async def post_comment(
    request: Request,
    code: str,
    body: str = Form(),
    project: str | None = None,
) -> Any:  # noqa: ANN401
    await require_csrf(request)
    result = await _mutate(
        request, lambda db, principal: tickets.add_comment(db, principal, code, body, project)
    )
    if result is None:
        return RedirectResponse("/login", status_code=303)
    target = f"/t/{code.upper()}"
    if project:
        target += f"?project={project}"
    return RedirectResponse(target, status_code=303)


# --------------------------------------------------------------------------- #
# Sesiones
# --------------------------------------------------------------------------- #


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(request: Request, project: str | None = None) -> Any:  # noqa: ANN401
    templates = request.app.state.templates

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        rows = [
            {
                "code": r.code,
                "title": r.title,
                "goal": r.goal,
                "status": r.status,
                "project": r.project.slug,
            }
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

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    return response


@router.get("/s/{code}", response_class=HTMLResponse)
async def session_detail(request: Request, code: str, project: str | None = None) -> Any:  # noqa: ANN401
    templates = request.app.state.templates

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        principal = _principal_for(user)
        context = sessions.join_session(db, principal, code, project=project)
        if not context.ok:
            return HTMLResponse(
                f"<h1>404</h1><p>{context.violations[0].message}</p>", status_code=404
            )
        return templates.TemplateResponse(
            request,
            "session.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "ctx": context.data,
                "project": project or context.data.get("project"),
            },
        )

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    return response


# --------------------------------------------------------------------------- #
# Tokens y proyectos
# --------------------------------------------------------------------------- #


def _read_new_token(request: Request) -> str | None:
    raw = request.cookies.get(NEW_TOKEN_COOKIE)
    if not raw:
        return None
    try:
        return str(_serializer.loads(raw, max_age=300))
    except BadSignature:
        return None


@router.get("/settings/tokens", response_class=HTMLResponse)
async def tokens_page(request: Request) -> Any:  # noqa: ANN401
    templates = request.app.state.templates
    new_token = _read_new_token(request)

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        from app.auth.tokens import list_tokens

        rows = list_tokens(db, user.id)
        return templates.TemplateResponse(
            request,
            "tokens.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "tokens": rows,
                "projects": [p.slug for p in repo.all_projects(db)],
                "new_token": new_token,
            },
        )

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    if new_token:
        # El token se muestra una sola vez: después de esta respuesta, la
        # cookie se borra y no queda en el historial del navegador.
        response.delete_cookie(NEW_TOKEN_COOKIE)
    return response


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

    await require_csrf(request)

    def _run(db: OrmSession) -> str | None:
        user = current_user(db, request)
        if user is None:
            return None
        agent = None
        if agent_name.strip():
            agent = ensure_agent(db, _principal_for(user), agent_name.strip(), client)
        scope = [s.strip() for s in projects.split(",") if s.strip()]
        plain, _row = create_token(
            db, user=user, name=name.strip() or "token", agent=agent, project_scope=scope
        )
        db.commit()
        return plain

    plain = await _db(_run)
    if plain is None:
        return RedirectResponse("/login", status_code=303)
    # El token no va en la URL: viaja en una cookie firmada de un solo uso.
    response = RedirectResponse("/settings/tokens", status_code=303)
    response.set_cookie(
        NEW_TOKEN_COOKIE,
        _serializer.dumps(plain),
        httponly=True,
        samesite="lax",
        max_age=300,
    )
    return response


@router.post("/settings/tokens/{token_id}/revoke")
async def revoke(request: Request, token_id: int) -> Any:  # noqa: ANN401
    from app.auth.tokens import revoke_token

    await require_csrf(request)

    def _run(db: OrmSession) -> bool:
        user = current_user(db, request)
        if user is None:
            return False
        from app.domain.models import ApiToken

        row = db.get(ApiToken, token_id)
        # Revocar credenciales ajenas no es una acción de la web.
        if row is None or (row.user_id != user.id and not user.is_admin):
            return False
        revoke_token(db, token_id)
        return True

    ok = await _db(_run)
    if not ok:
        return HTMLResponse("<h1>403</h1><p>Ese token no es tuyo.</p>", status_code=403)
    return RedirectResponse("/settings/tokens", status_code=303)


@router.get("/settings/projects", response_class=HTMLResponse)
async def projects_page(request: Request) -> Any:  # noqa: ANN401
    templates = request.app.state.templates

    def _run(db: OrmSession) -> Any:
        user = current_user(db, request)
        if user is None:
            return None
        return templates.TemplateResponse(
            request,
            "projects.html",
            {
                "request": request,
                "csrf_token": _csrf(request),
                "user": user,
                "projects": repo.all_projects(db),
            },
        )

    response = await _db(_run)
    if response is None:
        return RedirectResponse("/login", status_code=303)
    return response


@router.post("/settings/projects")
async def create_project(request: Request, slug: str = Form(), name: str = Form()) -> Any:  # noqa: ANN401
    from app.db.base import utcnow

    await require_csrf(request)

    def _run(db: OrmSession) -> None:
        user = current_user(db, request)
        if user is None:
            return
        clean = slug.strip().lower().replace(" ", "-")
        if clean and repo.get_project_by_slug(db, clean) is None:
            db.add(Project(slug=clean, name=name.strip() or clean, created_at=utcnow()))

    await _db(_run)
    return RedirectResponse("/settings/projects", status_code=303)
