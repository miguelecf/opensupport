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
| `/readyz` | La DB conecta. 503 si no. Es el que usa Railway. |

## Troubleshooting

### El agente dice `401 Unauthorized`

El token no existe, está revocado, o el header no viaja.
- Verificá que sea `Authorization: Bearer os_...`, sin comillas.
- En `/settings/tokens` comprobá que no esté revocado.
- El token se muestra **una sola vez**. Si lo perdiste, creá otro.

### El agente dice `E_PROJECT_OUT_OF_SCOPE`

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