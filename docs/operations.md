# Operaciones

Deploy, backups, migraciones y qué hacer cuando algo falla.

## Local

```bash
uv run opensupport init --demo        # DB + usuario + token + bloque de conexión
uv run opensupport serve              # 127.0.0.1:8000
```

Variables de entorno (prefijo `OPENSUPPORT_`):

| Variable | Default | Para qué |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/opensupport.db` | Ruta del archivo |
| `SECRET_KEY` | valor de desarrollo | Firma de la cookie de sesión. **Cambiala en producción.** |
| `MCP_REQUIRED_TOKENS` | `true` | Si es `false`, `/mcp` queda abierto. No lo hagas en producción. |

## Docker

```bash
docker build -t opensupport .
docker run -p 8000:8000 \
  -e OPENSUPPORT_SECRET_KEY=<una larga, generada> \
  -v opensupport_data:/data opensupport
```

Dos cosas que el Dockerfile resuelve y que son fáciles de romper al tocarlo:

1. **`uv sync` va antes de copiar `app/`, con `--no-install-project`.** Si se instala
   el proyecto antes de que exista el código, el paquete se instala vacío y el
   contenedor muere con `ModuleNotFoundError: No module named 'app'`.
2. **El `CMD` llama al binario del venv directamente**, no `uv run`. `uv` quiere
   escribir en su cache del `$HOME` y el usuario no-root no puede.

## Railway

```toml
# railway.toml ya está en el repo
[deploy]
startCommand = "opensupport serve --host 0.0.0.0 --port $PORT"
healthcheckPath = "/readyz"
numReplicas = 1          # SQLite exige 1. No lo subas.
```

Vars obligatorias:

```
OPENSUPPORT_DATABASE_URL=sqlite:////data/opensupport.db
OPENSUPPORT_SECRET_KEY=<generada>
```

El volumen va montado en `/data`. **El plan Free incluye 0.5 GB de volumen**,
que para un board de equipo alcanza de sobra (500 tickets ≈ 2-5 MB).

### Tres trampas de Railway, verificadas contra la doc

1. **Los volúmenes del Trial se borran** 30 días después de que vencen los
   créditos. Si quedás en el Trial y no mirás, perdés la base.
   → Antes de que expire: `opensupport export --format markdown > board.md`
   y commit. Después subí a **Hobby** ($5/mes, incluye $5 de uso).
2. **Free duerme a los 5 minutos.** El primer request paga el arranque.
   Los clientes MCP reconectan solos; Hobby da 30 min de sleep.
3. **SQLite sobre volumen de red.** Los volúmenes de Railway están montados
   sobre NFS, y WAL necesita shared memory, que NFS no siempre da.
   → `app/db/base.py` intenta WAL y cae a `journal_mode=DELETE` con un warning
   en el log si no puede. Verificá con `/readyz` después del primer deploy.

## Conectar un cliente MCP

El server expone **streamable HTTP** en `/mcp` (también acepta `/mcp/`, sin redirect).
Probado con JSON-RPC crudo y con `@modelcontextprotocol/inspector`, que es la
implementación de referencia del protocolo:

```bash
uv run opensupport serve
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp \
  --transport streamable-http --method tools/list \
  --header "Authorization: Bearer os_..."
```

Si un cliente no conecta, revisá en este orden:

1. **Sin slash.** `/mcp` y `/mcp/` funcionan, pero si un cliente muestra la URL con
   slash y le da un 307, es un cliente que no sigue redirects en POST.
2. **401** → token inexistente, revocado, o el header no viaja. Va como
   `Authorization: Bearer os_...`.
3. **Sin `Accept: text/event-stream`** → el transporte responde 406. Algunos clientes
   lonegocian mal; FastMCP y el inspector lo hacen bien.

## Backups

```bash
opensupport backup                      # → data/backups/opensupport-<stamp>.db
opensupport restore data/backups/<file>.db
```

Usa la **API `sqlite3` backup**, no `cp`: con WAL, copiar el archivo a mano
puede dar una base inconsistente. Guarda las últimas 10 y borra las anteriores.

**Automatizá esto.** Si no, no tenés backup:

```toml
# railway.toml, con el plugin de cron
[deploy]
restartPolicyType = "ON_FAILURE"
```

```bash
# o un cron en el mismo contenedor
0 3 * * * opensupport backup
```

## Calibrar el linter con datos

```bash
uv run opensupport lint-stats
```

Los umbrales del linter (12 pasos, 20 criterios, 15 items de scope) son un punto
de partida, no una verdad. Cada `create_ticket` rechazado escribe un
`ticket_events` con `kind='lint_failed'`; ese comando los agrupa.

**Cómo leerlo:** la regla que domina dice dónde está el problema real.

| Lo que domina | Qué hacer |
|---|---|
| `*_TOO_MANY`, `*_TOO_BIG` | El umbral quedó bajo. Subilo en `app/tickets/schema.py`. |
| `*_VAGUE` | La redacción de los prompts o del skill mejoró, no el número. |
| `*_MISSING` | El agente no entendió qué pedir. Revisá el skill. |
| Casi nada | El linter no está molestando. Dejalo así. |

Esperá **dos semanas de uso real** antes de tocar un número.

## Migraciones

```bash
uv run alembic upgrade head      # aplicar
uv run alembic downgrade base    # revertir todo (solo en dev)
```

`opensupport serve` y `opensupport init` aplican `upgrade head` solos al
arrancar. Si una migración falla, la app no levanta: es intencional.

Checklist al agregar una migración:

1. `upgrade()` y `downgrade()` completos y simétricos.
2. Actualizá `app/domain/models.py` en el mismo commit.
3. `tests/test_migrations.py` verifica que el modelo y la migración coincidan.
4. Probá `downgrade` **y** `upgrade` de nuevo en una DB de prueba.

## Health checks

| Ruta | Qué responde |
|---|---|
| `/healthz` | El proceso está vivo. Always 200 mientras el proceso exista. |
| `/readyz` | DB conecta + `PRAGMA integrity_check` + journal y tamaño. 503 si algo falla. Es el que usa Railway. |

## Troubleshooting

### El agente dice `Rate limit: superaste el límite`

El token hizo más escrituras que las permitidas en un minuto (120 por default).
Esperá el tiempo que dice el error, o subilo con
`OPENSUPPORT_RATE_LIMIT_WRITES_PER_MIN`. Si aparece en uso normal, lo más
probable es un agente en loop: revisá si está reintentando un `create_ticket`
que siempre falla.

### El agente dice `E_PROJECT_OUT_OF_SCOPE`

El token no existe, está revocado, o el header no viaja.
- Verificá que sea `Authorization: Bearer os_...`, sin comillas.
- En `/settings/tokens` comprobá que no esté revocado.
- El token se muestra **una sola vez**. Si lo perdiste, creá otro.

El token no tiene ese proyecto en su `project_scope`. El `fix` de la violación
lista los permitidos. Se cambia desde `/settings/tokens`.

### `E_CONFLICT`

Otro agente cambió el ticket entre que lo leíste y que lo escribiste.
Releelo con `get_ticket` y reintentá pasando el `updated_at` nuevo.

### El linter rechaza un ticket que parece bueno

Leé el `message` y el `fix` de la violación: dicen exactamente qué cambiar.
Los warnings (`W_*`) no bloquean; si los ves, el ticket se creó igual.

Si te parece que la regla está mal, el problema puede ser el umbral: están en
`app/tickets/schema.py` como constantes (`MAX_STEPS`, `MAX_ACCEPTANCE`,
`MAX_SCOPE_ITEMS`). Se ajustan ahí y en los tests que los verifican.

### La búsqueda no encuentra nada

FTS5 usa `tokenize='unicode61'`. No distingue acentos: "verificacion" encuentra
"verificación". Para búsquedas con acentos exactos usá `search_tickets` con
términos simples, o filtrá por tag con `list_tickets(tag=...)`.

### El servidor no levanta

```bash
uv run opensupport serve --log-level debug
```

Causas frecuentes: migraciones pendientes (mira el error de Alembic), puerto
ocupado, o `OPENSUPPORT_DATABASE_URL` mal formada.

### `RuntimeError: Task group is not initialized`

El lifespan del MCP app tiene que correr. `app/main.py` lo envuelve con
`async with mcp_app.lifespan(app)`. Si tocás el montaje de `/mcp`, mantenelo.

## Límites conocidos

- **Un solo worker.** SQLite serializa escritores. Es una decisión, no un bug.
- **Sin escala horizontal.** Si hacés falta, el camino es Postgres o libSQL;
  el acceso a datos ya está aislado en `tickets/repository.py`.
- **`projects.conventions` está sin uso.** Quedó de una decisión que se
  revirtió (scope en vez de linter por convenciones). Está reservada.
- **El export a GitHub es de un solo sentido.** No hay webhook de vuelta.