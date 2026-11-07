# El contrato de tickets

Este documento **explica** el contrato. La fuente de verdad son el código y
los recursos MCP: `app/tickets/schema.py`, `app/tickets/linter.py`,
`app/domain/transitions.py` y el resource `opensupport://schema/violations`.

Si este documento y el código no coinciden, el código gana: eso se detecta con
`tests/test_protocol.py` y `tests/test_support_intake.py`.

## Dos entradas

`intake_mode` separa **cómo llegó el reporte** de **qué clase de trabajo es**.
No es lo mismo que `type`: un `bug` puede entrar como incidente y conservar esa
identidad mientras se investiga.

| Modalidad | Qué sabe quien reporta | Qué exige | Estado inicial |
|---|---|---|---|
| `implementation` | Leyó el código y sabe qué cambiar | Evidencia de código, pasos, aceptación, scope | `ready` |
| `incident` | Algo pasa; puede no tener el repo | Síntoma y al menos una evidencia observable | `open` |

Omitir `intake_mode` equivale a `implementation`: los clientes históricos no
se rompen.

### `incident` en detalle

El incidente existe porque el soporte no siempre viene con acceso al repositorio.
Exige:

- `project`, `title`, `summary` (qué pasa y por qué importa).
- `support_context.symptom`: qué pasa, en una frase.
- al menos un item de `evidence` con `kind` observable.

No exige `steps`, `acceptance`, `scope`, `estimate` ni diagnóstico. El resultado
esperado es opcional: si no se conoce, `outcome` queda vacío y el linter avisa
(`W_NO_OUTCOME`) pero no bloquea.

`support_context` tiene: `symptom`, `observed`, `expected`, `impact`,
`environment`, `reproduction` y `unknown`. **`unknown` es la lista de lo que
todavía no se sabe**: es la forma correcta de no inventar una causa.

### Del `open` al `ready`

Un incidente nace `open` porque nadie lo investigó todavía. Para promoverlo a
`ready` hace falta:

| Campo | Por qué |
|---|---|
| `outcome` | El resultado observable que se va a lograr |
| `scope.include` | Qué entra: la superficie del cambio |
| `steps` | El plan de diagnóstico o de intervención |

`set_status(code, "ready")` sin eso devuelve `E_PROMOTION_NOT_READY` con la
lista de campos que faltan.

## Tipos de evidencia

| `kind` | Campos | Se usa cuando |
|---|---|---|
| `code` (default) | `path` (relativo), `lines`, `note` | Leíste el repo |
| `observation` | `note` | Viste algo pasar |
| `log` | `note` | Tenés un registro |
| `command` | `command`, `result` | Corriste algo |
| `reference` | `url` | Citás una fuente externa |

Un item sin `kind` se interpreta como `code`: es el contrato histórico.
La evidencia de código exige `path` relativo: sin ruta absoluta ni `..`.

## Estados

```
open ──▶ ready ──▶ in_progress ──▶ done
  │        ▲            │  ▲
  ▼        └────────────┘  │
wont ◀──────────────────────┘
```

- `open`: nobody lo investigó todavía (o se está esperando información).
- `ready`: hay objetivo, alcance y plan. Se puede trabajar.
- `in_progress`: hay un actor trabajando.
- `blocked`: hay un impedimento declarado.
- `done`: **solo** vía `close_ticket`, con resolución y verificación.
- `wont`: descartado **con motivo escrito** (`E_WONT_REASON_REQUIRED`).

Reabrir un `done` lo devuelve a `in_progress` y conserva la resolución anterior
como histórica: no es la solución vigente hasta que haya una verificación nueva.

## Cierre: resolución tipada

```json
{
  "kind": "configuration",
  "summary": "Se corrigió el TTL del cliente móvil.",
  "user_response": "Actualizá la app a la 1.4.1: la sesión dura una hora.",
  "verifications": [
    {"result": "La sesión sigue viva a los 10 minutos", "command": "pytest movil", "ran_at": "2026-01-01T10:00:00Z"}
  ]
}
```

| `kind` | Qué exige |
|---|---|
| `code_fix` | `pr` o `commit` **y** `commands` |
| `configuration` | al menos una verificación con `result` |
| `workaround` | al menos una verificación con `result` |
| `guidance` | al menos una verificación con `result` |

`user_response` es obligatorio en el contrato nuevo: es lo que el usuario va a
leer y queda guardado en `tickets.verification`.

El contrato viejo (`pr=`, `commit=`, `commands=`, `notes=`) sigue funcionando:
se normaliza a una resolución `code_fix`.

`done` no significa "no investigar", "no se reprodujo" ni "esperando
información". Para esperar información, el ticket queda en `open` y se pide en un
comentario.

## Seguimiento recuperable

- `get_ticket(code, project=)` — detalle, revisión, contexto de soporte,
  resolución y `workflow`.
- `get_ticket_activity(code, project=)` — comentarios y eventos en una línea de
  tiempo, con autor y fecha. Es lo que permite a un agente nuevo retomar el
  caso.
- `update_ticket(code, patch, expected_updated_at=)` — el parche se valida
  contra el **resultado combinado**: si lo que mandás rompe el contrato, no se
  guarda nada, no cambia el hash y no queda evento.

## Identidad

El código de un ticket (`OS-001`) es único **por proyecto**. Toda tool que
reciba un código acepta `project=` para desambiguar. Sin él y con más de una
coincidencia, el server devuelve `E_AMBIGUOUS_TICKET` en vez de elegir por vos.

Los vínculos entre tickets se resuelven **dentro del proyecto** del origen: un
`OS-001` de otro proyecto no es el destino (`E_TICKET_OTHER_PROJECT`).

## Permisos

| Operación | Quién puede |
|---|---|
| Leer | Cualquiera con un token válido |
| Crear | Solo en los proyectos del `project_scope` del token |
| Modificar, comentar, revisar, vincular | Owner, agente autor o admin, **y** dentro del alcance del token |

El alcance se verifica en el **servicio**, no en el adaptador: un token fuera de
alcance no escribe ni deja evento, aunque sea el owner del ticket.

## Violaciones

`E_*` bloquean. `W_*` avisan. La tabla completa, con el `fix` de cada una, está
en `opensupport://schema/violations` y en `_violation_table()`.

| Código | Qué significa |
|---|---|
| `E_SUPPORT_CONTEXT_MISSING` | El incidente no dice qué pasa |
| `E_PROMOTION_NOT_READY` | Faltan campos para pasar a `ready` |
| `E_WONT_REASON_REQUIRED` | Se descartó sin motivo |
| `E_RESOLUTION_INCOMPLETE` | Falta la respuesta al usuario o la forma |
| `E_VERIFICATION_INCOMPLETE` | No hay evidencia de la solución |
| `E_TICKET_OTHER_PROJECT` | El destino está en otro proyecto |
| `E_INVALID_DRAFT` | El item del lote no tiene forma de borrador |

## Idempotencia

`external_key` es estable **por reporte y proyecto**. Un reintento con la misma
clave devuelve el ticket existente en vez de duplicarlo. La clave describe el
problema, no la ubicación del archivo: `mi-tesis:auth:extraer-jwt`.
