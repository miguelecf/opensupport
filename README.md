# OpenSupport

Board de tickets **agent-native**. Tus agentes de código recorren sus repos y
dejan tickets que un humano puede ejecutar, revisar y cerrar.

**El servidor no tiene LLM.** No redacta, no resume, no decide. Valida con reglas
deterministas y devuelve al agente exactamente qué corregir. Por eso cuesta $0
correrlo y por eso los tickets que salen son consistentes entre agentes distintos.

## Arranque en un comando

```bash
uv sync
uv run opensupport init --demo
uv run opensupport serve
```

`init --demo` crea la base, un usuario, un proyecto y un token, e imprime el
bloque de conexión para Claude Code y Codex.

Después, en el chat de tu agente:

> Conectate a opensupport, leé `opensupport://skill/ticket-authoring`,
> recorré este repo y creá los tickets que falten.

Eso es todo. Sin archivos que copiar, sin variables de entorno que descubrir.

## Cómo se usa

```
resources/read opensupport://skill/ticket-authoring   ← el contrato
search_tickets("lo que busco")                        ← ¿ya existe?
lint_ticket(borrador)                                 ← no persiste nada
  └─ corregir hasta ok: true
create_ticket(borrador)                               ← recién acá persiste
```

Cuando implementás:

```
set_status(code, "in_progress")
close_ticket(code, pr="https://github.com/...", commands=["uv run pytest"])
```

## La web

`http://127.0.0.1:8000` — login con un token. Board, detalle, sesiones, tokens y
proyectos. HTML + HTMX, sin build step.

No hay API REST. Las máquinas usan MCP (`/mcp`); la web es una interfaz para
humanos, no un contrato programable. `/api/v1/*` responde 404 a propósito.

## Sesiones de planificación

Cuando varias personas y varios agentes planifican el mismo tema:

```text
start_session(project="mi-tesis", title="Auth: migrar a JWT", goal="...")
join_session("PLN-001")     ← devuelve objetivo, participantes y tickets ya escritos
create_tickets_batch([...], session="PLN-001")
```

El `join_session` es la razón de que la sesión sea una entidad y no una etiqueta:
el agente **lee lo que ya se decidió antes de aportar**.

## Export a GitHub Issues

```bash
uv run opensupport export --format gh | gh issue create -F -
uv run opensupport export --format markdown > board.md   # para commitear
```

Es export, no sync. OpenSupport es la fuente de verdad.

## Backups

```bash
uv run opensupport backup            # usa la API de sqlite3: atómico con WAL
uv run opensupport restore data/backups/opensupport-20261008-120000.db
```

## Docs

| Archivo | Qué hay |
|---|---|
| `AGENTS.md` | Reglas para trabajar en el repo. Normativo. |
| `CONTEXT.md` | Glosario de dominio. |
| `docs/architecture.md` | Por qué cada decisión. |
| `docs/ticket-contract.md` | El contrato completo y las violaciones. |
| `docs/operations.md` | Deploy, backups, troubleshooting. |

## Desarrollo

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```

## Stack

FastAPI · SQLite (WAL + FTS5) · SQLAlchemy 2.0 · Alembic · FastMCP 3 ·
Jinja2 + HTMX · uv · ruff · mypy --strict