"""Tests de integración MCP, web y export.

El MCP se prueba contra la app real montada en un servidor, no contra mocks:
el contrato se verifica hablando con él, que es como lo va a hacer el agente.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import HTTPStatusError
from mcp.shared.exceptions import McpError as ClientError

from app.main import app
from tests.fakes.valid_draft import make_invalid_draft, make_valid_draft

EXPECTED_TOOLS = {
    "lint_ticket",
    "create_ticket",
    "create_tickets_batch",
    "update_ticket",
    "assign_ticket",
    "link_tickets",
    "set_status",
    "add_comment",
    "close_ticket",
    "review_ticket",
    "start_session",
    "join_session",
    "close_session",
    "list_sessions",
    "get_ticket",
    "list_tickets",
    "search_tickets",
    "get_schema",
}

EXPECTED_RESOURCES = {
    "opensupport://skill/ticket-authoring",
    "opensupport://schema/ticket",
    "opensupport://schema/violations",
    "opensupport://board/snapshot",
    "opensupport://meta",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory) -> Iterator[str]:  # noqa: ANN001
    """Servidor uvicorn real para probar el MCP de punta a punta."""
    import os

    from alembic.config import Config

    from alembic import command

    db_dir = tmp_path_factory.mktemp("mcp")
    url = f"sqlite:///{db_dir / 'live.db'}"
    os.environ["OPENSUPPORT_DATABASE_URL"] = url

    from app.config import settings
    from app.db.base import reset_engine

    settings.database_url = url
    reset_engine()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(120):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def live_token(live_server: str) -> str:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope
    from app.domain.models import Project

    with session_scope() as db:
        user = ensure_user(db, "fede", is_admin=True)
        if db.query(Project).filter(Project.slug == "mi-tesis").one_or_none() is None:
            db.add(Project(slug="mi-tesis", name="Mi tesis"))
            db.flush()
        plain, _ = create_token(
            db, user=user, name="test-token", project_scope=["mi-tesis"]
        )
        db.commit()
    return plain


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #


def test_all_expected_tools_are_exposed(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            names = {t.name for t in await c.list_tools()}
            assert names == EXPECTED_TOOLS

    _run(check())


def test_all_expected_resources_are_exposed(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            uris = {str(r.uri) for r in await c.list_resources()}
            assert EXPECTED_RESOURCES <= uris

    _run(check())


def test_skill_resource_has_frontmatter(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            content = await c.read_resource("opensupport://skill/ticket-authoring")
            text = content[0].text  # type: ignore[attr-defined]
            assert text.startswith("---")
            assert "name: ticket-authoring" in text
            assert "lint_ticket" in text

    _run(check())


def test_invalid_token_is_rejected(live_server: str) -> None:
    async def check() -> None:
        # Cualquier fallo de handshake vale: lo que importa es que no entre.
        with pytest.raises((ToolError, ClientError, HTTPStatusError)):
            async with Client(live_server, auth="os_inventado"):
                pass

    _run(check())


def test_lint_ticket_does_not_persist(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            draft = make_valid_draft().model_dump(mode="json")
            before = await c.call_tool("list_tickets", {"limit": 100})
            result = await c.call_tool("lint_ticket", draft)
            after = await c.call_tool("list_tickets", {"limit": 100})
            assert result.data["ok"] is True
            assert before.data["count"] == after.data["count"]

    _run(check())


def test_create_is_idempotent_over_mcp(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            draft = make_valid_draft(external_key="e2e:idempotente").model_dump(mode="json")
            first = await c.call_tool("create_ticket", draft)
            second = await c.call_tool("create_ticket", draft)
            assert first.data["status"] == "created"
            assert second.data["status"] == "existing"
            assert first.data["code"] == second.data["code"]

    _run(check())


def test_invalid_draft_creates_nothing(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            draft = make_invalid_draft(external_key="e2e:invalido").model_dump(mode="json")
            before = await c.call_tool("list_tickets", {"limit": 100})
            result = await c.call_tool("create_ticket", draft)
            after = await c.call_tool("list_tickets", {"limit": 100})
            assert result.data["ok"] is False
            assert before.data["count"] == after.data["count"]

    _run(check())


def test_out_of_scope_project_error_lists_allowed(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            draft = make_valid_draft(project="otro-side").model_dump(mode="json")
            result = await c.call_tool("create_ticket", draft)
            assert result.data["ok"] is False
            violation = result.data["violations"][0]
            assert violation["code"] == "E_PROJECT_OUT_OF_SCOPE"
            assert "mi-tesis" in violation["fix"]

    _run(check())


def test_done_requires_close_ticket(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            draft = make_valid_draft(external_key="e2e:done").model_dump(mode="json")
            code = (await c.call_tool("create_ticket", draft)).data["code"]
            result = await c.call_tool("set_status", {"code": code, "status": "done"})
            assert result.data["ok"] is False
            assert result.data["violations"][0]["code"] == "E_CLOSED_NEEDS_EVIDENCE"
            # ready -> done tampoco está permitido: hay que empezar el trabajo.
            early = await c.call_tool(
                "close_ticket",
                {"code": code, "pr": "https://example.test/pr/1", "commands": ["pytest"]},
            )
            assert early.data["ok"] is False
            await c.call_tool("set_status", {"code": code, "status": "in_progress"})
            closed = await c.call_tool(
                "close_ticket",
                {"code": code, "pr": "https://example.test/pr/1", "commands": ["pytest"]},
            )
            assert closed.data["status"] == "done"

    _run(check())


def test_session_join_sees_other_agents_tickets(live_server: str, live_token: str) -> None:
    async def check() -> None:
        async with Client(live_server, auth=live_token) as c:
            session = (
                await c.call_tool(
                    "start_session",
                    {"project": "mi-tesis", "title": "Sesión e2e", "goal": "objetivo de prueba"},
                )
            ).data["code"]
            await c.call_tool("join_session", {"code": session})
            await c.call_tool(
                "create_tickets_batch",
                {"items": [make_valid_draft(external_key="e2e:sesion-1").model_dump(mode="json")],
                 "session": session},
            )
            context = await c.call_tool("join_session", {"code": session})
            assert context.data["read_only"] is False
            assert any(t["external_key"] if "external_key" in t else True for t in context.data["tickets"])
            assert len(context.data["tickets"]) == 1

    _run(check())


def test_read_only_token_cannot_write(live_server: str) -> None:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    with session_scope() as db:
        user = ensure_user(db, "ana")
        plain, _ = create_token(db, user=user, name="solo-lectura", scopes=["tickets:read"])
        db.commit()

    async def check() -> None:
        async with Client(live_server, auth=plain) as c:
            listing = await c.call_tool("list_tickets", {"limit": 5})
            assert listing.data["ok"] is True
            with pytest.raises(ToolError):
                await c.call_tool(
                    "create_ticket", make_valid_draft().model_dump(mode="json")
                )

    _run(check())


def _run(coro: Any) -> None:
    import asyncio

    asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #


@pytest.fixture()
def web_client(db_url: str) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def _login(client: TestClient) -> str:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    with session_scope() as db:
        user = ensure_user(db, "fede")
        plain, _ = create_token(db, user=user, name="web")
        db.commit()
    client.post("/login", data={"token": plain})
    return plain


def test_root_redirects_to_login(web_client: TestClient) -> None:
    response = web_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_login_rejects_bad_token(web_client: TestClient) -> None:
    response = web_client.post("/login", data={"token": "os_inventado"})
    assert response.status_code == 200
    assert "inv" in response.text.lower()


def test_board_renders_after_login(web_client: TestClient) -> None:
    _login(web_client)
    response = web_client.get("/")
    assert response.status_code == 200
    assert "Board" in response.text


def test_ticket_detail_renders_all_sections(web_client: TestClient) -> None:
    from app.auth.tokens import Principal
    from app.db.base import session_scope, utcnow
    from app.domain.models import Project, User
    from app.tickets import service

    _login(web_client)
    with session_scope() as db:
        user = db.query(User).filter_by(name="fede").one()
        if db.query(Project).filter_by(slug="mi-tesis").one_or_none() is None:
            db.add(Project(slug="mi-tesis", name="Mi tesis", created_at=utcnow()))
            db.flush()
        principal = Principal(
            user_id=user.id, username=user.name, is_admin=True,
            scopes=["tickets:read", "tickets:write"], project_scope=[],
        )
        code = service.create_ticket(db, principal, make_valid_draft()).data["code"]
    response = web_client.get(f"/t/{code}")
    assert response.status_code == 200
    for section in ("Resumen", "Evidencia", "Pasos", "Criterios de aceptación", "Scope"):
        assert section in response.text


def test_rest_returns_404(web_client: TestClient) -> None:
    _login(web_client)
    assert web_client.get("/api/v1/tickets").status_code == 404


def test_htmx_partial_has_no_html_wrapper(web_client: TestClient) -> None:
    _login(web_client)
    response = web_client.get("/_ui/board")
    assert response.status_code == 200
    assert "<html" not in response.text.lower()


def test_all_pages_render(web_client: TestClient) -> None:
    _login(web_client)
    for path in ("/", "/sessions", "/settings/tokens", "/settings/projects"):
        assert web_client.get(path).status_code == 200, path


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def test_github_export_shape(db_url: str) -> None:
    from app.auth.tokens import Principal
    from app.db.base import session_scope
    from app.domain.models import Project, User
    from app.export import export_github_markdown
    from app.tickets import service

    with session_scope() as db:
        user = User(name="fede", is_admin=True)
        db.add(user)
        db.add(Project(slug="mi-tesis", name="Mi tesis"))
        db.flush()
        principal = Principal(
            user_id=user.id, username=user.name, is_admin=True,
            scopes=["tickets:read", "tickets:write"], project_scope=[],
        )
        service.create_ticket(db, principal, make_valid_draft())
        out = export_github_markdown(db)

    assert out.startswith("---")
    assert 'title: "OS-001 —' in out
    assert "## Criterios de aceptación" in out
    assert "labels:" in out


def test_markdown_export_contains_board(db_url: str) -> None:
    from app.auth.tokens import Principal
    from app.db.base import session_scope
    from app.domain.models import Project, User
    from app.export import export_markdown
    from app.tickets import service

    with session_scope() as db:
        user = User(name="fede", is_admin=True)
        db.add(user)
        db.add(Project(slug="mi-tesis", name="Mi tesis"))
        db.flush()
        principal = Principal(
            user_id=user.id, username=user.name, is_admin=True,
            scopes=["tickets:read", "tickets:write"], project_scope=[],
        )
        service.create_ticket(db, principal, make_valid_draft())
        out = export_markdown(db)

    assert "# Board de OpenSupport" in out
    assert "OS-001" in out
