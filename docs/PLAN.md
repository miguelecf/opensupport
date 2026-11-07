# OpenSupport — plan de implementación

> **Documento histórico.** Describe el plan original de construcción, no el
> estado actual del producto. Para lo que el contrato es hoy, mirá
> `docs/ticket-contract.md`, `app/skill/ticket-authoring.md` y
> `app/skill/support-workflow.md`. Las decisiones que cambiaron después están
> en `docs/architecture.md` (0013 a 0016).

## Summary

**Objetivo.** Construir `OpenSupport`: un servidor pequeño, single-tenant, *agent-native* para que un equipo de programadores converja tickets de trabajo de alta calidad generados por sus propios agentes de código. Un agente (Claude Code, Codex, OpenCode, cualquier cliente MCP) recorre **su** proyecto local, y en vez de dejar el análisis en un chat que se pierde, postea tickets estructurados, validados y versionados contra `OpenSupport` vía **MCP**. La app **no tiene LLM**: valida de forma determinista, guarda, y devuelve al agente errores accionables. Un humano abre la web (FastAPI + Jinja2 + HTMX) para leer, triagear y comentar.

**Decisiones clave ya resueltas con vos:**

| Decisión | Resolución |
|---|---|
| ¿Quién redacta el ticket? | El agente cliente, en su máquina. El server **nunca** llama a un LLM. Costo de AI = $0 para la app. |
| ¿El server toca el código? | **No.** Agnóstico al repo: no lee archivos, no clona, no conoce paths. Solo recibe tickets. |
| Entidades | **11 tablas**: `users`, `agents`, `api_tokens`, `projects`, `sessions`, `session_participants`, `tickets`, `ticket_links`, `ticket_reviews`, `comments`, `ticket_events`. **Doble atribución**: `author_agent_id` (quién lo escribió, inmutable) + `owner_user_id` (quién responde, reasignable). |
| Sesiones | Contexto compartido: `join_session` devuelve objetivo, participantes y tickets ya escritos. Una por tema dentro de un proyecto. Personas y agentes participan con roles. |
| Revisión | Eje **ortogonal** al status: `approved` / `changes_requested` con nota. Informa, no bloquea. Cualquiera del equipo revisa, sin ser participante. Cada veredicto guarda el `content_hash` que estaba mirando. |
| Visibilidad | **El equipo lee todo**; la escritura es del `owner_user_id`, del agente autor, o de un admin. Los agentes solo escriben en los proyectos de su `project_scope`. |
| API para máquinas | **Solo MCP.** Sin REST. La web es HTML+HTMX sobre el service layer, no un contrato. |
| Estado inicial | `ready` — ejecutable desde el minuto uno. `open` es backlog, decisión humana. |
| Umbrales del linter | 12 pasos / 20 criterios / 15 items de scope. Permisivos a propósito, con telemetría `lint_failed` para recalibrar. |
| Deploy | **Local primero** (integrado con Orca). Railway después — ojo: borra volúmenes del trial a los 30 días. |
| Nomenclatura | **`OpenSupport`** (decidido). El directorio del repo sigue llamado `agent-support`: renombrarlo en disco es un paso operativo, no de diseño. |
| Código y docs | Identificadores en inglés, contenido en español. `AGENTS.md` normativo (~150 líneas) + `docs/` + `CONTEXT.md`. |
| Tests | TDD estricto en `linter/`, `transitions/`, `links/`, `service/`. Integración en MCP y web. |
| GitHub Issues | Export `.md` compatible con `gh issue create`, en un sentido. OpenSupport es la fuente de verdad. |
| Stack | FastAPI + SQLite + Jinja2 + HTMX + FastMCP, alineado con las convenciones de FinancePet. |

**El insight de producto (por qué esto es "AI native" y no un clon de Trello).** El valor no está en el board: está en el **contrato**. `OpenSupport` es, en esencia, un *linter de tickets para agentes* con almacenamiento persistente y un loop de cierre. Un board humano normal acepta cualquier texto; este rechaza el ticket mal formado y le devuelve al agente una violación con código y con la instrucción exacta de cómo arreglarlo (`E_ACCEPTANCE_VAGUE` → "cada criterio debe ser verificable; reformulá 'funciona bien' como 'POST /login con credenciales válidas devuelve 200'"). Eso es lo que hace que un agente que recorre un repo producir trabajo usable sin supervisión humana. El server no piensa — *exige*.

---

## Paso 0 (bloqueante): instalar el skill `no-ai-slop` global

Antes de escribir una línea de OpenSupport. **No es parte del producto** — es una corrección de mi comportamiento, y aplica a todos los proyectos, no solo a este.

### Por qué va primero

En este planning escribí dos cuestionarios y uno salió en portugués, en una conversación íntegramente en español, dos veces. El usuario lo frenaró: *"eso no te puede volver a pasar en lo absoluto"*. Es un fallo de atención, no de conocimiento, y por eso la defensa es explícita y global, no una nota en `AGENTS.md` de este repo.

### Convención detectada en su máquina (verificada)

`~/.agents/skills/` es el directorio **canónico**; el resto son symlinks. Confirmado resolviendo los existentes:

```
~/.claude/skills/herdr          -> ../../.agents/skills/herdr
~/.claude/skills/orca-cli       -> ../../.agents/skills/orca-cli
~/.claude/skills/playwright-cli -> /Users/miguelcaraballo/.agents/skills/playwright-cli
```

Los 9 directorios donde un cliente busca skills existen y son reales: `~/.pi/agent/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, `~/.claude/skills`, `~/.gemini/skills`, `~/.codex/skills`, `~/.codeium/windsurf/skills`, `~/.kiro/skills`, `~/.hermes/skills`.

### Pasos exactos

1. Crear el canónico: `~/.agents/skills/no-ai-slop/SKILL.md`
   (nombre de archivo `SKILL.md` porque es lo que lee el registry y todos los clientes; no `no-ai-slop.md`).
   Contenido = el texto que pasó el usuario, **verbatim**, con el frontmatter ya cerrado.
   Excluir la línea final *"llamado no-ai-slop.md ... Lo hagas de forma global no solo en este proyecto"*, que es instrucción para mí, no contenido del skill.

2. Symlinks relativos (la profundidad varía según el path, hay que respectarla):

   | Destino | Target relativo |
   |---|---|
   | `~/.claude/skills/no-ai-slop` | `../../.agents/skills/no-ai-slop` |
   | `~/.codex/skills/no-ai-slop` | `../../.agents/skills/no-ai-slop` |
   | `~/.gemini/skills/no-ai-slop` | `../../.agents/skills/no-ai-slop` |
   | `~/.hermes/skills/no-ai-slop` | `../../.agents/skills/no-ai-slop` |
   | `~/.config/opencode/skills/no-ai-slop` | `../../../.agents/skills/no-ai-slop` |
   | `~/.pi/agent/skills/no-ai-slop` | `../../../.agents/skills/no-ai-slop` |
   | `~/.codeium/windsurf/skills/no-ai-slop` | `../../../../.agents/skills/no-ai-slop` |
   | `~/.kiro/skills/no-ai-slop` | `../../../../.agents/skills/no-ai-slop` |

3. **No hay paso 3.** No hay índice que regenerar: `gentle-ai` fue eliminado del entorno y sus 16 directorios `.atl/` (caché derivada, nunca trackeada en git) fueron enviados a la papelera. La carga de skills no dependía de ese índice — los clientes resuelven por path. No hay `gentle-ai skill-registry refresh`.

4. Verificar: los 8 symlinks resuelven al mismo inode que el canónico, y `SKILL.md` es legible desde los 9 directorios.

### Un hueco en el skill que conviene saber (decisión del usuario)

Tal como está escrito, el skill declara *"Work in the draft's language"* y sus listas de palabras son **solo inglés y español**. **El error que motivó instalarlo fue en portugués**, un idioma que las listas no cubren: el skill, tal cual, no lo habría detectado.

Diff propuesto, de una línea, si quiere cerrarlo:

```diff
-description: Edit or audit drafts in English or Spanish to remove AI writing patterns while keeping the writer's voice.
+description: Edit or audit drafts in any language to remove AI writing patterns while keeping the writer's voice. Word lists below cover English and Spanish; apply the principles to any language.
```

**Default: se instala verbatim, sin el diff.** Es texto del usuario y la decisión es suya; el cambio se aplica solo si lo pide.

---

### Day-1 DX: el agente se conecta en un comando

El objetivo del proyecto es que sea **lo más fácil que sea AI native**. Concretamente: del arranque a un ticket creado, con un humano copiando un bloque de texto y sin leer documentación.

Un solo comando deja todo listo:

```bash
opensupport init --demo
```

Imprime, listo para copiar:

```
Base de datos: /Users/tu/workspace/agent-support/data/opensupport.db

── Claude Code ──
claude mcp add --transport http opensupport http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer os_demo123"

── Codex ──
export OPENSUPPORT_TOKEN=os_demo123
# en ~/.codex/config.toml:
[mcp_servers.opensupport]
url = "http://127.0.0.1:8000/mcp"
bearer_token_env_var = "OPENSUPPORT_TOKEN"

Token: os_demo123   (solo se muestra una vez)
```

Y después, en el chat del agente:

> Conectate a opensupport, leé opensupport://skill/ticket-authoring, recorré este repo y creá los tickets que falten.

Eso es todo. Sin configuración previa, sin variables de entorno que descubrir, sin leer un solo archivo de documentación.

**Las tres decisiones que hacen que eso funcione, y que no se pueden agregar después:**

1. **El skill es un MCP resource, no un archivo.** El agente pide `resources/read` y lo tiene. No hay instalación, ni copia, ni un `SKILL.md` que alguien tenga que acordarse de postear en cada repo.
2. **`lint_ticket` existe y no persiste.** El agente puede iterar sin dejar basura. Sin esto, probar el contrato significa crear tickets de prueba a propósito.
3. **`create_tickets_batch` es parcial.** Un lote de 20 con 2 malos devuelve 18 creados y 2 errores accionables. El agente no pierde la mitad del trabajo por un typo en un campo.

`get_schema` y `opensupport://schema/violations` dejan que el agente se autoverifique antes de escribir. Y como la mayoría de los clientes llaman `resources/list` al conectarse, si `skill/ticket-authoring` y `schema/ticket` aparecen ahí, el agente tiene su contrato sin que nadie se lo pida.

**Criterio de aceptación 21:** `opensupport init --demo` + pegar el bloque en un cliente MCP real + el prompt de arriba produce al menos un ticket válido, sin intervención humana y sin leer documentación.

---

### Goal

Que un agente pueda, con solo credenciales MCP (sin login web, sin acceso a la DB):

1. Leer su propio contrato de redacción (`resources/read`) sin tener nada instalado.
2. Lintear un borrador antes de postearlo, iterar hasta `ok: true`.
3. Crear tickets detallados, idempotentes, con evidencia en `path:line`.
4. Reportar progreso y cierre cuando los implementa.

### In scope (v1)

- Servidor MCP por HTTP streamable montado en la misma app FastAPI (`/mcp`).
- Auth por bearer token con scopes, verificado contra SQLite.
- Contrato de ticket estricto + linter determinista con códigos de violación.
- Escritura idempotente (`external_key`), búsqueda FTS5, máquina de estados.
- Web mínima en HTMX para humano: board, detalle, comentarios, gestión de tokens.
| CLI de bootstrap (crear usuario, crear token, backup, export a `gh issue create`), con `init --demo` que imprime el bloque de conexión listo para pegar. |
- Docker + config Railway con volumen persistente.
- `AGENTS.md` normativo y corto + `docs/` con el detalle.
- Suite de tests con TDD en el dominio + umbrales de calidad (ruff, mypy strict).

### Non-goals (explícitos)

- **No LLM en el server.** Ni para redactar, ni para resumir, ni para triagear.
- **No** lectura de repos, no `git`, no sandbox de código, no ejecución de nada del lado del server.
- **No REST / JSON API.** Las máquinas hablan **solo MCP**. La web es HTML para humanos y no es un contrato programable (ver "Por qué no hay REST").
- **No** kanban drag-and-drop, no realtime multiusuario, no notificaciones.
- **No** roles de usuario: la autorización vive en los scopes de los tokens (ver modelo de entidades).
- **No** multi-tenant real ni billing. Multi-*user* sí, pero un solo board compartido por el equipo.
- **No** escala horizontal. SQLite + un solo worker es la decisión, no una limitación temporal (ver riesgos).
- **No** escritura bidireccional a GitHub Issues. Hay export a markdown compatible con `gh issue create`, y nada más.

---

## Por qué no hay REST (y cómo queda la web de humanos)

Tu pregunta: *si no hay API, ¿cómo hago el front de humanos?* La distinción es entre **API** y **interfaz**:

- Una **API** es un contrato para máquinas: JSON versionado, documentado, estable, autenticado por token, pensado para integrarse. Eso es lo que estamos eliminando.
- Una **interfaz** es lo que el browser necesita para dibujar píxeles. **Es HTTP por naturaleza** — no hay forma de tener UI sin HTTP. Pero no es un contrato: no se documenta, no se versiona, no se testea como API, se autentica por cookie de sesión, y devuelve HTML, no JSON.

**La disciplina concreta en el código:**

```
agente ──MCP/HTTP+JSON+bearer token──▶ ┌──────────────────┐
                                       │  service layer   │ ◀── MCP tools

[... output omitted ...]


```
OpenSupport/
├── .git/                    # 1 commit: "Initial commit"
└── README.md                # una línea: "# OpenSupport"
```

Estado verificado: `git log` = un solo commit, `git status` = limpio, sin `AGENTS.md`, sin estructura de aplicación. (El directorio `.atl/` que tenía al inicio fue eliminado: caché derivada de `gentle-ai`, que ya no forma parte del entorno.)

**Contexto heredado de sesiones previas** (memoria del proyecto): el usuario pidió explícitamente repo **local, integrado en Orca, sin publicar en GitHub ni remoto**. Eso se mantiene: nada de push, deploy ni credenciales en esta etapa.

**Convenciones a reutilizar** (verificadas en `/Users/miguelcaraballo/workspace/financepet`):

## Current implementation

El repo está **vacío**. No hay código que preservar ni que romper.

```
OpenSupport/
├── .git/                    # 1 commit: "Initial commit"
└── README.md                # una línea: "# OpenSupport"
```

Estado verificado: `git log` = un solo commit, `git status` = limpio, sin `AGENTS.md`, sin estructura de aplicación.

**Contexto heredado de sesiones previas** (memoria del proyecto): el usuario pidió explícitamente repo **local, integrado en Orca, sin publicar en GitHub ni remoto**. Eso se mantiene: nada de push, deploy ni credenciales en esta etapa.

**Convenciones a reutilizar** (verificadas en `/Users/miguelcaraballo/workspace/financepet`):
- `uv` + `pyproject.toml` con `hatchling`, `[dependency-groups] dev`.
- Tooling: `ruff` (select E,W,F,I,B,C4,UP,ARG001,T201), `mypy --strict`, `coverage`.
- Layout modular: `app/modules/<domain>/{router,service,models,schemas,dependencies}.py` con `app/db/migrations` vía Alembic.
- Health check de readiness que valida acceso a DB y head de migraciones.
- Patrón de tests por módulo (`test_service.py`, `test_composition.py`).

Herramientas locales disponibles: Python 3.13.4, `uv` 0.12.18, Docker, sqlite3, node 22.

---

## Feedback: 6 cosas que quiero decirte antes de escribir una línea

1. **Tu instincto de "sin LLM en la app" es correcto y es la mejor decisión del proyecto.** Te ahorra el costo variable, la latencia, el vendor lock-in y —lo más importante— te deja subir la plataforma a cualquier free/cheap tier sin discutir. Un ticket generado por tu Claude local con el contexto completo del repo va a ser *mejor* que uno generado por un LLM server-side que solo ve lo que le mandaste.

2. **El riesgo real no es el board, es el contrato.** El 80% del esfuerzo de diseño debería ir al schema del ticket y al linter, no a la UI. Si el linter es bueno, los agentes producen trabajo bueno y la UI puede ser fea sin costo. Si el linter es flojo, ni una UI linda salva el resultado.

3. **El idempotency key no es un detalle, es la diferencia entre "usable" y "molesto".** Un agente que recorre un repo y se corta a mitad de camino va a reintentar. Sin `external_key` tenés el mismo ticket 4 veces. Con `external_key` (una clave estable tipo `mi-tesis:auth-module:extraer-jwt`) el reintento devuelve el ticket existente. Esto también te da deduplicación semántica entre agentes distintos.

4. **Cuidado con el tamaño del ticket.** Un ticket que dice "refactorizar el módulo de auth" no es un ticket, es un deseo. Impongo un límite duro (`E_SCOPE_TOO_BIG`) y exijo split. Es la disciplina tracer-bullet que ya tenés en tu skill `to-tickets`, pero convertido en *invariante del sistema* en vez de una instrucción en un prompt que el modelo puede ignorar.

5. **Railway tiene dos trampas concretas** que documenté abajo (borrado de volúmenes del trial a los 30 días, y SQLite sobre volumen de red). Ninguna es dealbreaker, pero hay que enterarse antes de confiarle el backup de tu tesis.

6. **El "mini skill" no debería ser un archivo en un repo que el agente no tiene.** La mayoría de los agentes no van a tener tu `SKILL.md`. Por eso lo voy a exponer como **MCP resource** (`opensupport://skill/ticket-authoring`): cualquier cliente MCP lo lee con `resources/read` y tiene el contrato en contexto sin instalar nada. Ese detalle solo es lo que hace la app realmente agent-friendly.

---

## Arquitectura propuesta

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Máquina del teammate            (el server NUNCA ve esto)                │
│                                                                           │
│   ┌──────────────────┐    lee repo    ┌──────────────┐                     │
│   │  Claude Code     │───────────────▶│  contexto    │                     │
│   │  Codex           │                │  del proyecto│                    │
│   │  OpenCode        │◀───────────────┤              │                     │
│   └────────┬─────────┘   MCP tools    └──────────────┘                     │
│            │                                                               │
└────────────┼───────────────────────────────────────────────────────────────┘
             │  HTTPS  Authorization: Bearer os_xxx...
             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  OpenSupport  (una app, un puerto)                                       │
│                                                                            │
│   ┌───────────────────────────────┐   ┌──────────────────────────────────┐  │
│   │  /mcp  (FastMCP, streamable)  │   │  /  (FastAPI + Jinja2 + HTMX)    │  │
│   │                               │   │                                  │  │
│   │  resources/                  │   │  board · detalle · comentarios   │  │
│   │   skill · schema · snapshot  │   │  · gestión de tokens             │  │
│   │  tools/                      │   │                                  │  │
│   │   lint · create · update     │   │  (humano, sesión por cookie)     │  │
│   │   status · comment · close   │   │                                  │  │
│   │   list · search · get        │   │                                  │  │
│   └───────────────┬───────────────┘   └──────────────────────────────────┘  │
│                   │                                                         │
│         ┌─────────▼──────────┐        ┌────────────────────────────────┐   │
│         │  domain/            │◀──────▶│  tickets/linter.py             │   │
│         │  ticket service     │        │  reglas deterministas          │   │
│         │  state machine      │        │  {code, field, message, fix}   │   │
│         └─────────┬──────────┘        └────────────────────────────────┘   │
│                   │                                                         │
│         ┌─────────▼──────────────────────────────────────────────────┐     │
│         │  db/  SQLAlchemy 2.0 + Alembic                             │     │
│         │  SQLite WAL · FTS5 · /data/opensupport.db               │     │
│         └────────────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Flujo end-to-end: de "recorré el repo" a ticket cerrado

```
1.  humano:  "recorré el repo y armo los tickets"
2.  agente:  resources/read opensupport://skill/ticket-authoring
            → recibe el contrato completo (reglas, ejemplos, anti-patrones)
3.  agente:  search_tickets(project="mi-tesis")
            → evita duplicar lo que ya está en el board
4.  agente:  lint_ticket({title, summary, acceptance, evidence, ...})
            → {ok: false, violations: [E_ACCEPTANCE_VAGUE, ...]}
5.  agente:  (reescribe) → lint_ticket(...) → {ok: true}
6.  agente:  create_tickets_batch([... 12 drafts ...])
            → por-item: {code: "OS-014", status: "created"}
                          {index: 7, status: "rejected", violations: [...]}
7.  agente:  commit + PR de los tickets que puede ejecutar
8.  humano:  abre /, lee el board, comenta, cambia prioridad
9.  agente:  set_status("OS-014", "in_progress") → implementa
10. agente:  close_ticket("OS-014", verification={pr, commit, tests, commands})
11. ticket queda en `done` con evidencia de verificación en el historial
```

**Por qué el paso 4 y 5 existen y no es gold-plating.** Un agente NO tiene forma de saber si su criterio de aceptación es verificable. El linter es el único feedback determinista que existe en el loop. Sin él, el agente posa "funciona correctamente" y el humano descubre el hueco tres días después. Con él, el ciclo de corrección es de segundos y no de sesiones.

### Segundo flujo: planificación en grupo

El primero es un agente solo contra el repo. El segundo es el que pide el equipo: varias personas y varios agentes alrededor de un mismo tema.

```
1.  humano:  abre sesión  →  start_session(project="mi-tesis", title="Auth: migrate to JWT",
                                        goal="Que /login deje de usar sesiones en memoria")
2.  agente A: join_session("PLN-002")
              → recibe objetivo, participantes y los tickets ya escritos en la sesión
3.  humano:   join + rol=facilitator desde la web
4.  agente B: join_session("PLN-002")  → ve lo que escribió A, aporta lo que falta
5.  ambos:    create_tickets_batch([...], session="PLN-002")
6.  humano:   revisa OS-018 → review_ticket(verdict="changes_requested", note="...")
7.  agente A: update_ticket(OS-018) → nueva versión → review_ticket(verdict="approved")
8.  cierre:   close_session("PLN-002")
```

El paso 2 es el que cambia el producto: un agente que entra a una sesión **lee lo que ya se decidió en ella antes de aportar**. Sin eso, dos agentes aportan la misma idea con nombres distintos y el humano deduplica a mano.

### Componentes

| Componente | Responsabilidad | No hace |
|---|---|---|
| `api/mcp/` | Contrato MCP: tools, resources, auth | Nada de lógica de negocio |
| `sessions/` | Sesiones de planificación: start/join/close, participantes, snapshot para agentes | No redacta tickets |
| `reviews/` | Veredictos sobre tickets, ortogonales al status | No cambia el status del ticket |
| `tickets/linter.py` | Reglas puras: draft → lista de violaciones | No toca DB |
| `tickets/service.py` | Orquestación: lintear, persistir, idempotencia, estados, eventos | No conoce MCP ni HTTP |
| `tickets/repository.py` | Queries, FTS5, paginación | No decide nada de negocio |
| `db/` | Engine, sesión, Alembic | — |
| `api/web/` | Rutas + templates para el humano | No duplica reglas de negocio |
| `app/skill/` | Fuente de verdad del mini skill (markdown) | No se importa desde Python salvo como lectura de archivo |

La frontera que importa: **`linter.py` es puro y testeable sin FastAPI ni DB**. Es la pieza con más lógica de negocio y la que más tests merece.

---

## Modelo de entidades

Decidido con vos. **11 tablas.** Todas las FK con `ON DELETE CASCADE` salvo `projects`/`users` (RESTRICT: no se borra un proyecto con tickets ni un usuario con tickets asignados).

```
                         ┌──────────────┐
                         │    users     │  personas: dueña real del trabajo
                         └──────┬───────┘
                     ┌───────────┼────────────┐
                     │ 1:N       │ 1:N         │ 1:N
              ┌──────▼─────┐ ┌───▼──────┐ ┌────▼──────────┐
              │ api_tokens │ │  agents  │ │   projects    │
              │ (credencial│ │ (identid.│ │  (metadata    │
              │  de acceso)│ │  de ag.)│ │   del proyecto)│
              └──────┬─────┘ └───┬──────┘ └────┬──────────┘
                     │ 1:N       │ 1:N          │ 1:N
                     │           │      ┌───────▼──────────┐
                     │           │      │    sessions     │ PLN-002
                     │           │      │  (planificación)│
                     │           │      └───┬─────────┬───┘
                     │           │        1:N       │ 1:N
                     │           │   ┌────▼─────┐  ┌──▼─────────────────┐
                     │           │   │ session_ │  │ (tickets de la    │
                     │           │   │ participa│  │  sesión)          │
                     │           │   │  nts     │  └─────────┬─────────┘
                     │           │   └────┬─────┘            │ N:1
                     │           │        │      ┌───────────┤
                     └───────────┴────────┘      ▼           ▼
                                          ┌──────▼──────┐ ┌──────────────┐
                                          │   tickets   │─▶│ ticket_links │
                                          │             │ │ (from, to,   │
                                          │ owner_user ─┘ │     kind)     │
                                          │ author_agent─┘└──────────────┘
                                          │ session_id ─┐
                                          └──────┬──────┘
                       ┌───────────────────────┼─────────────┬───────────┐
                       ▼                       ▼             ▼           ▼
                ┌─────────────┐        ┌────────────┐ ┌────────────┐ ┌────────┐
                │  comments   │        │  reviews   │ │ticket_ev.  │ │(events)│
                └─────────────┘        └────────────┘ └────────────┘ └────────┘
```

### Tablas

**`users`** — la persona. Es quien *responde* por los tickets, no solo quien los escribe.
`id, name UNIQUE, email?, is_admin, next_ticket_seq, created_at`

**`agents`** — la identidad del cliente MCP que redacta. Pertenece a un `user`.
`id, user_id FK, name UNIQUE, client ('claude_code'|'codex'|'opencode'|'other'), model_reported?, description?, created_at, retired_at?`

**`api_tokens`** — la credencial. Pertenece a un `user`, opcionalmente a un `agent`.
`id, user_id FK, agent_id FK?, name, token_hash UNIQUE (sha256), scopes (JSON: ['tickets:read','tickets:write']), project_scope (JSON: ['mi-tesis','otro-side']), created_at, last_used_at?, revoked_at?`
`project_scope` vacío = sin restricción (solo para uso interno; ningún token de agente real lo lleva vacío).

**`projects`** — metadata. El server **no lee el repo**: solo guarda nombre, descripción y convenciones que el equipo declara.
`id, slug UNIQUE, name, description?, conventions?, next_session_seq, created_at, archived_at?`

**`sessions`** — una sesión de planificación sobre **un tema** dentro de un proyecto. Varias sesiones conviven sobre el mismo proyecto, cada una con su recorte.
`id, code UNIQUE ("PLN-002"), project_id FK, title, goal, status ('open'|'closed'), created_by_user_id FK, created_at, closed_at?`
El código es por proyecto, igual que los tickets: contador en `projects.next_session_seq`, incrementado en la misma transacción que inserta la fila.

**`session_participants`** — quién estuvo en la sesión. Personas y agentes indistintamente.
`id, session_id FK, user_id FK?, agent_id FK?, role ('facilitator'|'contributor'|'observer'), joined_at`
`CHECK ((user_id IS NOT NULL) != (agent_id IS NOT NULL))` — exactamente uno de los dos, nunca ambos ni ninguno.
`UNIQUE(session_id, user_id)` y `UNIQUE(session_id, agent_id)` — unirse dos veces es idempotente y no duplica fila.

**`ticket_reviews`** — veredictos de revisión. **Ortogonales al status**: no lo cambian, no lo bloquean.
`id, ticket_id FK, reviewer_user_id FK?, reviewer_agent_id FK?, verdict ('approved'|'changes_requested'), note?, ticket_content_hash NOT NULL, created_at`
`CHECK ((reviewer_user_id IS NOT NULL) != (reviewer_agent_id IS NOT NULL))` — la doble atribución aplicada a quien revisa.
`ticket_content_hash` es el hash **del ticket en el momento de la revisión**: sirve para saber si el veredicto sigue siendo vigente (ver "Revisión contra qué versión").

**`tickets`** — el corazón.
`id, code UNIQUE ("OS-014"), project_id FK, session_id FK?, owner_user_id FK, author_agent_id FK?, author_user_id FK?, external_key?, type, priority, status, title, summary, problem?, outcome, evidence (JSON), steps (JSON), acceptance (JSON), scope (JSON), risks (JSON), tags (JSON), estimate?, content_hash, created_at, updated_at`
`UNIQUE(project_id, external_key)` — la idempotencia es **por proyecto, no por sesión**: relanzar un agente dentro de una sesión no duplica tickets.
`session_id` nullable: un ticket creado por un agente que recorre el repo solo no pertenece a ninguna sesión.

**`ticket_links`** — relaciones tipadas.
`id, from_ticket_id FK, to_ticket_id FK, kind ('blocks'|'split_from'|'relates_to'), created_at`
`UNIQUE(from_ticket_id, to_ticket_id, kind)`

**`comments`** — conversación humano↔agente.
`id, ticket_id FK, author_user_id FK?, author_agent_id FK?, body, created_at`

**`ticket_events`** — auditoría append-only. Nunca se actualiza ni se borra.
`id, ticket_id FK, kind ('created'|'updated'|'status_changed'|'assigned'|'commented'|'closed'|'lint_failed'|'relinked'), actor_user_id?, actor_agent_id?, payload (JSON), created_at`

### Doble atribución: `owner` vs `author`

Tu observación fue la correcta y la fijo así:

- **`author_agent_id`** — quién *escribió* el ticket. Inmutable después de la creación.
- **`owner_user_id`** — quién *responde*. **Cambiable**: un agente lo reasigna (`assign_ticket`), un humano lo reasigna desde la web.

Por qué importa y por qué no es redundante: el board tiene dos preguntas distintas que responder. *"¿qué está escribiendo mal el agente?"* (calidad de redacción → filtrar por `author_agent_id`) y *"¿ya lo vio Fede?"* (responsabilidad → agrupar por `owner_user_id`). Con un solo campo perdés una de las dos. Y cuando el agente es de un solo(dev) pero el trabajo es del equipo, el owner puede ser alguien distinto al dueño del agente.

**Regla de default**: si el agente no manda `owner_user_id`, el owner es el `user` dueño del agente. Nunca queda sin owner — un ticket sin responsable es un ticket perdido.

### Relaciones entre tickets

Un solo tipo de tabla con `kind`:
- **`blocks`** — dependencia de bloqueo. Es la única que participa en la validación de ciclos (DFS sobre el grafo). Un ticket `blocked` no puede pasar a `in_progress` si alguno de sus `blocks` está abierto.
- **`split_from`** — este ticket nació de dividir otro. Trazabilidad: si dividís OS-007 en tres, los tres apuntan a OS-007 y se ve por qué existen. UI: "3 tickets derivados de OS-007".
- **`relates_to`** — contexto, sin semántica de orden. Simétrica por construcción.

Nada de `supersedes` ni `duplicates` en v1: se agregan como un `kind` nuevo en la tabla si hacen falta.

### Sin roles de usuario

La autorización vive en **dos capas**, no en una tabla de roles:
1. **Persona** (`users.is_admin`): solo controla quién puede crear/revocar tokens de otras personas.
2. **Credencial** (`api_tokens.scopes`): `tickets:read` y `tickets:write`. Un token de lectura no crea ni cierra nada, aunque la persona sea admin.

Esto evita la ceremonia de un RBAC que nadie va a usar en un equipo de 3-10 personas.

### Sesiones de planificación

Una sesión es el **contexto compartido** donde humanos y agentes aportan tickets sobre un mismo tema. Tres propiedades la hacen funcionar:

**1. El agente lee antes de escribir.** `join_session(code)` devuelve el objetivo, los participantes y los tickets ya escritos. Sin esto, dos agentes aportan la misma idea con nombres distintos y el humano deduplica a mano. Es la razón de que la sesión sea entidad y no una etiqueta.

**2. Unirse es idempotente.** `UNIQUE(session_id, agent_id)` convierte un segundo `join_session` en un no-op que devuelve la misma fila. El agente puede llamar al join las veces que quiera al inicio de una sesión de trabajo sin ensuciar la lista de participantes.

**3. Cerrada es cerrada.** `create_tickets_batch(session="PLN-002")` sobre una sesión `closed` devuelve `E_SESSION_CLOSED` con un mensaje que dice que abra una nueva. Un `join_session` sobre una sesión cerrada sí funciona y devuelve el contexto en modo lectura, con un campo `read_only: true`: un agente que llega tarde tiene que poder ver de qué se trata antes de aportar.

**Rol del participante:** `facilitator` (lleva la sesión y la cierra), `contributor` (aporta tickets), `observer` (mira). El rol **no restringe permisos**: es contexto, no autorización. Un `observer` puede aportar igual; lo que cambia es cómo lo muestra el board.

### Revisión: el otro eje

El error fácil sería meter la revisión dentro del status. Son dos ejes distintos:

| Eje | Pregunta que responde | Campos |
|---|---|---|
| **Status** | ¿En qué momento del trabajo está? | `tickets.status` |
| **Revisión** | ¿Alguien lo miró y qué dice? | `ticket_reviews.verdict` |

Un ticket puede estar `in_progress` con dos aprobaciones, o `ready` con tres pedidos de cambio. **Ninguna combinación bloquea nada**, y esa es la decisión: la revisión informa, el status manda.

Por qué no bloquea, en concreto: vos decidiste que el ticket nace `ready` para que el equipo no se quede esperando a que alguien mire el board. Si la revisión bloqueara, habríamos reintroducido ese cuello de botella con un nombre nuevo. Un pedido de cambio se ve como badge en el board y llega al owner; si a nadie le sirve, se ignora. El costo de la revisión no bloqueante es que un ticket puede ejecutarse sin revisión — ese es el precio consciente de no frenar al equipo.

**Cualquiera del equipo puede revisar**, sin necesidad de ser participante de la sesión. Revisar es opinar, no intervenir.

`changes_requested` sin `note` se rechaza (`E_REVIEW_NOTE_REQUIRED`): pedir cambios sin decir cuáles es lo mismo que no opinar, y ensucia el board.

**No guardamos un único campo `reviewed_by` en `tickets`.** Cada revisión es una fila, así que un ticket puede tener tres revisores con tres veredictos distintos en el tiempo, y se ve la historia completa en el `detail`.

### Revisión contra qué versión

Una revisión sin versión asociada es ruido. Si Fede pidió cambios y el autor corrigió el ticket, un badge rojo que dice "piden cambios" miente sobre el estado real.

Solución: cada revisión guarda el `content_hash` del ticket **en el momento de la revisión**.

```
tickets.content_hash        = sha256(title + summary + outcome + evidence + steps + acceptance + scope)
```

Cuando el board muestra el estado de revisión de un ticket:

1. Se agrupan las revisiones por revisor.
2. De cada revisor se toma **su veredicto más reciente sobre el `content_hash` actual**.
3. Los veredictos sobre hashes anteriores se muestran aparte, tachados, con la etiqueta *"sobre versión anterior"*.

El resultado: si nadie revisó desde la última edición, el badge dice **"sin revisar desde el último cambio"**, que es la verdad y además es el mensaje que empuja a alguien a mirar. Y el histórico completo sigue disponible.

`update_ticket` **no** crea revisiones ni las invalida: no reescribe la historia, solo deja que el cálculo de "vigente" cambie por el hash.

Esto no agrega filas ni campos más allá del `content_hash` que la revisión ya necesitaba, y resuelve el caso que hace que un board de revisiones sea inútil en la práctica.

---

## El contrato de ticket (corazón del producto)

### Payload canónico

```jsonc
{
  "external_key": "mi-tesis:auth:extraer-jwt",   // estable, idempotencia (único por proyecto)
  "project": "mi-tesis",                          // slug de un Project existente (o se crea)
  "owner_user": "fede",                          // quién responde; default = dueño del agente
  "title": "Extraer verificación de JWT a un módulo testeable",

  "summary":    "La verificación de JWT vive inline en el endpoint /login y no se puede testear sin levantar la app. Moverla a auth/verify.py permite probarla como función pura.",
  "problem":    "Hoy el test del endpoint necesita TestClient + DB real, y hay 3 tests por cada uno que podría tener.",
  "outcome":    "auth.verify_token(token, secret, leeway) es una función pura con tests unitarios; /login la llama y no cambia de comportamiento.",

  "type": "refactor",              // feature|bug|refactor|chore|docs|test|perf|security
  "priority": "p2",                // p0|p1|p2|p3 (default p2)

  "evidence": [                    // path:line REALES que el agente leyó
    { "path": "backend/app/api/login.py", "lines": "42-88", "note": "verificación inline" },
    { "path": "backend/tests/api/test_login.py", "lines": "10-60", "note": "3 tests con TestClient" }
  ],

  "steps": [                       // 3..12 pasos, verbos de implementación
    "Crear backend/app/auth/verify.py con verify_token(token, secret, leeway=0).",
    "Mover la lógica actual de login.py:42-88 sin cambiar semántica.",
    "Exponer la dependencia secret vía parámetro, no import global.",
    "Agregar backend/tests/auth/test_verify.py con 4 casos: válido, expirado, firma inválida, malformado.",
    "Delegar en /login y borrar el bloque inline."
  ],

  "acceptance": [                  // 1..20, cada uno VERIFICABLE objetivamente
    "backend/tests/auth/test_verify.py corre verde con `uv run pytest backend/tests/auth`.",
    "El módulo auth.verify_token no importa nada de app.api ni de app.db.",
    "GET /login con un JWT expirado sigue devolviendo el mismo status y body que antes del cambio."
  ],

  "scope": {
    "include": ["extracción del módulo", "tests unitarios nuevos"],
    "exclude": ["cambio de formato de token", "rotación de secrets", "cambio en el frontend"]
  },

  "risks": ["cambiar el orden de validación rompería el error de /login"],
  "tags": ["auth", "testabilidad"],
  "estimate": "s",                // xs|s|m|l|xl (opcional)
  "deps": []                       // codes ("OS-003") o external_keys; edges de bloqueo
}
```

### Por qué cada campo está

| Campo | Razón |
|---|---|
| `external_key` | Idempotencia + dedupe entre agentes. Estable por *qué es el problema*, no por dónde está el archivo. |
| `evidence` con `lines` | Forcejea al agente a citar lo que leyó. Es el campo que máskills tickets inventados. |
| `steps` | Da el "cómo", que es lo que un humano no quiere adivinar. 3-7items fuerza granularidad. |
| `acceptance` | El criterio de "hecho". Todo lo demás es contexto. |
| `scope.exclude` | Lo que más se olvida y más caro sale. Explícito > implícito. |
| `deps` | Edges de bloqueo explícitos (tu skill `to-tickets` ya lo pide). |
| `risks` | Opcional pero pedido explícitamente: un ticket sin riesgos pensados es un ticket sin pensar. |

### Reglas del linter

Cada violación devuelve `{code, field, message, fix}` — el `fix` es lo que el agente lee para corregirse.

| Code | Dispara cuando | `fix` (ejemplo) |
|---|---|---|
| `E_TITLE_MISSING` | título vacío o >120 chars | "Máximo 120 caracteres; fórmula: verbo + objeto + dónde." |
| `E_SUMMARY_MISSING` | `summary` ausente o <80 chars | "Describí QUÉ hace y POR QUÉ, en 2-5 oraciones." |
| `E_OUTCOME_MISSING` | `outcome` ausente o <40 chars | "Enunciá el cambio observable, no la tarea." |
| `E_EVIDENCE_MISSING` | sin `evidence` (o <1 item) | "Cite al menos un path:line que leíste." |
| `E_EVIDENCE_MALFORMED` | item sin `path`, o path absoluto, o `..` | "Usá path relativo al repo, formato `path:a-b`." |
| `E_STEPS_MISSING` | sin `steps` | "Entre 3 y 12 pasos de implementación." |
| `E_STEPS_TOO_MANY` | >12 pasos | "Dividí el ticket: >12 pasos es más de un ticket." |
| `E_STEPS_VAGUE` | paso con <15 chars o sin verbo | "Cada paso arranca con un verbo: Crear/Mover/Agregar/Eliminar." |
| `E_ACCEPTANCE_MISSING` | sin `acceptance` | "Al menos un criterio de aceptación." |
| `E_ACCEPTANCE_VAGUE` | criterio con léxico no-verificable | "Reformulá en forma verificable." |
| `E_ACCEPTANCE_TOO_MANY` | >20 criterios | "Recortá; si son 20, son varios tickets." |
| `E_SCOPE_EMPTY` | `scope.include` vacío | "Enumerá qué entra." |
| `E_SCOPE_TOO_BIG` | >15 items en include | "Dividí el ticket." |
| `W_NO_EXCLUDE` | `scope.exclude` vacío | *(warning, no bloquea)* "¿Qué queda explícitamente afuera?" |
| `W_NO_RISKS` | `risks` vacío | *(warning)* "¿Qué puede romperse?" |

**Umbrales altos a propósito (decidido).** Arrancamos permisivos: 12 pasos / 20 criterios / 15 items de `include`. La razón es una sola: **el loop del agente nunca debe trabarse**. Un umbral demasiado estricto no produce tickets mejores, produce agentes que abandonan el lote a mitad de camino. Lo que protege la calidad no es el número sino las dos reglas cualitativas, que no dependen del umbral: `E_ACCEPTANCE_VAGUE` y `E_EVIDENCE_MISSING`.

**Telemetría para recalibrar.** Cada llamada a `create_ticket` que falla el linter escribe un `ticket_events` con `kind='lint_failed'` y `payload={code, field}` (ticket descartado, sin fila en `tickets`). Con eso, a las dos semanas se puede ver la distribución real de tamaños y bajar los umbrales con evidencia en vez de con intuición. Es el único dato que justifica cambiar estos números.

**Detección de `E_ACCEPTANCE_VAGUE`** — lista de tokens matcheados con `\b`, case-insensitive, en español e inglés:
`funciona`, `funcione`, `correctamente`, `bien`, `ok`, `okay`, `todo`, `etc`, `etcetera`, `as appropriate`, `should work`, `works`, `properly`, `correct`, `robust`, `graceful`, `sin errores`, `no falla`, `termina`, `termina bien`, `se ve`, `limpio`.
Regla: un criterio cuyo único "hito" es uno de esos tokens y tiene <60 chars → violación. Los ejemplos aceptados se dan en el mini skill.

### Estados y transiciones

**El ticket nace `ready`.** Decidido: si ya pasó el linter, es ejecutable desde el minuto uno. El humano revisa y comenta, pero no es un filtro obligatorio — si lo fuera, en un equipo chico el board se vuelve un cuello de botella y los agentes se quedan esperando.

`open` existe igual, pero como estado de *backlog* al que un humano puede degradar un ticket que no quiere que se toque todavía. Es una decisión del humano, nunca el estado inicial.

```
     (nace acá)
          ▼
   ┌───────────┐   ┌─────────────┐   ┌──────┴───┐
   │   ready   │──▶│ in_progress │──▶│  blocked │
   └─────┬─────┘   └──────┬──────┘   └──────────┘
         │                │               │ (vuelve a in_progress)
         │◀───────────────┘
         │
         │ (degradar a backlog, decisión humana)
         ▼
   ┌───────────┐
   │   open    │
   └───────────┘

   ready|in_progress|blocked|open ──▶ wont   (descartado, con razón)
   in_progress ──▶ done   SOLO vía close_ticket con verification
   done ──▶ in_progress  (reapertura)
```

Transiciones permitidas (matriz en `domain/transitions.py`, testeada exhaustivamente):
`open→{ready, wont}`, `ready→{open, in_progress, wont}`, `in_progress→{blocked, done, wont}`, `blocked→{in_progress, wont}`, `done→{in_progress}` (reapertura), `wont→{open}`.

Cualquier otra transición → `E_INVALID_TRANSITION` con la lista de transiciones válidas.

`→ done` **solo** vía `close_ticket(code, verification)`, que exige `verification.pr` o `verification.commit` y al menos un comando de verificación. No se puede.setear `done` con `set_status`. Esto es lo que convierte el board en un loop de cierre y no en un sticky note.

---

## Layout del repo

```
OpenSupport/
├── AGENTS.md                     # contrato NORMATIVO y corto (~150 líneas) para agentes
├── README.md                     # qué es + cómo correr local + cómo conectar un agente
├── CONTEXT.md                    # glosario de dominio: draft, violation, owner, autor, link
├── docs/
│   ├── architecture.md           # por qué: monolito, sync, MCP montado, sin REST
│   ├── ticket-contract.md        # el contrato completo + JSON Schema + tabla de violaciones
│   ├── operations.md             # deploy, backups, restore, migraciones, troubleshooting
│   └── decisions/                # ADRs: 0001-sin-llm-en-server.md, 0002-sqlite-un-worker.md, ...
├── pyproject.toml                # uv / hatchling / ruff / mypy strict
├── uv.lock
├── Dockerfile                    # python:3.13-slim, non-root, EXPOSE $PORT
├── railway.toml                  # builder, healthcheckPath, restart policy
├── alembic.ini
├── alembic/versions/0001_initial.py
├── app/
│   ├── __init__.py
│   ├── main.py                   # create_app(), monta /mcp, routers, lifespan
│   ├── config.py                 # Settings (pydantic-settings)
│   ├── cli.py                    # init / create-user / create-token / backup / export
│   ├── db/
│   │   ├── base.py               # DeclarativeBase, create_engine, PRAGMAs
│   │   ├── session.py            # session_scope(), db_call() helper a threadpool
│   │   └── fts.py                # setup FTS5 + triggers de sync
│   ├── domain/
│   │   ├── models.py             # User, Agent, ApiToken, Project, Ticket, TicketLink, Comment, TicketEvent
│   │   ├── enums.py              # TicketStatus, TicketType, Priority, LinkKind, AgentClient
│   │   └── transitions.py        # state machine
│   ├── auth/
│   │   ├── tokens.py             # hash, verify, generate
│   │   └── session.py            # cookie firmado (itsdangerous)
│   ├── projects/
│   │   ├── service.py            # create/list/slugify
│   │   └── repository.py
│   ├── agents/
│   │   ├── service.py            # register/retire, resolve_agent_from_token
│   │   └── repository.py
│   ├── tickets/
│   │   ├── schema.py             # TicketDraft, TicketPatch, Violation (Pydantic)
│   │   ├── linter.py             # PURO: draft -> list[Violation]
│   │   ├── service.py            # create/update/lint/close/transition/assign + idempotencia
│   │   ├── repository.py         # queries + FTS5 + paginación por cursor
│   │   ├── links.py              # create/list links + validación de ciclos (blocks)
│   │   └── codes.py              # generate_next_code(project) -> "OS-014"
│   ├── api/
│   │   ├── mcp/
│   │   │   ├── server.py         # FastMCP(name="OpenSupport", auth=SqliteTokenVerifier)
│   │   │   ├── auth.py           # TokenVerifier subclass → sqlite lookup
│   │   │   ├── tools.py          # @mcp.tool
│   │   │   ├── resources.py      # @mcp.resource
│   │   │   └── mapping.py        # TicketDraft <-> args de tool
│   │   └── web/
│   │       ├── router.py         # rutas HTML + partials HTMX
│   │       ├── views.py          # handlers Jinja; delegan en tickets/service.py
│   │       └── deps.py           # usuario de la sesión, guards
│   ├── templates/
│   │   ├── base.html  login.html  index.html  ticket.html  tokens.html
│   │   └── partials/  _board.html _ticket_card.html _comment_form.html _status_bar.html
│   ├── static/
│   │   ├── app.css               # ~200 líneas, sin build step
│   │   └── htmx.min.js           # vendorizado o CDN con SRI
│   └── skill/
│       └── ticket-authoring.md   # FUENTE DE VERDAD del mini skill (con frontmatter)
└── tests/
    ├── conftest.py               # db tmp_path, app, token de test, client MCP
    ├── fakes/
    │   └── valid_draft.py        # factory de drafts válidos (reusada por todos)
    ├── test_linter.py            # table-driven, una fila por regla + borders
    ├── test_transitions.py       # matriz completa + rejected
    ├── test_links.py             # links, ciclos en blocks, split_from, relates_to
    ├── test_service.py           # idempotencia, code gen, optimistic lock, owner/assign
    ├── test_mcp_tools.py         # fastmcp.Client in-memory contra la app
    ├── test_mcp_resources.py     # skill + schema legibles
    ├── test_auth.py              # 401 sin token / inválido / revocado / scope insuficiente
    ├── test_web.py               # render, redirects, partials HTMX, no-leak cross-user
    ├── test_export.py            # markdown compatible con `gh issue create`
    └── test_migrations.py        # upgrade head desde vacío
```

**No existe `app/api/rest/`.** Es una decisión, no un forgot. La web llama al service layer directamente; ver "Por qué no hay REST".

### Dependencias

```toml
requires-python = ">=3.13,<3.15"
dependencies = [
  "fastapi[standard]>=0.141",     # incluye uvicorn
  "jinja2>=3.1",
  "python-multipart>=0.0.20",     # forms de comentarios/login
  "sqlalchemy>=2.0.36,<3.0",
  "alembic>=1.14,<2.0",
  "fastmcp>=3.2,<4.0",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "itsdangerous>=2.2",
]
[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.28",
       "ruff>=0.9", "mypy>=1.13", "coverage>=7.6"]
```

**Desviación deliberada respecto de FinancePet:** ahí usan `sqlmodel`; acá uso **SQLAlchemy 2.0 tipado** (`Mapped[...]`). Razón: los tickets tienen ~8 columnas JSON anidadas (`evidence`, `steps`, `acceptance`, `scope`, `deps`) y con SQLModel se vuelve incómodo tipar `sa_column(JSON)`. Donde JSON alcanza (strings, listas de strings, dicts chicos)SQLAlchemy tipado es más directo. Todo lo demás (uv, ruff, mypy strict, Alembic, layout modular) es idéntico.

**Sin `aiosqlite` a propósito.** DB sync + `def` en los handlers web (FastAPI los corre en threadpool). Las tools MCP se declaran `async def` y la llamada a DB se hace con un helper `db_call(fn, *args)` que envuelve `anyio.to_thread.run_sync` — explícito y sin sorpresas de event loop.

---

## Convenciones de código y AGENTS.md

### Idioma (decidido)

**Identificadores en inglés, contenido en español.** El corte es exacto:

| Va en **inglés** | Va en **español** |
|---|---|
| Nombres de función, clase, variable, método | Todo mensaje de error del linter (`message`, `fix`) |
| Nombres de tabla, columna, índice, enum | Mensajes de validación de Pydantic |
| Nombres de test, de archivo, de función de test | Textos de templates y UI |
| Docstrings | `ticket_events.kind` explicados, comentarios de commit |
| Commits (`feat:`, `fix:`, `refactor:`) — Conventional Commits | El mini skill (`ticket-authoring.md`) |
| Claves de `settings.py` | README, AGENTS.md, docs/ |

Por qué este corte: el inglés es el idioma de interoperabilidad (grep, stack traces, documentación upstream, código de otras fuentes). El español es el de las personas que leen tickets y errores. **Ningún mensaje que vea un humano o un agente va en inglés.** Si alguna vez se contradice, gana el español para contenido y el inglés para identificadores.

### AGENTS.md: normativo y corto, con `docs/` para el detalle

Decidido: un `AGENTS.md` de **~150 líneas que se lee entero, siempre**, y `docs/` para lo que se consulta bajo demanda.

**Lo que va en `AGENTS.md`** (normativo — si lo violás, el código está mal):
1. Qué es OpenSupport en 3 líneas y qué NO es (no LLM, no REST, no lee repos).
2. Arquitectura en un diagrama ASCII + la tabla de fronteras (qué módulo puede importar qué).
3. Los 8 comandos: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `opensupport serve`, `opensupport init`, `opensupport backup`, `opensupport export`.
4. Las reglas inviolables: TDD en dominio, `db_call` para todo acceso a DB desde async, 1 worker, nunca loguear tokens, nunca `print` (ruff T201).
5. Cómo se agrega una tool MCP (4 pasos, y el test que va con ella).
6. Cómo se agrega una regla del linter (el test va primero, tabla de umbrales).
7. Glosario de 10 líneas: draft, violation, owner, autor, link, code.
8. Links a `docs/` y a `CONTEXT.md`.

**Lo que va en `docs/`** (explicativo, se lee cuando hace falta):
- `docs/architecture.md` — por qué cada decisión (con ADRs en `docs/decisions/`).
- `docs/ticket-contract.md` — el contrato completo, JSON Schema, tabla de violaciones con ejemplos.
- `docs/operations.md` — deploy, backups, restore, migraciones, troubleshooting.

**Lo que va en `CONTEXT.md`**: glosario de dominio extendido, para cuando el repo tenga varios agentes trabajando en paralelo.

**El criterio**: si una regla *evita un error*, va en AGENTS.md. Si una regla *explica un porqué*, va en docs/.

### Reglas de código

**TDD por capas** (decidido):

| Capa | Estrategia | Por qué |
|---|---|---|
| `linter/`, `transitions/`, `links/` | **TDD estricto.** Test rojo primero, sin excepciones | Lógica pura, sin IO. El test es más barato que el debug. |
| `tickets/service.py` | **TDD estricto** con DB real en `tmp_path` | Es donde vive la idempotencia y la concurrencia: necesita tests antes que comentarios. |
| `api/mcp/` | Test de integración escrito junto al código (no antes) | El contrato se descubre hablando con FastMCP; el test documenta lo que se construyó. |
| `api/web/` | Tests de render + autorización. **No** se testean partials por fragmento exacto | El HTML es implementación; los tests de web verifican comportamiento y aislamiento, no markup. |
| `templates/` | Solo smoke: renderiza sin 500 | Un test por cada combo de datos (vacío, 1 ticket, 50 tickets, con draft rechazado). |

**Reglas duras** (van a `ruff` y a `AGENTS.md`):
- `mypy --strict` sin excepciones. Los `# type: ignore` necesitan justificación en el PR.
- Cero `print` (`T201`). Logging con `logging.getLogger(__name__)`.
- Funciones de más de 40 líneas: extraelas. Más de 5 parámetros: agrupá en un dataclass o Pydantic model.
- Ningún `except Exception` sin loguear la excepción completa con `exc_info`.
- Ninguna dependencia nueva sin justificar en el PR: `pyproject.toml` es corto a propósito.

**Anti-patrones explícitos** (lo que un agente suele hacer mal y acá está prohibido):
- Meter lógica de negocio en `api/mcp/tools.py` o en `api/web/views.py`. Va en `service.py`. Es la causa #1 de que un sistema con dos superficies termine con dos versiones de la verdad.
- SQL crudo en las tools o en las vistas. Va en `repository.py`.
- `create_autoescape=False` en Jinja. Siempre autoescape on.
- Exponer el token plano en logs, errores o el historial de eventos.

---

## Superficie MCP

### Tools

| Tool | Scopes | Input (esencial) | Output |
|---|---|---|---|
| `lint_ticket` | `tickets:write` | draft completo | `{ok, violations[], normalized}` — **no persiste** |
| `create_ticket` | `tickets:write` | draft completo + `session?` | `{code, created|existing, warnings[]}` |
| `create_tickets_batch` | `tickets:write` | `{items: [...], session?}` | `{results: [{index, status, code|violations}]}` — parcial, no todo-o-nada |
| `update_ticket` | `tickets:write` | `{code, patch, expected_updated_at}` | `{code, updated_at}` o `E_CONFLICT` |
| `assign_ticket` | `tickets:write` | `{code, owner_user}` | `{code, owner_user}` — cambia el responsable, no el autor |
| `link_tickets` | `tickets:write` | `{from, to, kind}` | `{ok}` o `E_DEPS_CYCLE` / `E_UNKNOWN_TICKET` |
| `set_status` | `tickets:write` | `{code, status, note?}` | `{code, status}` o `E_INVALID_TRANSITION` |
| `add_comment` | `tickets:write` | `{code, body}` | `{comment_id, created_at}` |
| `close_ticket` | `tickets:write` | `{code, verification{pr\|commit, commands[], notes?}}` | `{code, status: "done"}` |
| `review_ticket` | `tickets:write` | `{code, verdict, note?}` | `{code, review_count, summary}` — **no cambia el status**; devuelve `stale: true` si el ticket cambió desde ese veredicto |
| `start_session` | `tickets:write` | `{project, title, goal}` | `{code: "PLN-002"}` |
| `join_session` | `tickets:read` | `{code}` | `{code, goal, participants[], tickets[], read_only}` |
| `close_session` | `tickets:write` | `{code}` | `{code, status: "closed", tickets_count}` |
| `list_sessions` | `tickets:read` | `{project?, status?, limit=20, cursor?}` | `{items[], next_cursor}` |
| `get_ticket` | `tickets:read` | `{code}` | ticket completo normalizado + `reviews[]` |
| `list_tickets` | `tickets:read` | `{status?, type?, priority?, project?, tag?, limit=50, cursor?}` | `{items[], next_cursor}` |
| `search_tickets` | `tickets:read` | `{query, limit=20}` | hits con fragmento y code |
| `get_schema` | `tickets:read` | — | JSON Schema del contrato + lista de violaciones |

Todas devuelven **structured output** (FastMCP deriva el `output_schema` del return type). Los errores de validación NO son excepciones MCP: son parte del output estructurado con `violations`, para que el agente pueda parsearlos y corregirse en loop. Excepciones solo para auth/DB caídos.

### Resources

| URI | Devuelve | Para qué |
|---|---|---|
| `opensupport://skill/ticket-authoring` | `SKILL.md` completo con frontmatter | **el mini skill**: reglas, ejemplos buenos/malos, anti-patrones |
| `opensupport://session/{code}` | markdown de la sesión: objetivo, participantes, tickets con resumen y estado de revisión | el join por HTTP, para clientes que prefieren no llamar una tool |
| `opensupport://schema/ticket` | JSON Schema (draft 2020-12) | para agentes que wanna validar antes de postear |
| `opensupport://schema/violations` | tabla de códigos + significado + fix | decodificar errores sin adivinar |
| `opensupport://board/snapshot` | markdown del board del usuario | orientarse sin N llamadas |
| `opensupport://meta` | versión, límites, stats del board | |

Son **6 resources y 17 tools**. Todos `mime_type: "text/markdown"` salvo `schema` (`application/json`). El resource del skill se sirve leyendo el archivo `app/skill/ticket-authoring.md` desde disco → **una sola fuente de verdad**: el mismo markdown que se puede copiar a un `SKILL.md` local si el agente lo prefiere.

### Estructura del mini skill

```markdown
---
name: ticket-authoring
description: Cómo redactar tickets para OpenSupport...
version: 1
---

# Ticket authoring para OpenSupport

## Cuándo usar esto
## El loop correcto (lint → corregir → crear)
## Anatomía de un ticket aceptable
## Ejemplo completo BUENO (mi-tesis / extracción de JWT)
## Ejemplo RECHAZADO + por qué (y cómo arreglarlo)
## Los 10 errores más comunes
## Checklist antes de postear
## Cómo reportar progreso y cerrar (set_status, close_ticket)
## Respuestas a las violaciones del linter
```

### Auth (resumen)

Las máquinas (agentes) entran a `/mcp` con `Authorization: Bearer os_...`. Los humanos entran a la web con cookie de sesión firmada. Los dos mecanismos son distintos y a propósito. El diseño completo, el por qué de no usar JWT y el estado de la spec MCP están en **"Autenticación y sesiones"** más abajo.

El token se muestra **una sola vez** al crearse (como el PAT de GitHub): se guarda `sha256`, nunca el plano. Se implementa como subclase de `fastmcp.server.auth.providers.jwt.TokenVerifier` (que valida cualquier token, no solo JWT) haciendo lookup en `api_tokens`.

**Rate limit por token**: token bucket en memoria, 120 writes/min y 600 reads/min por token. No es distribuido (no hace falta: 1 réplica).

---

## Web (HTMX + Jinja2) — deliberadamente simple

6 pantallas, cero build step, cero framework JS. Un solo `app.css` a mano.

**Ninguna de estas rutas es una API.** Son la interfaz humana; devuelven HTML (o fragmentos HTML para HTMX), se autentican por cookie, y no son un contrato programable. Ver "Por qué no hay REST".

| Ruta | Qué hace | HTMX |
|---|---|---|
| `GET /login` | login por token | form normal |
| `GET /` | board: columnas por estado, contadores, filtro por status/type/priority/project/owner, búsqueda | filtro y búsqueda = `hx-get` a partials; transiciones = botones `hx-post` |
| `GET /t/{code}` | detalle: contenido por sección, links, historial, comentarios, owner | status bar, owner y comentario = swap de partial |
| `GET /settings/tokens` | crear/revocar tokens y registrar agentes + snippet de conexión por cliente (claude / codex / opencode) | revoke = `hx-delete` |
| `GET /sessions` | sesiones del proyecto, agrupadas por estado, con nº de tickets y nº de participantes | filtro por proyecto = `hx-get` |
| `GET /s/{code}` | detalle de sesión: objetivo, participantes con rol, tickets de la sesión con su badge de revisión | unirse y cerrar = `hx-post` |
| `GET /settings/projects` | crear/listar proyectos y sus convenciones | — |
| `GET /healthz` · `GET /readyz` | liveness · readiness (DB + head de Alembic) | — |

El detalle del ticket suma una sección **Revisión**: la lista de veredictos con autor, fecha y nota, más un formulario de dos botones (`Apruebo` / `Pido cambios`). El formulario de `Pido cambios` **exige la nota** — el mismo `E_REVIEW_NOTE_REQUIRED` del server, aplicado en la UI. El badge del board muestra `✓2` en verde o `!3` en ámbar, y filtra por "con cambios pedidos" como un filtro más.

El board agrupa por **owner** en vez de por estado en la vista de "mis tickets", porque la pregunta del día a día es "¿qué tengo yo?". El filtro por estado queda en el selector.

El contenido del ticket se renderiza con un filtro Jinja custom `md` (markdown mínimo: headings, listas, backticks, negrita) — sin depender de `markdown` completo para contenido que el agente ya controla.

**Anti-objetivos de UI**: sin drag-and-drop, sin columnas kanban animadas, sin websockets, sin design system. Se ve bien y se usa bien; no intenta competir con nada.

---

## Autenticación y sesiones

Investigado a partir de la spec de MCP (`modelcontextprotocol.io/specification/2025-06-18/basic/authorization`) y del comportamiento de FastMCP.

### El hallazgo que define todo

> **"Authorization is OPTIONAL for MCP implementations."**
> — Model Context Protocol, sección Authorization, Protocol Requirements

La spec **no obliga** a tener auth. Y cuando la implementás sobre HTTP, dice que **SHOULD** ser OAuth 2.1 completo: RFC 8414 (metadata del AS), RFC 7591 (registro dinámico de clientes), RFC 9728 (protected resource metadata), PKCE, resource indicators RFC 8707, y `WWW-Authenticate` en cada 401.

**Qué significa eso en la práctica:** un bearer token estático es una desviación consciente del *SHOULD*, no una violación del *MUST*. Y la desviación tiene un costo real que hay que pesar, no ignorar.

### Las tres audiences, no dos

Lo que hace "agéntica" a esta web no es que los agentes hablen MCP — eso es el transporte. Es que **hay tres tipos de actor distintos** y cada uno necesita una respuesta distinta a "¿quién sos y qué podés hacer?":

| Actor | Credencial | Mecanismo | Por qué |
|---|---|---|---|
| **Agente** (Claude Code, Codex) | `Authorization: Bearer os_...` | Token opaco + lookup en DB | Es una máquina: necesita revocación instantánea y un handle de auditoría. No tiene pantalla ni callback de browser. |
| **Humano** (tu teammate) | Cookie de sesión firmada | `itsdangerous`, `HttpOnly`, `SameSite=Lax`, `Secure` | Es un browser: el cookie viaja solo, sobrevive reloads, y no se puede exfiltrar a un repo. |
| **Un agente dentro de la web** (Playwright, computer-use) | No tiene — usa la sesión de un humano | **Prohibido explícitamente** | Ver "no impersonación" abajo. |

La tercera fila es la que casi nadie piensa y es la que más problemas causa. Un agente con browser **no tiene identidad propia** salvo que se la des. Y darle la de un humano es exactamente el error de diseño que hay que evitar.

### Por qué NO JWT (y por qué tu instinto de FinancePet no aplica acá)

FinancePet usa JWT access + refresh cookie, y está bien: tiene clientes móviles y una API pública donde el costo de verificación stateless importa. Acá el cálculo es al revés:

| Criterio | JWT | Token opaco + DB |
|---|---|---|
| Revocación | **Inmediata requiere blocklist** → volvés a tener estado, que es justo lo que querías evitar | `DELETE`/`revoked_at`. Instantáneo. |
| Ventaja real del stateless | Verificar sin DB | **Inexistente**: un `SELECT` por `token_hash` en SQLite es de microsegundos, y ya vas a pegarle a la DB igual en cada tool call. |
| Superficie de fuga | El payload es legible por cualquiera que tenga el token; si metés datos, van en claro | Es una string aleatoria de 256 bits. No hay nada que filtrar. |
| Scopes | Van dentro del token, no se pueden cambiar sin reemitir | En la DB: cambiás scopes y el efecto es inmediato. |
| Auditoría gratis | No (`last_used_at` no existe) | Sí, `last_used_at` se actualiza en cada uso |
| Coste de implementación | Firmar, verificar, expiraciones, blocklist, rotación | 30 líneas |

**Conclusión: token opaco.** `os_` + `secrets.token_urlsafe(32)`, `sha256` en `api_tokens.token_hash`, lookup en cada request. Opcionalmente se le pone `expires_at` para tokens de agentes que alguien quiere que caduquen, pero lo default es **sin expiración y revocable** — que es exactamente lo que querés cuando querés "revocá el token de la laptop de Fede ahora".

**Dónde JWT sí valdría:** si algún día OpenSupport se parte en varios servicios que precisam validar el token sin pegarle a la DB. No es el caso y no va a serlo con 1 réplica.

### Por qué NO OAuth 2.1 (aun siendo SHOULD de la spec)

OAuth 2.1 sobre MCP te obliga a correr (o integrar) un authorization server completo, con registro dinámico de clientes, PKCE, redirect URIs registradas y flujo de consent. Para un equipo de 3-10 personas donde vos mismo generás los tokens desde `/settings/tokens`, eso es mucha infraestructura para resolver un problema que no tenés.

El trade-off es concreto:

| | Token estático (elegido) | OAuth 2.1 completo |
|---|---|---|
| Conectar un agente | Pegar `os_xxx` en `.mcp.json` | Login interactivo con browser |
| Desvío de la spec | No (el *SHOULD*, no el *MUST*) | Sí |
| Infraestructura | 0 | Un authorization server (Auth0/Keycloak/your own AS) |
| Revocar acceso | Click en la UI | Revocar sesión en el IdP, que puede requerir esperar expiración |
| Cuándo tiene sentido | Servidor autoalojado, equipo conocido | Servidor público multi-tenant, clientes de terceros |

**Los clientes que usamos soportan token estático**: Claude Code acepta `headers` en `.mcp.json` y Codex acepta `bearer_token_env_var`. Ninguno de los dos *necesita* discovery si el token ya está configurado.

**No hay contingencia OAuth: no existe el escenario.** El error sería imaginar un "tercer cliente" que exija discovery OAuth, y no lo hay. Todos los consumidores de OpenSupport son tuyos: tus agentes (Claude Code, Codex, OpenCode — que aceptan token estático en su config) y tu equipo humano (que usa la web con cookie). Nadie externo se conecta. La pregunta "¿y si algún día un cliente insiste?" no aplica: si algún día **vos** querés abrir OpenSupport a usuarios externos, esa es una decisión de producto —multi-tenant, IdP, billing— y se toma con el producto delante, no como contingencia técnica. El token estático es la decisión final, no un placeholder.

### Implementación

```
/mcp  (agentes)
  └── FastMCP(auth=SqliteTokenVerifier)
        └── sha256(bearer) → SELECT api_tokens WHERE token_hash=? AND revoked_at IS NULL
              → AccessToken(sub=user_id, scopes=[...], claims={agent_id})
                    └── FastMCP verifica required_scopes por tool
                          └── 401 si no hay token, 403 si falta scope (según la spec)

/  (humanos)
  └── SessionMiddleware lee cookie firmada → user_id → templates
        └── login: pega un token propio → se valida → cookie HttpOnly

/_ui/*  (parciales HTMX)
  └── misma sesión + CSRF token en header (hx-headers)
```

### Tres decisiones de seguridad que quedan fijadas

**1. Un agente NUNCA actúa como un humano.** No hay tool de impersonación, ni parámetro `as_user` en las tools MCP. La identidad de un agente es su token; el `owner_user_id` es un campo explícito del ticket que cualquiera puede cambiar (`assign_ticket`), no una delegación de permisos. Esto elimina de raíz la clase de bugs donde un agente compromise escribe tickets como si fueras vos.

**2. La cookie de sesión NO sirve contra `/mcp`.** Un test verifica que un `Set-Cookie` válido no autentica una llamada MCP. Cada superficie valida su propia cosa; no hay un "token universal" que se pueda reused de un lado al otro.

**3. CSRF en toda mutación web.** La web es cookie-authenticated, y HTMX hace POSTs. `SameSite=Lax` cubre el caso común, pero además cada POST de la web exige un token CSRF en el header (`hx-headers`). Sin esto, un agente con browser (computer-use) navegando a una página hostil podría disparar acciones con tu sesión. Esto es precisamente el riesgo que crea una web agéntica y no existe en una web normal.

**4. Nunca loguear credenciales.** `Authorization` y `Set-Cookie` van a una lista de redacción en el middleware de logging desde el primer commit. Los tokens no aparecen en `ticket_events`, ni en errores, ni en los `ticket_events.payload`.

### Lo que esto NO incluye

- Sin SSO (Google/GitHub). Si algún día el equipo crece, es agregar OAuth provider como un camino más de login — no cambia el resto.
- Sin recuperación de contraseña. El login es "pegá tu token", que para un equipo autoalojado es el equivalente funcional y no requiere emails ni reseteo.
- Sin 2FA. El control real es la superficie: token opaco, revocable, con scopes, más rate limit.

---

### Export a GitHub Issues

Decidido: convive con GitHub Issues, con export en un sentido.

`opensupport export --format gh` genera archivos markdown **compatibles con `gh issue create`**: frontmatter YAML con `title`, `labels`, `body` con las 8 secciones del ticket (summary, problem, outcome, evidence, steps, acceptance, scope, risks), links a los tickets relacionados, y el `code` (`OS-014`) en el título y como label. Botón "Exportar como issue" en el detalle del ticket que deja el `.md` en `/tmp` listo para pipear.

**Es export, no sync.** OpenSupport es la fuente de verdad; GitHub es donde el equipo mira. No hay webhook de vuelta, no hay escritura automática. Si un ticket se actualiza en OpenSupport, regenerar el `.md` es una operación manual de un comando. Es la complejidad mínima que evita perder el contexto del flujo habitual del equipo.

---

## Estado, concurrencia, ownership, falla y recuperación

### Ownership y visibilidad

Decisión: **el equipo lee en compartido, escribe en privado.** Son dos ejes distintos y el plan anterior los mezclaba.

| Operación | Quién puede |
|---|---|
| **Leer** cualquier ticket de cualquier proyecto | Cualquiera con un token válido. Sin excepciones. |
| **Modificar** (`update`, `set_status`, `assign`, `link`, `close`) | El `owner_user_id` de ese ticket, o el `author_agent_id` si quien llama es ese agente, o un `is_admin`. |
| **Comentar y revisar** | Cualquiera del equipo (opinar no es intervenir). |
| **Crear tickets** | Cualquier agente, **solo en los proyectos que tiene asignados** (ver abajo). |

**Por qué no un filtro por `user_id` en cada query.** El modelo original filtraba todo por el usuario del token, lo que vuelve cada board privado. Eso choca con el objetivo real —un equipo planificando juntos— y además hace inútil la idea de sesión: si Fede no ve los tickets que escribió Claude Code, no puede revisar ni powerfully_session.

**El aislamiento que sí se mantiene, y es el que importa:** nadie puede *cambiar* un ticket que no sea suyo. Un `E_NOT_YOUR_TICKET` en cualquier mutación. Es lo que evita que dos agentes del equipo se pisen el mismo ticket en silencio. El test `test_no_cross_user_leak` sigue existiendo, pero verifica **escritura**, no lectura: dos usuarios, uno intenta `update_ticket` sobre un ticket del otro → 403.

**Un admin** (`users.is_admin`) puede modificar cualquier ticket. Con 3-10 personas esto casi nunca se usa, pero sirve para desbloquear un ticket que quedó con el owner equivocado.

### Alcance de los agentes

Un agente no escribe en cualquier proyecto: escribe en los que tiene asignados.

`api_tokens.project_scope` es una lista de slugs (JSON array). Vacía = sin alcance (solo un token de servicio interno, que no debería existir). Cuando `create_ticket` o `create_tickets_batch` reciben un `project` fuera del scope, la respuesta es:

```json
{ "status": "rejected",
  "violations": [{ "code": "E_PROJECT_OUT_OF_SCOPE", "field": "project",
    "message": "El agente 'claude-code-fede' no tiene alcance sobre 'mi-trabajo'.",
    "fix": "Proyectos permitidos: mi-tesis, otro-side. Pedí que agreguen el proyecto desde /settings/tokens." }] }
```

El mensaje lista los permitidos, porque el caso real de esta falla no es un agente malicioso: es un agente que se conectó al board equivocado y no lo sabe.

En `/settings/tokens`, al crear un token se eligen los proyectos del checkbox. Es un selector múltiple y no hace falta nada más.
- **Un usuario puede tener N tokens** (uno por persona, uno por agente), para revocar individually sin romper al equipo.

### Concurrencia y SQLite

PRAGMAs en cada conexión:

```sql
PRAGMA journal_mode = WAL;      -- con fallback a DELETE (ver riesgo de volumen de red)
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA synchronous  = NORMAL;
```

- **Un solo worker uvicorn.** Sin `--workers N`. Con Railway Free/Hobby = 1 réplica. Esto simplifica TODO y elimina las condiciones de carrera multi-proceso.
- **Optimistic concurrency**: cada update exige `expected_updated_at`; si no matchea → `E_CONFLICT` con el `updated_at` actual, para que el agente re-lea y reintente. Dos agentes que tocan el mismo ticket no se pisan en silencio.
- **Escrituras multi-fila** usan `BEGIN IMMEDIATE`.
- **Idempotencia**: `UNIQUE(user_id, external_key)`. En `create_ticket`, un `IntegrityError` en ese constraint se traduce a `{status: "existing", code: <existente>}`, no a un 500.
- **`create_tickets_batch` es parcial por diseño**: una transacción por ticket, para que un item inválido no tire los 19 válidos. Es lo que hace que un agente pueda crear "todo lo que pude" y no perder trabajo.
- **Generación de código** `OS-001`: contador en `users.next_ticket_seq`, incrementado dentro de la misma transacción. No hay race porque SQLite serializa escritores.

### Fallas y recuperación

| Falla | Comportamiento |
|---|---|
| Agente se corta a mitad de `create_tickets_batch` | Los ya creados quedan; los pendientes no existen. Re-run con los mismos `external_key` → idempotente, completa lo que falta. |
| Draft inválido | `violations` estructuradas, **cero filas escritas**. El agente itera. |
| Agente escribe en un proyecto fuera de su scope | `E_PROJECT_OUT_OF_SCOPE` con la lista de proyectos permitidos en el `fix`. Cero filas escritas. |
| Un humano o agente intenta modificar un ticket ajeno | `E_NOT_YOUR_TICKET` (403). Lectura sí permitida; escritura no. Un admin sí puede. |
| Token expirado/revocado a mitad de una sesión | `401` en la siguiente llamada; el agente re-auth. Ningún estado parcial. |
| 5xx / DB caído | MCP devuelve error de tool; el agente decide si reintenta. Los writes son transaccionales, no hay estados intermedios. |
| Disco lleno (Railway 0.5GB en plan Free) | `PRAGMA integrity_check` en `/readyz`; el `opensupport backup` reporta tamaño. Umbral de alerta en `/readyz`. |
| Rollback de migración | Alembic `downgrade` soportado; `migrations` testeadas desde cero en CI. |
| SQLite corrompido | `opensupport restore <archivo>` desde el último backup; documentado en `docs/operations.md`. |

### Backups (no negociable, hay una tesis en juego)

- `opensupport backup` usa la **API `sqlite3` backup** (no `cp` — no es atómico con WAL) → `/data/backups/opensupport-YYYYMMDD.db`, conserva las últimas 10.
- `opensupport export --format markdown` vuelca el board entero a un archivo listo para commitear a git. Es el respaldo humano-legible y el que sobrevive a cualquier cambio de hosting.
- Railway: cron plugin diario (o `opensupport backup` desde el mismo contenedor). Documentado en `docs/operations.md`.

---

## Despliegue

### Local (ahora)

```bash
uv sync
uv run opensupport init                    # crea ./data/opensupport.db + usuario admin
uv run opensupport create-token admin      # imprime os_xxx (una sola vez)
uv run opensupport serve --host 127.0.0.1 --port 8000
```

Agentes en la misma máquina: `http://127.0.0.1:8000/mcp` con el bearer token. Config snippets listos para copiar en `/settings/tokens`:

```jsonc
// Claude Code (.mcp.json) — remoto por HTTP
{ "mcpServers": { "opensupport": {
    "type": "http", "url": "https://<host>/mcp",
    "headers": { "Authorization": "Bearer os_xxx" } } } }

// Codex (config.toml)
[mcp_servers.opensupport]
url = "https://<host>/mcp"
bearer_token_env_var = "OPENSUPPORT_TOKEN"
```

### Railway (después) — `railway.toml` + `Dockerfile`

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "opensupport serve --host 0.0.0.0 --port $PORT"
healthcheckPath = "/readyz"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
numReplicas = 1          # SQLite exige 1
```

Vars: `DATABASE_URL=sqlite:////data/opensupport.db` (volume montado en `/data`), `SECRET_KEY` (generada), `MCP_REQUIRED_TOKENS=true`.

### Riesgos de despliegue — leídos de la doc actual, no asumidos

| Riesgo | Realidad verificada | Mitigación |
|---|---|---|
| **Volúmenes del trial se BORRAN** | "Railway deletes stateful volumes created by Trial accounts 30 days after the expiration of their credits." | Volumen solo en plan **Hobby** ($5/mes, incluye $5 de uso). Antes de que expire el trial: `export --format markdown` + commit a git. |
| **Free duerme a los 5 min** | Free/Trial: auto-sleep tras 5 min idle, autostart hasta 5 min. | Los clientes MCP reconectan solos. `$5/mes` Hobby da 30 min de sleep. No afecta correctitud, sí latencia del primer request. |
| **SQLite sobre volumen de red** | Railway monta volúmenes sobre NFS. WAL requiere shared memory, que NFS no siempre soporta. | Detección en startup: intentar `PRAGMA journal_mode=WAL`; si falla, caer a `DELETE` y loguear un warning. `busy_timeout=5000`. **Siempre 1 réplica.** Test de escritura/lectura en `/readyz`. |
| **Free = 0.5 GB de volumen, 1 vCPU / 0.5 GB RAM** | Doc oficial de planes. | Un board de equipo con 500 tickets weighs ~2-5 MB. 0.5GB alcanza de sobra. Verificado con el presupuesto de `volumes` (~2-3% de overhead). |
| **Costo real Railway** | Hobby $5/mes incluye $5 de uso. RAM $10/GB/mes, CPU $20/vCPU/mes. Un servicio de 0.5GB RAM ocioso ≈ $5/mes. | El budget está incluido en la suscripción. Monitorear "Estimated Usage" la primera semana (recomendación oficial de Railway). |
| Escalabilidad horizontal | No soportada y no se busca. | Documentado como non-goal. Si hiciera falta, la migración natural es Postgres — el acceso a datos está aislado en `repository.py`. |

Alternativa de escape si el volumen de red da problemas: **libSQL/Turso** (SQLite remoto) o mover a Postgres. Por eso el acceso a datos vive aislado en `repository.py` y no se filtra SQL a las tools.

---

## Tests y criterios de aceptación

### Suite de tests

| Archivo | Qué cubre | Casos clave |
|---|---|---|
| `test_linter.py` | las 16 reglas | 1 caso por regla + border cases: título de 120 y 121 chars, summary de 79 y 80, "funciona correctamente" vs "el endpoint devuelve 200", path absoluto vs relativo, 12 vs 13 pasos, 20 vs 21 criterios, 15 vs 16 items de scope, warnings que NO bloquean |
| `test_transitions.py` | matriz de estados | todas las aristas válidas + una por cada inválida, `wont→done` rechazada, `done` exige `close_ticket`, un ticket **nace** `ready` |
| `test_links.py` | relaciones | `blocks` con ciclo → `E_DEPS_CYCLE`; `blocks` sin ciclo OK; `split_from` y `relates_to` no validan ciclo; link a code inexistente → `E_UNKNOWN_TICKET`; no se puede linkear un ticket consigo mismo |
| `test_service.py` | orquestación | idempotencia (mismo `external_key` 2 veces en el mismo proyecto → 1 fila), secuencia de códigos **por proyecto**, `expected_updated_at` desactualizado → `E_CONFLICT`, owner default = dueño del agente, `assign_ticket` cambia owner y **no** cambia autor, batch parcial (3 items, 1 malo → 2 creados + 1 rechazado), draft inválido escribe evento `lint_failed` y **no** fila |
| `test_sessions.py` | sesiones | `start_session` genera código **por proyecto** (dos proyectos empiezan en PLN-001); `join_session` dos veces con el mismo agente → una sola fila de participante; participante puede ser user **o** agent, nunca ambos (viola el CHECK); `join_session` sobre sesión cerrada devuelve `read_only: true` y el contenido igual; `create_ticket(session=cerrada)` → `E_SESSION_CLOSED`; el snapshot del recurso `session/{code}` contiene objetivo, participantes y los tickets |
| `test_reviews.py` | revisión ortogonal | `review_ticket` **no cambia** `tickets.status` en ningún caso; `changes_requested` sin nota → `E_REVIEW_NOTE_REQUIRED`; varias revisiones del mismo ticket se acumulan (3 filas, no 1); revisar dos veces por la misma persona con veredictos distintos se guarda como dos filas; un humano revisa sin ser participante de la sesión; **el veredicto queda `stale` si el ticket cambió después**; un ticket editado y nunca re-revisado muestra "sin revisar desde el último cambio" en el board |
| `test_access.py` | visibilidad y alcance | **lectura**: el usuario B lee un ticket de A sin error; **escritura**: B hace `update_ticket` sobre un ticket de A → 403 `E_NOT_YOUR_TICKET`; A sí puede; un admin sí puede sobre cualquiera; un token con `project_scope=['mi-tesis']` no puede crear en `otro-side` → `E_PROJECT_OUT_OF_SCOPE` con la lista de permitidos, y sí puede en `mi-tesis`; un agente que es el `author_agent_id` puede editar su propio ticket aunque el owner sea otro |
| `test_mcp_tools.py` | contrato MCP | `fastmcp.Client` in-memory: la lista de tools incluye las **17** esperadas; `lint_ticket` devuelve `violations` sin escribir; `create_ticket` con draft malo **no crea fila**; token read-only falla en `create_ticket` y en `review_ticket` pero **sí** funciona en `join_session` y `get_ticket` |
| `test_mcp_resources.py` | resources | `opensupport://skill/ticket-authoring` devuelve markdown con frontmatter `name: ticket-authoring`; `schema/ticket` devuelve JSON Schema válido; `board/snapshot` lista los tickets del usuario y **solo** los suyos |
| `test_auth.py` | auth | sin header → 401; token inexistente → 401; token revocado → 401; scope insuficiente → 403; el token plano nunca aparece en la DB (solo su sha256); **la cookie de sesión web NO autentica `/mcp`**; token revocado deja de funcionar en la request siguiente, sin esperar expiración |
| `test_web.py` | web | login OK/FAIL, board renderiza N tickets, detalle renderiza las 8 secciones, partials devuelven fragmento (sin `<html>`), **test de no-escritura entre usuarios** (ver `test_access.py`), cookie de sesión no funciona como bearer token contra `/mcp` |
| `test_export.py` | export | el markdown generado tiene frontmatter `title`/`labels`, las 8 secciones, el code en el título; `gh issue create` acepta el body sin markdown roto |
| `test_migrations.py` | schema | `alembic upgrade head` desde una DB vacía; `downgrade base`; FTS5 creada y poblada; las **11** tablas y sus FKs existen; los CHECK de `session_participants` y `ticket_reviews` rechazan user+agent simultáneos |

Invariante de test global: `tests/fakes/valid_draft.py` provee `make_valid_draft()` y `make_invalid_draft()` usados por todos los archivos. Un draft "válido" que el linter rechaza rompe la suite → la fricción de testear contra la factory es la que mantiene el contrato honesto.

### Criterios de aceptación (verificables, end-to-end)

1. **Bootstrap**: en una máquina limpia, `uv sync && uv run opensupport init && uv run opensupport serve` levanta la web en `127.0.0.1:8000` y responde 200 en `/readyz`.
2. **Contrato sin instalación**: un cliente MCP que solo conoce la URL y el token puede hacer `resources/read` de `opensupport://skill/ticket-authoring` y recibe el skill completo. No requiere copiar archivos ni instalar nada.
3. **Ciclo de lint**: un draft sin criterios verificables es rechazado con `{ok: false, violations: [{code: "E_ACCEPTANCE_VAGUE", field: "acceptance", message, fix}]}` y **no** se escribe ninguna fila en `tickets`.
4. **Idempotencia**: correr `create_tickets_batch` dos veces con los mismos 12 `external_key` deja **12** tickets, y la segunda respuesta devuelve `status: "existing"` para los 12.
5. **Aislamiento**: con dos usuarios, ninguno puede leer ni mutar los tickets del otro (verificado por test, no a ojo).
6. **Cierre exige evidencia**: `set_status(code, "done")` es rechazado; `close_ticket(code, verification={pr, commands})` funciona y deja el ticket en `done` con el verification en el historial.
7. **Doble atribución**: un ticket creado por un agente queda con `author_agent_id` = ese agente y `owner_user_id` = su dueño; `assign_ticket` cambia el segundo y deja el primero intacto, con ambos cambios en `ticket_events`.
8. **Nace ejecutable**: un ticket recién creado y validado está en `ready`, sin intervención humana.
9. **Determinismo**: 200 ejecuciones del linter sobre el mismo draft dan el mismo resultado, sin LLM ni randomness.
10. **Sin REST**: `GET /api/v1/...` devuelve 404; el board y el detalle funcionan igual (el front humano no depende de una API).
11. **Calidad de código**: `pytest` verde, `ruff check` limpio, `mypy --strict` limpio.
12. **Container**: `docker build .` + `docker run -p 8000:8000 -v opensupport_data:/data -e DATABASE_URL=sqlite:////data/opensupport.db` levanta y sobrevive un restart con los datos intactos.
13. **Documentación**: un teammate sin contexto previo llega de `README.md` a un ticket creado en <10 minutos siguiendo los snippets de `/settings/tokens`.
14. **Export**: `opensupport export --format gh | gh issue create -` funciona sobre un ticket real y produce un issue legible.
15. **Join de sesión**: un agente que entra a `join_session("PLN-002")` recibe el objetivo, los participantes y los tickets ya escritos, **incluyendo los que escribió otro agente en esa misma sesión**. Sin esto, dos agentes cambian el producto.
16. **Revisión no bloquea**: un ticket con tres `changes_requested` sigue siendo `ready` y se puede executar. La UI muestra el badge y el filtro "con cambios pedidos" los trae.
17. **Sesión cerrada**: `create_ticket(session="PLN-002")` con la sesión cerrada devuelve `E_SESSION_CLOSED`; `join_session` sí responde, en modo lectura.
18. **Lectura compartida, escritura acotada**: con dos usuarios, B lee un ticket de A sin error, pero `update_ticket` de B sobre ese ticket devuelve `E_NOT_YOUR_TICKET`.
19. **Alcance de proyecto**: un token con `project_scope=["mi-tesis"]` crea tickets en `mi-tesis` y recibe `E_PROJECT_OUT_OF_SCOPE` en `otro-side`, con la lista de permitidos en el `fix`.
20. **Revisión vigente**: un ticket con un `changes_requested`, luego editado y no re-revisado, muestra "sin revisar desde el último cambio" en el board; su veredicto viejo aparece como histórico, no como badge activo.

---

## Orden de implementación (work units commits)

Cada unidad es un commit revisable por separado, con sus tests. Las unidades 1-7 son el producto usable por agentes; la web no bloquea ese uso.

| # | Unidad | Contiene | TDD |
|---|---|---|---|
| 0 | **Skill `no-ai-slop`** (global, fuera del repo) | ✅ Hecho: canónico en `~/.agents/skills/no-ai-slop/SKILL.md` + 8 symlinks verificados | — |
| 1 | **Scaffold + AGENTS.md** | `pyproject.toml`, `uv.lock`, `config.py`, `db/base.py`, `main.py` mínimo, `/healthz`, `CONTEXT.md`, `docs/architecture.md`, ADR `0001-sin-llm-en-server` | — |
| 2 | **Dominio + migración** | 9 modelos, `enums.py`, `transitions.py`, `links.py`, Alembic `0001` (incluye FTS5), `auth/tokens.py` | ✅ estricto |
| 3 | **Linter** | `schema.py`, códigos de violación, `linter.py`, `fakes/valid_draft.py` | ✅ estricto |
| 4 | **Servicio** | `service.py`, `repository.py`, idempotencia, optimistic lock, owner/assign, export | ✅ estricto |
| 5 | **MCP: auth + read** | `mcp/auth.py`, `mcp/server.py`, `mcp/resources.py`, `get_ticket`/`list`/`search`/`get_schema` | integración |
| 6 | **MCP: write** | `lint_ticket`, `create_ticket`, `create_tickets_batch`, `update`, `assign`, `link`, `set_status`, `add_comment`, `close_ticket` | integración |
| 7 | **Sesiones** | `sessions/`, `session_participants`, `start/join/close_session`, resource `session/{code}`, `test_sessions` | ✅ estricto (idempotencia del join) |
| 8 | **Revisión** | `reviews/`, `review_ticket`, badge y filtro en el board, `test_reviews` | ✅ estricto (que no toca el status) |
| 9 | **Mini skill** | `app/skill/ticket-authoring.md` completo con el ejemplo de referencia | test de resource |
| 10 | **Web** | templates, `static/`, `api/web/*`, login, board, detalle con revisión, sesiones, tokens, proyectos | render + auth |
| 11 | **CLI + ops** | `cli.py` (`init`, `create-user`, `create-token`, `backup`, `export`, `restore`), `docs/operations.md`, Railway | integración |
| 12 | **Endurecer** | rate limit, `/readyz` con `integrity_check`, validación de umbrales del linter, tests de carga del batch | integración |

**Unidades 1-9 son el producto usable por agentes**; la web (10) es necesaria para el loop humano pero no bloquea el uso por MCP.

**Reparto sugerido para trabajo en paralelo.** `linter/` (3) y `sessions/` (7) son independientes: el linter no toca DB y las sesiones no conocen reglas de redacción. Un agente puede hacer el 3 mientras otro hace el 7. El 4 es el punto de convergencia y conviene hacerlo con vos delante, porque es donde se decide qué significa "ticket válido" en el servicio.

---

## Riesgos y decisiones abiertas

### Riesgos con mitigación definida

| Riesgo | Impacto | Mitigación |
|---|---|---|
| **El linter es demasiado estricto** y los agentes no pueden crear nada (loop infinito lint↔corregir) | Alto — el producto se vuelve inutilizable | El skill incluye un draft de ejemplo completo que pasa el linter. Los warnings (`W_*`) nunca bloquean. Medir la tasa de rechazo en la primera semana de uso real y relajar. |
| **El linter es demasiado flojo** y entran tickets como "arreglar el login" | Alto — vuelve a ser un sticky note | Las 2 reglas más duras son `E_ACCEPTANCE_VAGUE` y `E_SCOPE_TOO_BIG`. Son el corazón. Tests con casos reales de tu tesis. |
| **Tokens de agentes filtrados** si el deploy queda público | Alto | Bearer tokens, nunca en la DB en plano, rate limit, sin datos sensibles en los tickets. Opcionalmente basic-auth adicional sobre `/mcp` con `MCP_REQUIRED_TOKENS`. |
| **Railway cobra por uso** y el usuario no lo nota | Bajo | Un servicio de 0.5GB está cubierto por los $5 de Hobby. Monitorear la primera semana ( Railway lo recomienda explícitamente). |
| **SQLite sobre NFS rompe WAL** en Railway | Medio | Detección en startup + fallback a `journal_mode=DELETE`. Verificado por `/readyz` post-deploy. |
| **Agente genera tickets de baja calidad en masa** y el board se llena de ruido | Medio | `external_key` + `E_SCOPE_TOO_BIG` + `close_ticket` con verificación. Más: `search_tickets` antes de crear, para que el agente detecte duplicados. |
| Cosas que descubrimos tarde sobre el comportamiento real de FastMCP v3 (p.ej. una tool sync corriendo en el event loop) | Bajo | Ya está mitigado por diseño (`async def` + `db_call` a threadpool, explícito). Primer spike: conectar un cliente MCP real y listar tools antes de construir encima. |

### Decisiones tomadas (cerradas)

Todas resueltas en esta sesión de planning. No reopened salvo que aparezca evidencia nueva.

| # | Decisión | Elección | Motivo |
|---|---|---|---|
| 1 | Nombre | **OpenSupport** | — |
| 2 | Project | entidad de primera clase | El board separa por proyecto (tesis, trabajo, side) |
| 3 | Agent | entidad, **más** owner humano | Doble atribución: el agente escribe, la persona responde |
| 4 | Owner por defecto | dueño del agente, reasignable | Nunca queda un ticket sin responsable |
| 5 | Relaciones | `blocks` + `split_from` + `relates_to` | Una tabla, tres `kind` |
| 6 | Roles | ninguno; scopes en tokens | RBAC es ceremonia en equipos de 3-10 |
| 7 | Estado inicial | `ready` (ejecutable ya) | Un gate humano en un equipo chico es cuello de botella |
| 8 | Umbrales del linter | 12 / 20 / 15 (permisivos) | El loop del agente no puede trabarse; telemetría para recalibrar |
| 9 | REST | **no** — MCP puro | Dos contratos para las máquinas es peor que uno |
| 10 | Front de humanos | HTML+HTMX sobre el service layer | Una UI es HTTP por naturaleza; no es un contrato |
| 11 | GitHub Issues | export `.md` compatible con `gh issue create`, un sentido | OpenSupport es la fuente de verdad |
| 12 | AGENTS.md | normativo ~150 líneas + `docs/` + `CONTEXT.md` | Si la regla evita un error, va en AGENTS.md; si explica un porqué, va en docs/ |
| 13 | Idioma | identificadores inglés, contenido español | Interoperabilidad vs. quien lee tickets |
| 14 | Tests | TDD estricto en dominio, integración en MCP/web | El linter no puede depender de tests superficiales |
| 15 | Deploy | local primero, Railway después | Railway borra volúmenes del trial a los 30 días |
| 16 | Qué es una sesión | **contexto compartido**: los agentes la leen y escriben | Sin esto, dos agentes aportan lo mismo con nombres distintos |
| 17 | Revisión vs status | **ortogonal**: informa, no bloquea | Bloquear devolvería el cuello de botella del gate humano |
| 18 | Alcance de sesión | **por tema, dentro de un proyecto** | Varias sesiones por proyecto, cada una con su recorte |
| 19 | Quiénes participan y revisan | **personas y agentes, con roles** | Coherente con la doble atribución |
| 20 | Auth | token **opaco** + cookie de sesión, sin JWT, sin OAuth | La spec MCP marca auth OPTIONAL; todos los consumidores son tuyos |
| 21 | Visibilidad | **el equipo lee todo; la escritura es del owner o del autor** | Un board privado por persona rompe la idea de sesión compartida |
| 22 | Revisión vs edición | la revisión guarda el `content_hash`; los veredictos viejos se marcan como históricos | Un badge que dice "piden cambios" sobre algo ya corregido miente |
| 23 | Alcance de agente | cada token tiene `project_scope`; fuera de scope → `E_PROJECT_OUT_OF_SCOPE` | Un agente conectado al board equivocado tiene que enterarse por el error |

### Pendientes que no bloquean la implementación

Son decisiones operativas o de segundo orden, con un default sensato ya asumido en el plan:

1. **Renombrar el directorio del repo** de `agent-support` a `OpenSupport` (local, `git mv`, antes del primer commit de código). Default: hacerlo en la unidad 1.
2. **Cuándo subir a Railway.** Default: después de la unidad 9, cuando haya backup funcionando. Antes, no.
3. **Basic-auth adicional sobre `/mcp`.** Default: no; bearer token + rate limit alcanzan hasta que esté público con datos reales.
4. **OAuth / multi-tenant externo.** Descartado: OpenSupport es para tu equipo, no es un producto multi-tenant. Si algún día se abre a usuarios externos, es una decisión de producto nueva (IdP, billing, aislamiento), no una contingencia técnica de auth.
5. **Cuándo recalibrar los umbrales.** Default: mirar la distribución de `lint_failed` a las 2 semanas, no antes.
6. **Si el volumen de Railway rompe WAL.** Default: fallback a `journal_mode=DELETE` y seguir. Escape hatch: libSQL/Turso o Postgres, con el acceso a datos ya aislado en `repository.py`.
7. **`projects.conventions` quedó sin uso.** La columna existe desde el diseño original pero nada la lee: la decisión 23 eligió *scope* en vez de *linter por convenciones*. Default: conservarla y documentarla como reservada para una regla del linter del tipo `E_CONVENCION_<proyecto>`. Borrarla es una migración más.
8. **"Sin revisar desde el último cambio" puede ser ruidoso.** Si el equipo edita tickets seguido, el board puede llenarse de ese estado. Default: mostrarlo solo en el detalle del ticket y no como badge en el board, hasta ver cuánto aparece en uso real.

---

## Fuentes consultadas para el plan

- Panorama free tier 2026: `snapdeploy.dev/state-of-free-hosting`, `agentdeals.dev/hosting-pricing`, `render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026`, `fly.io/learn/python-hosting` — convergencia: no existe PaaS gratis con disco persistente + always-on.
- Oracle Always Free reducido a 2 OCPU/12GB: `infoq.com/news/2026/07/oracle-cloud-free-tier-limits`, `docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm`.
- Railway: `docs.railway.com/volumes/reference`, `docs.railway.com/pricing/free-trial`, `docs.railway.com/pricing/plans`.
- FastMCP v3: montaje de ASGI en FastAPI (`mcp.http_app(path="/")` + `lifespan`), `@mcp.tool` con structured output, `@mcp.resource` con URI templates, `TokenVerifier` sin OAuth discovery y `RemoteAuthProvider` como fallback.
