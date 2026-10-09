"""Compatibilidad con clientes MCP reales, verificada con JSON-RPC crudo.

`fastmcp.Client` prueba la API de FastMCP, no el protocolo. Esto habla HTTP
contra el spec directamente: si un cliente modesto no puede hacer esto, tampoco
puede OpenSupport.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "raw-probe", "version": "0"},
    },
}
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _rpc(client: TestClient, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/mcp", json=payload, headers={**HEADERS, "Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.text
    if body.startswith("event:"):
        body = body.split("data: ", 1)[1].split("\n\n")[0]
    return json.loads(body)


@pytest.fixture()
def raw(db_url: str) -> Iterator[tuple[TestClient, str]]:
    from app.auth.tokens import create_token, ensure_user
    from app.db.base import session_scope

    with session_scope() as db:
        user = ensure_user(db, "fede")
        token, _ = create_token(db, user=user, name="raw", project_scope=["mi-tesis"])
        db.commit()
    with TestClient(app) as client:
        yield client, token


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_both_paths_work_without_redirect(raw: tuple[TestClient, str], path: str) -> None:
    """El bloque de init --demo dice /mcp. Si eso redirige, el cliente decide."""
    client, token = raw
    response = client.post(
        path, json=INIT, headers={**HEADERS, "Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, f"{path} devolvió {response.status_code}"
    assert response.status_code != 307


def test_no_token_is_401_not_redirect(raw: tuple[TestClient, str]) -> None:
    client, _token = raw
    response = client.post("/mcp", json=INIT, headers=HEADERS)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate")


def test_initialize_reports_server_info(raw: tuple[TestClient, str]) -> None:
    client, token = raw
    result = _rpc(client, token, INIT)["result"]
    assert result["serverInfo"]["name"] == "OpenSupport"
    assert result["protocolVersion"] == "2025-06-18"
    # Las instrucciones viajan en el handshake: son lo primero que ve el agente.
    assert "OpenSupport" in result["instructions"]


def test_every_tool_declares_an_output_schema(raw: tuple[TestClient, str]) -> None:
    client, token = raw
    tools = _rpc(client, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert len(tools) == 18
    for tool in tools:
        assert tool.get("description"), f"{tool['name']} sin descripción"
        assert tool.get("outputSchema"), f"{tool['name']} sin outputSchema"


def test_resources_are_listed_and_readable(raw: tuple[TestClient, str]) -> None:
    client, token = raw
    uris = {
        r["uri"]
        for r in _rpc(client, token, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})[
            "result"
        ]["resources"]
    }
    assert "opensupport://skill/ticket-authoring" in uris
    result = _rpc(
        client,
        token,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "opensupport://skill/ticket-authoring"},
        },
    )["result"]
    assert result["contents"][0]["mimeType"] == "text/markdown"
    assert result["contents"][0]["text"].startswith("---")


def test_validation_errors_come_back_as_data_not_exceptions(raw: tuple[TestClient, str]) -> None:
    """Un error de validación debe ser structuredContent legible, no un exception MCP."""
    client, token = raw
    result = _rpc(
        client,
        token,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "create_ticket",
                "arguments": {
                    "project": "mi-tesis",
                    "title": "Arreglar el login",
                    "summary": "El login anda mal y hay que arreglarlo.",
                    "outcome": "El login funciona bien.",
                    "acceptance": ["Funciona correctamente"],
                },
            },
        },
    )["result"]
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["ok"] is False
    codes = {v["code"] for v in data["violations"]}
    assert "E_ACCEPTANCE_VAGUE" in codes
    assert all(v["fix"] for v in data["violations"])


def test_unknown_tool_is_an_error_result(raw: tuple[TestClient, str]) -> None:
    client, token = raw
    result = _rpc(
        client,
        token,
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "no_existe", "arguments": {}}},
    )["result"]
    assert result["isError"] is True


def test_mounting_mcp_last_does_not_shadow_the_web(raw: tuple[TestClient, str]) -> None:
    """El MCP matchea todo lo que no agarran las rutas web: tiene que ir al final."""
    client, token = raw
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/v1/tickets").status_code == 404
    assert client.get("/static/app.css").status_code == 200
    # follow_redirects=False: sin sesión, el board tiene que redirigir, no servir 200.
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/login").status_code == 200
