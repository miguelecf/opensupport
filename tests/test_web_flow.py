"""La web real: formularios, CSRF, proyecto y credenciales.

Un agente con browser navega estas páginas. Si el CSRF solo funciona con
headers, los formularios normales quedan rotos; si el token nuevo viaja en la
URL, queda en el historial del navegador.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
    from app.tickets import service

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


def test_el_formulario_normal_manda_el_csrf_en_el_cuerpo(
    authed: tuple[TestClient, str, str],
) -> None:
    """Un POST de HTML no manda headers: el token va en el campo del form."""
    client, csrf, code = authed
    response = client.post(
        f"/t/{code}/comment",
        data={"body": "un comentario", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_sin_csrf_no_hay_mutacion(authed: tuple[TestClient, str, str]) -> None:
    from app.db.base import session_scope
    from app.domain.models import Comment

    client, _csrf, code = authed
    response = client.post(f"/t/{code}/comment", data={"body": "no"}, follow_redirects=False)
    assert response.status_code == 403
    with session_scope() as db:
        assert db.query(Comment).count() == 0


def test_el_formulario_de_crear_proyecto_lleva_csrf(authed: tuple[TestClient, str, str]) -> None:
    client, csrf, _code = authed
    response = client.post(
        "/settings/projects",
        data={"slug": "nuevo-proyecto", "name": "Nuevo", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.get("/settings/projects")


def test_el_token_nuevo_no_viaja_en_la_url(authed: tuple[TestClient, str, str]) -> None:
    """El token se muestra una vez, y no queda en el historial del navegador."""
    client, csrf, _code = authed
    response = client.post(
        "/settings/tokens",
        data={
            "name": "otro",
            "projects": "",
            "agent_name": "",
            "client": "other",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "os_" not in response.headers["location"]
    body = client.get("/settings/tokens").text
    assert "os_" in body  # se muestra en la página, una sola vez
    assert "os_" not in client.get("/settings/tokens").text  # y después desaparece


def test_no_se_puede_revocar_el_token_de_otro(authed: tuple[TestClient, str, str]) -> None:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    client, csrf, _code = authed
    with session_scope() as db:
        otro = ensure_user(db, "ana")
        _plain, row = create_token(db, user=otro, name="de-ana")
        db.commit()
        token_id = row.id
    response = client.post(
        f"/settings/tokens/{token_id}/revoke", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 403


def test_el_board_con_texto_no_revienta(authed: tuple[TestClient, str, str]) -> None:
    client, _csrf, _code = authed
    response = client.get("/", params={"q": "JWT"})
    assert response.status_code == 200


def test_el_detalle_muestra_el_contexto_de_soporte(client: TestClient) -> None:
    from app.auth.tokens import Principal, create_token, ensure_user
    from app.db.base import session_scope, utcnow
    from app.domain.models import Project
    from app.tickets import service

    with session_scope() as db:
        user = ensure_user(db, "fede")
        if db.query(Project).filter_by(slug="mi-tesis").one_or_none() is None:
            db.add(Project(slug="mi-tesis", name="Mi tesis", created_at=utcnow()))
            db.flush()
        plain, _ = create_token(db, user=user, name="web-demo")
        principal = Principal(
            user_id=user.id,
            username=user.name,
            is_admin=False,
            scopes=["tickets:read", "tickets:write"],
            project_scope=[],
        )
        from tests.fakes.valid_draft import make_incident_draft

        code = service.create_ticket(db, principal, make_incident_draft()).data["code"]
        db.commit()
    client.post("/login", data={"token": plain})
    body = client.get(f"/t/{code}", params={"project": "mi-tesis"}).text
    assert "Contexto de soporte" in body
    assert "La sesión móvil se cierra a los 5 minutos" in body
    # El proyecto viaja explícito en los formularios del detalle.
    assert "?project=mi-tesis" in body
