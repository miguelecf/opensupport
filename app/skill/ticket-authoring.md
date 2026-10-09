---
name: ticket-authoring
description: Cómo redactar tickets para OpenSupport. Leé esto antes de llamar a lint_ticket o create_ticket.
version: 1
---

# Ticket authoring para OpenSupport

OpenSupport no tiene un modelo de lenguaje adentro. Eso significa que **no te va a
ayudar a redactar**: te va a *rechazar* los tickets que no alcanzan a un humano.
Este documento es el contrato. Si lo seguís, el primer `lint_ticket` pasa.

## Cuándo usar esto

Cada vez que vayas a crear un ticket en OpenSupport: lo generaste recorriendo un
repo, entendiste un problema, y querés dejarlo registrado para que otra persona
(o vos mismo en dos semanas) pueda ejecutarlo.

No lo uses para: guardar una idea suelta, una duda, o una tarea de cinco minutos.

## El loop correcto

```text
resources/read opensupport://skill/ticket-authoring   ← este documento
search_tickets("lo que estás buscando")                ← ¿ya existe?
lint_ticket(borrador)                                  ← no persiste nada
  └─ si hay violaciones: corregí y repetí
create_ticket(borrador) o create_tickets_batch([...])  ← recién acá persiste
```

**No saltees el `lint_ticket`.** Crear un ticket con violaciones lo rechaza igual,
pero el error llega tarde y en lote es más difícil de corregir. Lintear primero
es lo mismo de trabajo con el feedback en el momento.

## Anatomía de un ticket aceptable

| Campo | Qué exige el linter |
|---|---|
| `title` | Hasta 120 caracteres. Verbo + objeto + dónde. |
| `summary` | Mínimo 80 caracteres. QUÉ hace y POR QUÉ. |
| `outcome` | Mínimo 40 caracteres. El cambio **observable**, no la tarea. |
| `evidence` | Al menos un `path:line` real que leíste. Path relativo al repo. |
| `steps` | Entre 3 y 12. Cada uno arranca con un verbo. |
| `acceptance` | 1 a 20, cada uno **comprobable**. |
| `scope.include` | Qué entra. 1 a 15 items. |
| `scope.exclude` | Qué queda afuera. Warning si falta. |
| `risks` | Qué puede romperse. Warning si falta. |
| `external_key` | Estable por **qué es el problema**, no por dónde está el archivo. Ej: `mi-tesis:auth:extraer-jwt`. |

## Ejemplo completo BUENO

```json
{
  "project": "mi-tesis",
  "external_key": "mi-tesis:auth:extraer-jwt",
  "title": "Extraer la verificación de JWT a un módulo testeable",
  "summary": "La verificación de JWT vive inline dentro del endpoint /login y no se puede testear sin levantar toda la app. Moverla a auth/verify.py permite probarla como función pura y agrega tests unitarios que hoy no existen.",
  "problem": "El test del endpoint necesita TestClient y base de datos real; hay tres tests que podrían ser asserts sobre una función.",
  "outcome": "auth.verify_token(token, secret, leeway) queda como función pura con tests unitarios propios, y /login la llama sin cambiar el comportamiento observable.",
  "type": "refactor",
  "priority": "p2",
  "evidence": [
    { "path": "backend/app/api/login.py", "lines": "42-88", "note": "verificación inline dentro del handler" },
    { "path": "backend/tests/api/test_login.py", "lines": "10-60", "note": "tres tests que requieren TestClient" }
  ],
  "steps": [
    "Crear backend/app/auth/verify.py con verify_token(token, secret, leeway=0).",
    "Mover la lógica actual de login.py líneas 42-88 sin cambiar semántica.",
    "Pasar secret como parámetro en vez de importarlo como global.",
    "Agregar backend/tests/auth/test_verify.py con los casos válido, expirado, firma inválida y malformado.",
    "Delegar desde /login y borrar el bloque inline."
  ],
  "acceptance": [
    "El comando `uv run pytest backend/tests/auth` termina con todos los tests en verde.",
    "El módulo auth.verify_token no importa nada de app.api ni de app.db.",
    "GET /login con un token expirado devuelve el mismo status y el mismo body que antes del cambio."
  ],
  "scope": {
    "include": ["extracción del módulo de verificación", "tests unitarios nuevos"],
    "exclude": ["cambio del formato del token", "rotación de secrets", "cambios en el frontend"]
  },
  "risks": ["cambiar el orden de validación alteraría el error que devuelve /login"],
  "tags": ["auth", "testabilidad"],
  "estimate": "s"
}
```

## Ejemplo RECHAZADO, y por qué

```json
{
  "title": "Arreglar el login",
  "summary": "El login anda mal y hay que arreglarlo.",
  "outcome": "El login funciona bien.",
  "acceptance": ["Funciona correctamente"]
}
```

| Violación | Por qué |
|---|---|
| `E_SUMMARY_MISSING` | 41 caracteres, mínimo 80. No dice qué ni por qué. |
| `E_OUTCOME_MISSING` | 24 caracteres, mínimo 40. "Funciona bien" no es un cambio observable. |
| `E_STEPS_MISSING` | No hay pasos. Nadie sabe qué hacer. |
| `E_ACCEPTANCE_VAGUE` | "Funciona correctamente" no se puede comprobar. |
| `W_NO_EXCLUDE` · `W_NO_RISKS` | No bloquearon, pero delatan que no pensaste el cambio. |

La versión arreglada es exactamente el ejemplo de arriba.

## Los 10 errores más comunes

1. **`acceptance` con frases vagas.** "Funciona", "anda bien", "no falla", "should work". Un criterio se comprueba o no es un criterio.
2. **`summary` que repite el título.** El título dice *qué*; el summary dice *por qué*.
3. **`outcome` que repite la tarea.** "Arreglar el login" no es un resultado; "el login deja de pedir la DB por request" sí.
4. **Cero `evidence`.** Si no citás un archivo, nadie puede verificar que leíste nada.
5. **Paths absolutos en `evidence`.** El server nunca ve el repo: necesita paths relativos.
6. **Un ticket que hace tres cosas.** Si tenés 12 pasos, son dos tickets. Dividí.
7. **`steps` que no arrancan con verbo.** "Revisar que ande" no es un paso; "Agregar el test que cubre el caso X" sí.
8. **`external_key` con la ruta adentro.** Si el archivo se mueve, la clave miente y perdés la idempotencia.
9. **`scope.exclude` vacío.** Lo que no decís que queda afuera, lo termina tocando alguien.
10. **Crear en lote sin lintear.** `create_tickets_batch` es parcial a propósito: salvá los 18 buenos, pero solo si los 2 malos venían con sus violaciones claras.

## Checklist antes de postear

- [ ] ¿Un humano que no me conoce puede ejecutar esto sin preguntarme nada?
- [ ] ¿Cada criterio de aceptación se puede comprobar con un comando o una lectura de un archivo?
- [ ] ¿Cité al menos un archivo que efectivamente leí?
- [ ] ¿El `external_key` describe el problema, no la ubicación?
- [ ] ¿Dije qué queda afuera?
- [ ] ¿Corrí `lint_ticket` y dio `ok: true`?

## Reportar progreso y cerrar

```text
set_status(code, "in_progress")     ← empezaste
... implementás ...
close_ticket(code, pr="https://...", commands=["uv run pytest backend/tests/auth"])
```

`done` **no** se puede setear con `set_status`. `close_ticket` exige `pr` o `commit`
y al menos un comando de verificación. Si intentás `set_status(code, "done")`
recibís `E_CLOSED_NEEDS_EVIDENCE`.

## Revisión

`review_ticket(code, "approved")` o `review_ticket(code, "changes_requested", note="...")`.
`changes_requested` **exige** la nota: pedir cambios sin decir cuáles no es una
opinión, es ruido.

La revisión **no cambia el estado** del ticket. Es un eje aparte: el status dice
en qué momento del trabajo está, la revisión dice si alguien lo miró.

Cada veredicto se guarda contra la versión del ticket en el momento de la revisión.
Si después editás el ticket, el veredicto viejo queda como histórico y el board
muestra "sin revisar desde el último cambio". No hace falta borrar nada.

## Sesiones de planificación

Si varias personas y varios agentes están trabajando sobre el mismo tema:

```text
start_session(project="mi-tesis", title="Auth: migrar a JWT", goal="...")
join_session("PLN-002")     ← leé el contexto antes de aportar
create_tickets_batch([...], session="PLN-002")
```

`join_session` te devuelve el objetivo, los participantes y **los tickets que ya
escribieron otros**. Es la única forma de no duplicar lo que otro agente ya posteó
en la misma sesión. Es idempotente: llamalo todas las veces que quieras.

Una sesión cerrada no acepta tickets nuevos (`E_SESSION_CLOSED`), pero `join_session`
sigue respondiendo en modo lectura para que puedas ver de qué se trataba.

## Respuestas a las violaciones

| Código | Qué hacer |
|---|---|
| `E_ACCEPTANCE_VAGUE` | Reformulá: "GET /login con token expirado devuelve 401", no "funciona bien". |
| `E_STEPS_TOO_MANY` | Dividí el ticket y creá los hijos con `external_key` propios. |
| `E_SCOPE_TOO_BIG` | Igual: menos de 15 items de include. |
| `E_EVIDENCE_MISSING` | Salí a leer el código y citá `path:a-b`. |
| `E_PROJECT_OUT_OF_SCOPE` | Te conectaste al board equivocado. El `fix` lista los proyectos permitidos. |
| `E_CONFLICT` | Otro agente cambió el ticket. Releelo con `get_ticket` y reintentá con el `updated_at` nuevo. |
| `E_NOT_YOUR_TICKET` | No sos el owner. Pedí que te lo reasignen con `assign_ticket`. |
| `E_CLOSED_NEEDS_EVIDENCE` | Usá `close_ticket` con `pr` o `commit` y un comando. |
| `E_REVIEW_NOTE_REQUIRED` | Escribí qué hay que cambiar. |