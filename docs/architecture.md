# Arquitectura

Por qué cada decisión. Lo normativo está en `AGENTS.md`.

## 0001 — Sin LLM en el servidor

El equipo tiene Claude Code, Codex y OpenCode corriendo localmente. Cada uno ya
lee el repo completo antes de decidir qué falta. Un LLM en el servidor solo
vería lo que le mandáramos, que es peor que lo que ya tiene el agente.

Además: sin LLM el costo de correr OpenSupport es $0, la latencia es la de
SQLite, y no hay vendor lock-in.

La contrapartida es que el servidor no puede *ayudar* a redactar. Solo puede
**exigir**. Por eso el 80% del diseño está en el contrato del ticket y el
linter, no en la UI.

→ [tickets/linter.py](../app/tickets/linter.py), [app/skill/ticket-authoring.md](../app/skill/ticket-authoring.md)

## 0002 — Monolito, un proceso, un puerto

Una app FastAPI con el MCP montado en `/mcp`. Podríamos haberlos separado, pero
un equipo chico paga el doble de configuración por una frontera que no cruza
nadie. El coste: `/mcp` y `/` comparten proceso y event loop. Por eso todo acceso
a DB desde async pasa por `_run_in_thread` / `db_call`: un `SELECT` bloqueando el
loop de MCP.

## 0003 — SQLite, un solo worker

El board de un equipo son cientos de tickets, no millones. SQLite da backups
filesystem, cero ops, y sobrevive a que nadie lo mire. El límite es un solo
writer, así que el proceso corre con `--workers 1`. No es una limitación
temporal: con más carga el camino es Postgres, y el acceso a datos ya está
aislado en `tickets/repository.py`.

→ [app/db/base.py](../app/db/base.py)

## 0004 — MCP puro, sin REST

Dos contratos para las máquinas es peor que uno. Un agente que habla MCP tiene
schema tipado, structured output, y un resource que puede leer sin instalar
nada. Un endpoint JSON sería una segunda puerta al mismo interior, con reglas de
versionado que nadie va a mantener.

La web sí es HTTP, porque un browser necesita HTTP. Pero **no es un contrato**:
devuelve HTML, se autentica por cookie, no se documenta como API, y puede
cambiar sin aviso. `AGENTS.md` la llama "plumbing de UI".

`/api/v1/*` devuelve 404 explícito, no un 405 confuso.

## 0005 — El skill es un MCP resource

Un `SKILL.md` en un repo es invisible para el agente que está en otro repo. Un
MCP resource no: el agente llama `resources/read` y lo tiene.

Esta es la decisión que hace que la app sea realmente agent-native. La mayoría de
los clientes llaman `resources/list` al conectarse, así que el contrato aparece
solo, sin que nadie se lo pida.

## 0006 — El linter devuelve violaciones, no excepciones

Una excepción MCP hace que el cliente la muestre como error y siga. Una
violación estructurada `{code, field, message, fix}` hace que el agente la lea
y **se corrija en loop**. Es la diferencia entre un agente que se frustra y uno
que itera.

`E_*` bloquean. `W_*` avisan y dejan pasar.

## 0007 — Umbrales altos (12 / 20 / 15)

Un umbral estricto no produce tickets mejores: produce agentes que abandonan el
lote a mitad de camino. El loop del agente no puede trabarse.

Lo que protege la calidad no es el número, son las dos reglas cualitativas que
no dependen del umbral: `E_ACCEPTANCE_VAGUE` y `E_EVIDENCE_MISSING`.

Para recalibrar hay telemetría: cada create rechazado escribe un
`ticket_events` con `kind='lint_failed'` y el código de la violación. Después de
dos semanas de uso real se puede ver la distribución y bajar los números con
datos en vez de con intuición.

## 0008 — Doble atribución: autor y owner

El agente *escribe* el ticket; la persona *responde*. Son dos preguntas
distintas y el board las contesta con dos campos distintos:

- `author_agent_id` — inmutable. Responde "¿qué agente escribe mejor?".
- `owner_user_id` — reasignable. Responde "¿quién tiene esto?".

Con un solo campo, una de las dos preguntas queda sin respuesta.

## 0009 — La revisión es ortogonal al status

El error fácil es meter la revisión en la máquina de estados. No va: son ejes
distintos.

| Eje | Pregunta |
|---|---|
| Status | ¿en qué momento del trabajo está? |
| Revisión | ¿alguien lo miró y qué dice? |

Si la revisión bloqueara, volvería el cuello de botella que se eliminó al hacer
que los tickets nazcan `ready`. La revisión informa; el status manda.

Cada veredicto guarda el `content_hash` del ticket en el momento de la revisión.
Si editás el ticket después, el veredicto viejo queda como histórico y el board
dice *"sin revisar desde el último cambio"* — que es la verdad, y además es lo
que empuja a alguien a mirar.

## 0010 — Token opaco, sin JWT y sin OAuth

La spec de MCP dice *"Authorization is OPTIONAL"* y marca el flujo OAuth 2.1 como
SHOULD. OAuth obligaría a correr un authorization server, con registro dinámico
de clientes, PKCE y consent, para un equipo donde uno mismo genera los tokens.

JWT tampoco: la revocación inmediata requiere una blocklist, que es estado — y
en un solo proceso con SQLite, consultar la DB es de microsegundos. El token
opaco te da revocación instantánea, scopes mutables y `last_used_at` gratis.

La cookie de sesión web es otro mecanismo, con `itsdangerous`. Nunca sirve
contra `/mcp`: cada superficie valida su propia cosa.

→ [app/api/mcp/auth.py](../app/api/mcp/auth.py), [app/auth/tokens.py](../app/auth/tokens.py)

## 0011 — Idempotencia por `external_key`, único por proyecto

Un agente que recorre un repo y se corta a mitad de camino va a reintentar. Sin
una clave estable, el mismo ticket aparece cuatro veces.

`UNIQUE(project_id, external_key)` — por proyecto y no por sesión, para que
relanzar un agente dentro de una sesión no duplique nada.

La clave describe **qué es el problema**, no dónde está el archivo
(`mi-tesis:auth:extraer-jwt`). Si el archivo se mueve, la clave sigue siendo
verdadera.

## 0012 — `create_tickets_batch` es parcial

Si el batch fuera todo-o-nada, un typo en el ítem 7 de 20 tiraría los otros 19.
Con transacciones por ítem, el agente se queda con lo que pudo hacer y recibe
errores accionables por cada rechazo. Es la diferencia entre "el agente
continúa" y "el agente se frena".