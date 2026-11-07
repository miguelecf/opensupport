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
- **Modalidad de ingreso** (`tickets.intake_mode`) — cómo llegó el reporte:
  `incident` (alguien vio algo pasar) o `implementation` (ya se sabe qué
  cambiar). Es un eje distinto del `type`.
- **Contexto de soporte** (`tickets.support_context`) — síntoma, lo observado,
  entorno, reproducción y **lo que todavía no se sabe**. Sin causa ni diagnóstico.
- **Resolución** (`tickets.verification`) — clase de solución, resumen, respuesta
  para el usuario y verificaciones observables.
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
open ──▶ ready ──▶ in_progress ──▶ done
  ▲        │            │  ▲
  └────────┴────────────┘  │
wont ◀──────────────────────┘
```

- El ticket **nace `ready`** si entra como `implementation`: ejecutable desde el
  minuto uno. No hay gate humano, porque en un equipo chico el gate es un cuello
  de botella.
- El ticket **nace `open`** si entra como `incident`: nadie lo investigó todavía.
- `open` también es backlog: un humano lo degrada cuando no quiere que se toque.
- De `open` a `ready` hace falta objetivo, alcance y plan (`E_PROMOTION_NOT_READY`).
- `done` **solo** vía `close_ticket`, que exige la resolución y su verificación.
- `wont` es "descartado, con razón": el motivo es obligatorio.

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
| Comentar y revisar | Cualquiera, dentro del alcance del token |
| Crear tickets | Solo en los proyectos del `project_scope` del token |
| Vincular | Dentro del proyecto del ticket de origen |

El alcance del token se verifica **en el servicio**: un token fuera de alcance
no escribe ni deja evento, aunque sea el owner del ticket.

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
| `E_SUPPORT_CONTEXT_MISSING` | El incidente no dice qué pasa |
| `E_PROMOTION_NOT_READY` | Faltan campos para pasar a `ready` |
| `E_WONT_REASON_REQUIRED` | Se descartó sin motivo |
| `E_RESOLUTION_INCOMPLETE` | Falta la respuesta al usuario |
| `W_NO_EXCLUDE` / `W_NO_RISKS` | Falta pensar qué queda afuera / qué se rompe |
| `E_PROJECT_OUT_OF_SCOPE` | El token no tiene ese proyecto |
| `E_NOT_YOUR_TICKET` | No sos owner ni autor |
| `E_CONFLICT` | Otro agente cambió el ticket |
| `E_CLOSED_NEEDS_EVIDENCE` | Intentó setear `done` directamente |
| `E_TICKET_OTHER_PROJECT` | El destino del vínculo está en otro proyecto |

## Reglas de oro

1. El **server no piensa**: exige. Su valor está en el contrato, no en la UI.
2. **Idempotencia por `external_key`**, único por proyecto. Un agente que se corta
   a la mitad y vuelve no duplica nada.
3. **`create_tickets_batch` es parcial** a propósito: un item malo no tira los buenos.
4. **Un agente lee antes de escribir**: `join_session` y `get_ticket_activity` son
   lo que evita que dos agentes propongan lo mismo o que un agente nuevo
   reinicie el análisis desde cero.
5. **Ninguna soporte exige un documento**: el reporte vive en OpenSupport, por
   MCP. Los documentos de implementación son opcionales, no un requisito.