"""CSRF. La web se autentica por cookie: sin esto, un agente con browser
navegando a una página hostil dispara acciones con tu sesión."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tickets import service

CSRF_HEADER = "X-CSRF-Token"


@pytest.fixture()
def client(db_url: str):  # noqa: ANN201
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed(client: TestClient) -> tuple[TestClient, str, str]:
    """Cliente logueado + csrf token + código de ticket."""
    from app.auth.tokens import Principal, create_token, ensure_user
    from app.db.base import session_scope, utcnow
    from app.domain.models import Project

    with session_scope() as db:
        user = ensure_user(db, "fede")
        if db.query(Project).filter_by(slug="mi-tesis").one_or_none() is None:
            db.add(Project(slug="mi-tesis", name="Mi tesis", created_at=utcnow()))
            db.flush()
        plain, _ = create_token(db, user=user, name="csrf")
        principal = Principal(
            user_id=user.id,
            username=user.name,
            is_admin=False,
            scopes=["tickets:read", "tickets:write"],
            project_scope=[],
        )
        from tests.fakes.valid_draft import make_valid_draft

        code = service.create_ticket(db, principal, make_valid_draft()).data["code"]
        db.commit()
    client.post("/login", data={"token": plain})
    csrf = client.cookies.get("opensupport_csrf", "")
    return client, csrf, code


def test_login_sets_a_csrf_cookie(client: TestClient) -> None:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    with session_scope() as db:
        user = ensure_user(db, "fede")
        plain, _ = create_token(db, user=user, name="csrf-login")
        db.commit()
    client.post("/login", data={"token": plain})
    assert client.cookies.get("opensupport_csrf")


def test_page_exposes_the_token(authed: tuple[TestClient, str, str]) -> None:
    client, csrf, _code = authed
    assert csrf
    body = client.get("/").text
    assert 'name="csrf-token"' in body
    assert csrf in body


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("post", "/t/OS-001/status", {"status": "in_progress"}),
        ("post", "/t/OS-001/comment", {"body": "hola"}),
        ("post", "/t/OS-001/review", {"verdict": "approved"}),
        ("post", "/settings/projects", {"slug": "nuevo", "name": "Nuevo"}),
    ],
)
def test_mutation_without_csrf_is_blocked(
    authed: tuple[TestClient, str, str], method: str, path: str, data: dict[str, str]
) -> None:
    client, _csrf, code = authed
    url = path.replace("OS-001", code)
    response = getattr(client, method)(url, data=data, follow_redirects=False)
    assert response.status_code == 403


def test_mutation_with_wrong_csrf_is_blocked(authed: tuple[TestClient, str, str]) -> None:
    client, _csrf, code = authed
    response = client.post(
        f"/t/{code}/comment",
        data={"body": "hola"},
        headers={CSRF_HEADER: "token-inventado"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_mutation_with_valid_csrf_goes_through(authed: tuple[TestClient, str, str]) -> None:
    client, csrf, code = authed
    response = client.post(
        f"/t/{code}/comment",
        data={"body": "un comentario"},
        headers={CSRF_HEADER: csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_blocked_mutation_did_not_change_anything(authed: tuple[TestClient, str, str]) -> None:
    """El 403 tiene que venir antes de tocar la DB, no después."""
    from app.db.base import session_scope
    from app.domain.models import Comment

    client, _csrf, code = authed
    client.post(f"/t/{code}/comment", data={"body": "no debería existir"}, follow_redirects=False)
    with session_scope() as db:
        assert db.query(Comment).filter(Comment.ticket_id.isnot(None)).count() == 0


def test_login_post_does_not_require_csrf(client: TestClient) -> None:
    """Login es la única excepción: todavía no hay sesión que proteger."""
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    with session_scope() as db:
        user = ensure_user(db, "fede")
        plain, _ = create_token(db, user=user, name="csrf-login-2")
        db.commit()
    response = client.post("/login", data={"token": plain}, follow_redirects=False)
    assert response.status_code == 303


def test_logout_without_csrf_is_blocked(authed: tuple[TestClient, str, str]) -> None:
    client, _csrf, _code = authed
    assert client.post("/logout", follow_redirects=False).status_code == 403
