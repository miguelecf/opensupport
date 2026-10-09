# CONTEXT.md

Glosario de dominio de OpenSupport. Para las reglas de trabajo, `AGENTS.md`.

## Actores

- **Persona** (`users`) — un humano del equipo. Es quien *responde* por los tickets.
- **Agente** (`agents`) — un cliente MCP: Claude Code, Codex, OpenCode. Redacta tickets.
- **Token** (`api_tokens`) — la credencial. Pertenece a una persona, opcionalmente
  a un agente. Es opaco: en la DB solo vive su sha256.

## Objetos

- **Proyecto** (`projects`) — metadata. Un repo, un trabajo, un side project.
  El server **nunca lee el repo**: solo guarda nombre, descripción y convenciones
  que el equipo declara.
- **Sesión** (`sessions`, `PLN-002`) — una sesión de planificación sobre **un tema**
  dentro de un proyecto. Es contexto compartido: los agentes entran, leen lo que ya
  se escribió y aportan. Varias sesiones conviven sobre el mismo proyecto.
- **Ticket** (`tickets`, `OS-014`) — una unidad de trabajo con evidencia verificable.
- **Link** (`ticket_links`) — relación tipada entre dos tickets.
- **Revisión** (`ticket_reviews`) — veredicto de alguien sobre un ticket.
- **Comentario** (`comments`) — conversación.
- **Evento** (`ticket_events`) — auditoría append-only. Nunca se actualiza.

## Doble atribución

Un ticket tiene **dos** identidades y no son la misma:

- **`author_agent_id`** — quién lo *escribió*. Inmutable.
- **`owner_user_id`** — quién *responde*. Reasignable con `assign_ticket`.

Sirve para responder dos preguntas distintas: *"¿qué está escribiendo mal el
agente?"* (filtrar por autor) y *"¿ya lo vio Fede?"* (agrupar por owner).

## Estados

```
ready ──▶ in_progress ──▶ done
  │            │  ▲
  ▼            ▼  │
open        blocked
```

- El ticket **nace `ready`**: ejecutable desde el minuto uno. No hay gate humano,
  porque en un equipo chico el gate es un cuello de botella.
- `open` es backlog: un humano lo degrada cuando no quiere que se toque todavía.
- `done` **solo** vía `close_ticket`, que exige PR o commit y un comando de verificación.
- `wont` es "descartado, con razón".

## Revisión, que es OTRO eje

La revisión **no** es un estado. Responde "¿alguien lo miró?", no "¿en qué
momento del trabajo está?".

| Eje | Pregunta | Dónde vive |
|---|---|---|
| Status | ¿en qué momento del trabajo está? | `tickets.status` |
| Revisión | ¿alguien lo miró y qué dice? | `ticket_reviews.verdict` |

Cada veredicto se guarda contra el `content_hash` del ticket **en el momento de
la revisión**. Si después editás el ticket, el veredicto viejo queda histórico y
el board dice *"sin revisar desde el último cambio"*. Así el badge nunca miente
sobre algo que ya se corrigió.

## Visibilidad

**El equipo lee en compartido; la escritura es privada.**

| Operación | Quién puede |
|---|---|
| Leer cualquier ticket | Cualquiera con un token válido |
| Modificar | El owner, el agente autor, o un admin |
| Comentar y revisar | Cualquiera |
| Crear tickets | Solo en los proyectos del `project_scope` del token |

## Vocabulario de las violaciones

`E_*` **bloquean**: el ticket no se crea. `W_*` avisan: se crea igual.

| Prefijo | Significado |
|---|---|
| `E_TITLE_MISSING` | Título vacío o demasiado largo |
| `E_SUMMARY_MISSING` | Summary ausente o demasiado corto |
| `E_OUTCOME_MISSING` | El outcome no describe un cambio observable |
| `E_EVIDENCE_MISSING` / `MALFORMED` | No cita un `path:line` real |
| `E_STEPS_MISSING` / `TOO_MANY` / `VAGUE` | Pasos ausentes, demasiados o sin verbo |
| `E_ACCEPTANCE_MISSING` / `VAGUE` / `TOO_MANY` | Criterios no comprobables |
| `E_SCOPE_EMPTY` / `TOO_BIG` | El scope no delimita nada, o hace demasiado |
| `W_NO_EXCLUDE` / `W_NO_RISKS` | Falta pensar qué queda afuera / qué se rompe |
| `E_PROJECT_OUT_OF_SCOPE` | El token no tiene ese proyecto |
| `E_NOT_YOUR_TICKET` | No sos owner ni autor |
| `E_CONFLICT` | Otro agente cambió el ticket |
| `E_CLOSED_NEEDS_EVIDENCE` | Intentó setear `done` directamente |

## Reglas de oro

1. El **server no piensa**: exige. Su valor está en el contrato, no en la UI.
2. **Idempotencia por `external_key`**, único por proyecto. Un agente que se corta
   a la mitad y vuelve no duplica nada.
3. **`create_tickets_batch` es parcial** a propósito: un item malo no tira los buenos.
4. **Un agente lee antes de escribir**: `join_session` es lo que evita que dos
   agentes propongan lo mismo.