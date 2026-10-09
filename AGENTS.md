# AGENTS.md

Contrato normativo para trabajar en OpenSupport. Se lee entero, siempre.
Lo que está acá es normativo: si lo violás, el código está mal.
Lo que es explicación (por qué) vive en `docs/`.

## Qué es

OpenSupport es un board de tickets para equipos donde los **agentes de código**
hacen el análisis. El agente recorre su repo local y postea tickets validados
contra este servidor por MCP. Un humano los lee y los triagea en la web.

**Qué NO es, y no va a ser:**
- No tiene LLM. Ninguna llamada de red a un modelo. El server no piensa: *exige*.
- No lee repositorios, no ejecuta `git`, no corre código de nadie.
- No tiene API REST. Las máquinas hablan **solo MCP**. `/api/v1/*` da 404 a propósito.
- No es multi-tenant. Es para un equipo.

## Arquitectura

```
agente ──MCP/HTTP+Bearer os_xxx──▶ /mcp  (FastMCP)
humano ──HTML+cookie────────────▶ /     (FastAPI + Jinja2 + HTMX)
                                       │
                                  service layer   ← la única fuente de verdad
                                       │
                                  SQLAlchemy + SQLite (WAL, FTS5)
```

## Fronteras (qué módulo puede importar qué)

| Módulo | Puede importar | NO puede |
|---|---|---|
| `api/mcp/`, `api/web/` | `service`, `repository`, `schema` | importar reglas de negocio propias |
| `tickets/service.py` | `repository`, `linter`, `schema`, `domain` | conocer MCP o HTTP |
| `tickets/linter.py` | `schema` | tocar DB, FastAPI o HTTP |
| `tickets/repository.py` | `domain`, SQLAlchemy | decidir nada de negocio |

**La regla que más se rompe:** lógica de negocio en `api/mcp/tools.py` o en
`api/web/views.py`. Va en `service.py`. Es la causa #1 de que un sistema con dos
superficies termine con dos versiones de la verdad.

## Comandos

```bash
uv sync                      # instalar
uv run pytest                # tests
uv run pytest -q tests/test_linter.py
uv run ruff check .          # lint
uv run ruff format .         # formato
uv run mypy app              # tipos, strict
uv run opensupport init --demo   # DB + token + bloque de conexión
uv run opensupport serve     # levantar en 127.0.0.1:8000
uv run opensupport backup    # backup atómico con la API de sqlite3
uv run opensupport export --format gh
```

## Reglas inviolables

1. **TDD en el dominio.** `linter/`, `transitions/`, `links/` y `service.py` se
   escriben con el test primero. `api/mcp/` y `api/web/` llevan su test junto al código.
2. **`db_call` / `_run_in_thread` para todo acceso a DB desde async.** Nunca
   pegarle a la sesión desde una corrutina: bloquea el event loop.
3. **Un solo worker.** SQLite serializa escritores. `--workers N` rompe el modelo.
   El rate limit (`app/ratelimit.py`) es un dict en memoria y depende de esto.
4. **Nunca loguear credenciales.** `app/logging_setup.py` redacta `Authorization`,
   `Set-Cookie`, `Cookie` y los tokens `os_*`. Ojo: el filtro va en los **handlers**,
   no en los loggers — los filtros de logger no se heredan a los hijos.
5. **Cero `print`** (ruff T201). `logging.getLogger(__name__)`.
6. **`mypy --strict` limpio.** Los `# type: ignore` se justifican.
7. **Autoescape de Jinja siempre on.** Nunca `autoescape=False`.
8. **Ninguna dependencia nueva sin justificar.** `pyproject.toml` es corto a propósito.

## Cómo agregar una regla del linter

1. Escribí el test que falla en `tests/test_linter.py` (una fila + un borde).
2. Agregá la función `_check_*` en `tickets/linter.py` y llamala desde `lint()`.
3. Agregá el código a `_violation_table()` en `api/mcp/server.py`, con `message` y `fix`.
4. Agregá el caso al mini skill `app/skill/ticket-authoring.md`.
5. Corré los tests. Si `make_valid_draft()` falla, actualizá la factory — **no** la regla.

## Cómo agregar una tool MCP

1. Definí la lógica en `tickets/service.py` o `sessions/service.py` primero.
2. Agregá el `@mcp.tool` en `api/mcp/server.py` delegando en el service.
3. Todo acceso a DB va dentro de un closure `_run(db)` ejecutado con `_run_in_thread`.
4. **Si es un draft, debe aceptar exactamente los mismos campos que `create_ticket`.**
   Es el contrato entero: si `lint_ticket` rechaza un campo que `create_ticket`
   acepta, el loop del agente se rompe.
5. Agregá el test en `tests/test_integration.py` (contra el servidor real, no mocks).

## Seguridad implementada

| Propiedad | Dónde | Cómo se rompe |
|---|---|---|
| CSRF en toda mutación web | `api/web/views.py:require_csrf` | Header `X-CSRF-Token` != cookie. Base lo inyecta por htmx. |
| Rate limit por token | `ratelimit.py`, dentro de `require_write`/`require_read` | Token bucket 120/min escrituras, 600/min lecturas |
| Redacción de credenciales | `logging_setup.py` | Filtro en los handlers; también tacha `os_*` en texto libre |
| Aislamiento de escritura | `tickets/service.py:_can_mutate` | `E_NOT_YOUR_TICKET` si no sos owner, autor ni admin |
| Alcance de proyecto | `api_tokens.project_scope` | `E_PROJECT_OUT_OF_SCOPE` con la lista de permitidos |
| `done` con evidencia | `tickets/service.py:close_ticket` | Sin PR/commit o sin comando, no cierra |

Si agregás una tool que escriba, no te saltees `require_write()`: ahí viven el scope y el rate limit.

## Anti-patrones

- Lógica de negocio en `api/mcp/tools.py` o `api/web/views.py` → va en `service.py`.
- SQL crudo fuera de `repository.py`.
- Un closure interno llamado `_run` cuando ya existe el helper `_run_in_thread`:
  tapa el helper y rompe el acceso a la sesión.
- `TemplateResponse("x.html", {...})` con la firma vieja. Es
  `TemplateResponse(request, "x.html", {...})`.
- `{{ algo.created_at[:16] }}` en un template: los datetime no son subscriptables.
  Usá `.strftime('%d/%m %H:%M')`.

## Glosario

- **draft** — el objeto que manda el agente, antes de existir como ticket.
- **violation** — `{code, field, message, fix}`. `E_*` bloquea, `W_*` avisa.
- **owner** — la persona que responde por el ticket. Cambiable.
- **autor** — el agente que escribió el ticket. Inmutable.
- **link** — relación entre tickets: `blocks`, `split_from`, `relates_to`.
- **code** — `OS-014` para tickets, `PLN-002` para sesiones. Secuencial por proyecto.
- **scope** — proyectos que un token puede escribir. Vacío = todos.
- **review** — veredicto sobre un ticket. No cambia el status.

## Idioma

Identificadores, tablas y columnas en **inglés**. Todo lo que lee una persona o
un agente —mensajes del linter, validaciones, UI, skill, docs— en **español**.

## Más

- `docs/architecture.md` — por qué cada decisión.
- `docs/ticket-contract.md` — el contrato completo y las violaciones.
- `docs/operations.md` — deploy, backups, migraciones, troubleshooting.
- `CONTEXT.md` — glosario de dominio extendido.