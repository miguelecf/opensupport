"""OpenSupport: un monolito, un puerto. La web y el MCP comparten el service layer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.mcp.server import mcp
from app.api.web import router as web
from app.db.base import db_is_ready, get_engine

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

mcp_app = mcp.http_app(path="/", transport="http", stateless_http=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """El lifespan del MCP app tiene que correr: es el que inicializa su task group."""
    get_engine()
    async with mcp_app.lifespan(app):
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenSupport",
        description="Board de tickets agent-native. Las máquinas hablan MCP, los humanos usan la web.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # No hay API pública: no exponemos un contrato JSON.
        lifespan=lifespan,
    )
    app.mount("/mcp", mcp_app)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(web.router)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["md"] = _markdown_filter
    app.state.templates = templates

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    async def readyz(request: Request) -> HTMLResponse | dict[str, object]:
        ready = db_is_ready()
        if request.headers.get("accept", "").startswith("text/html"):
            body = "ok" if ready else "no: la base de datos no responde"
            status = 200 if ready else 503
            return HTMLResponse(body, status_code=status)
        return {"status": "ok" if ready else "error", "database": ready}

    @app.get("/api/v1/{path:path}", include_in_schema=False, response_model=None)
    async def no_rest(path: str) -> HTMLResponse:
        # El plan decidió no tener REST. Esto responde 404 explícito, no un 405 confuso.
        return HTMLResponse(
            f"<h1>404</h1><p>OpenSupport no expone API REST: <code>{path}</code> no existe. "
            "Las máquinas usan MCP en <code>/mcp</code>.</p>",
            status_code=404,
        )

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


app = create_app()
