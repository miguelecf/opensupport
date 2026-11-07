"""OpenSupport: un monolito, un puerto. La web y el MCP comparten el service layer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.mcp.server import mcp
from app.api.web import router as web
from app.api.web.views import csrf_field
from app.db.base import db_integrity_check, db_is_ready, get_engine
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


class _McpAtRoot:
    """Sirve el transporte MCP en /mcp y en /mcp/ sin redirect.

    Montado en "/mcp", Starlette responde 307 para el path sin slash antes de que
    el app vea la request. Montándolo en "/" (al final, después de las rutas web)
    el shim recibe el path completo y lo reduce: /mcp y /mcp/ funcionan igual, sin
    depender de que el cliente siga redirects — que no todos lo hacen en POST.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # El transporte matchea exactamente "/". Le llega el path completo porque
        # está montado en "/": se lo reducimos para /mcp y /mcp/.
        if scope["type"] == "http" and scope["path"] in ("/mcp", "/mcp/"):
            scope = dict(scope, path="/")
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    """Cada app tiene su propio transporte MCP.

    El transporte abre un task group en su lifespan. Si dos apps comparten la
    misma instancia, el ciclo de vida de una apaga el de la otra: un TestClient
    que entra y sale deja muerto al servidor MCP de al lado.
    """
    mcp_inner = mcp.http_app(path="/", transport="http", stateless_http=True)

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        get_engine()
        async with mcp_inner.lifespan(app):
            yield

    app = FastAPI(
        title="OpenSupport",
        description="Board de tickets agent-native. Las máquinas hablan MCP, los humanos usan la web.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # No hay API pública: no exponemos un contrato JSON.
        lifespan=app_lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(web.router)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["md"] = _markdown_filter
    templates.env.filters["highlight"] = _highlight_filter
    templates.env.globals["csrf_field"] = csrf_field
    app.state.templates = templates

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    async def readyz(request: Request) -> HTMLResponse | dict[str, object]:
        # La readiness pega a la DB: va en un hilo, como todo acceso a disco.
        ready, detail = await to_thread.run_sync(db_is_ready)
        integrity_ok, integrity = await to_thread.run_sync(db_integrity_check)
        healthy = ready and integrity_ok
        if request.headers.get("accept", "").startswith("text/html"):
            body = f"ok ({detail})" if healthy else f"no: {detail}; integridad={integrity}"
            return HTMLResponse(body, status_code=200 if healthy else 503)
        return {
            "status": "ok" if healthy else "error",
            "database": ready,
            "detail": detail,
            "integrity": integrity,
        }

    @app.get("/api/v1/{path:path}", include_in_schema=False, response_model=None)
    async def no_rest(path: str) -> HTMLResponse:
        # El plan decidió no tener REST. Esto responde 404 explícito, no un 405 confuso.
        return HTMLResponse(
            f"<h1>404</h1><p>OpenSupport no expone API REST: <code>{path}</code> no existe. "
            "Las máquinas usan MCP en <code>/mcp</code>.</p>",
            status_code=404,
        )

    # El MCP va al final: matchea todo lo que no agarraron las rutas de arriba.
    app.mount("/", _McpAtRoot(mcp_inner))
    return app


def _markdown_filter(text: str) -> str:  # noqa: ANN201
    """Markdown mínimo para contenido que el agente ya controla."""
    import html
    import re

    escaped = html.escape(text or "")
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?m)^- (.+)$", r"<li>\1</li>", escaped)
    escaped = re.sub(r"(?m)^\d+\. (.+)$", r"<li>\1</li>", escaped)
    return escaped.replace("\n", "<br>")


def _highlight_filter(text: str) -> str:  # noqa: ANN201
    """Convierte los delimitadores del snippet FTS (<<...>>) en <mark> seguro.

    Se escapa todo primero y recién después se inyecta la marca: el texto del
    ticket nunca se trata como HTML, pero el resaltado de la búsqueda sí.
    """
    import html

    from markupsafe import Markup

    escaped = html.escape(text or "")
    escaped = escaped.replace("&lt;&lt;", "<mark>").replace("&gt;&gt;", "</mark>")
    return Markup(escaped)


app = create_app()
