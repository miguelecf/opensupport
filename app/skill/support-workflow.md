# Ciclo de soporte en OpenSupport

Este es el recorrido completo, desde "algo pasa" hasta "quedó resuelto y el
usuario tiene la respuesta". OpenSupport no ejecuta tu proyecto ni lee tus
repositorios: vos lo ejecutás en tu entorno autorizado y OpenSupport guarda
lo que declarás.

## 0. Ubicarte

`list_projects()` te dice qué proyectos existen, cómo se llaman y si tu token
puede escribir en ellos. `get_project(slug)` da el detalle de uno.

El código de un ticket (`OS-001`) es único **por proyecto**. Sin el proyecto
no se puede elegir: vas a recibir `E_AMBIGUOUS_TICKET` con la lista.

## 1. Consultar

- `search_tickets(query, project=...)` busca por texto y devuelve el proyecto
  de cada hit.
- `list_tickets(project=..., status=..., ...)` lista con filtros.
- `get_ticket(code, project=...)` devuelve el detalle, la revisión y el
  `workflow`: qué transiciones podés hacer, qué falta y qué puede hacer tu
  token.
- `get_ticket_activity(code, project=...)` devuelve comentarios y eventos en
  una línea de tiempo. Es lo que usás para retomar un caso sin leer el chat
  anterior.

## 2. Reportar: dos entradas

### `intake_mode: "incident"` — no tenés el repo

Para soporte sin acceso al código. El ticket nace **`open`**: nadie
investigó todavía.

Exige:

- `project`, `title`, `summary` (qué hace y por qué).
- `support_context.symptom`: qué pasa, en una frase.
- al menos una evidencia observable, con `kind`:
  - `observation`: lo que viste.
  - `log`: el registro.
  - `command`: el comando ejecutado (`command` + `result`).
  - `reference`: una fuente externa (`url`).
- `support_context` puede llevar `observed`, `expected` (si lo sabés),
  `impact`, `environment`, `reproduction` y `unknown` (lo que todavía no
  sabés).

No exige archivos, ni pasos de implementación, ni criterios de aceptación, ni
diagnóstico. **No inventes una causa que no conocés**: `unknown` es una
respuesta válida.

### `intake_mode: "implementation"` — ya investigaste

El contrato completo: `evidence` con `kind: "code"` y `path` relativo,
`steps`, `acceptance` y `scope`. El ticket nace **`ready`**.

## 3. Investigar y completar

`update_ticket(code, patch, expected_updated_at=...)` agrega lo que encontraste.
El parche se valida contra el resultado combinado: si lo que mandás rompe el
contrato, no se guarda nada y te vuelve la lista de lo que falta.

Para promover un incidente de `open` a `ready` hacen falta:

- `outcome`: el resultado observable esperado.
- `scope.include`: qué entra.
- `steps`: el plan de diagnóstico o de intervención.

`set_status(code, "ready")` te devuelve `E_PROMOTION_NOT_READY` con los
campos que faltan si todavía no es el momento.

## 4. Resolver

1. `set_status(code, "in_progress")`.
2. `close_ticket(code, resolution={...})` con:

```json
{
  "kind": "code_fix",
  "summary": "Qué se cambió, en dos líneas.",
  "user_response": "Qué tiene que hacer el usuario y qué pasó.",
  "pr": "https://github.com/org/repo/pull/12",
  "commands": ["uv run pytest"],
  "verifications": [{"result": "226 tests en verde", "command": "uv run pytest", "ran_at": "2026-01-01T10:00:00Z"}]
}
```

- `kind: "code_fix"` exige `pr` o `commit` **y** `commands`.
- `kind: "configuration"`, `"workaround"` o `"guidance"` no exigen commit:
  exigen al menos una verificación con `result` observable. Un ticket de
  soporte no se arregla con código, y no por eso queda sin cerrar.
- `user_response` es obligatorio: es lo que el usuario va a leer.

## 5. Cerrar el loop

Un ticket que no está resuelto no se marca `done`. Si no se va a trabajar, se
descarta con `set_status(code, "wont", note="...")` y el motivo escrito. Si
falta información, se deja en `open` y se pide en un comentario: eso no es lo
mismo que "listo para implementar".

Al reabrir un ticket cerrado, la resolución anterior queda como historia: no
es la solución vigente hasta que haya una verificación nueva.

## Errores frecuentes

| Código | Qué pasó | Qué hacer |
|---|---|---|
| `E_AMBIGUOUS_TICKET` | el código existe en varios proyectos | pasá `project=` |
| `E_PROJECT_OUT_OF_SCOPE` | tu token no escribe en ese proyecto | pedí alcance o usá otro proyecto |
| `E_SUPPORT_CONTEXT_MISSING` | el incidente no dice qué pasa | escribí el síntoma |
| `E_PROMOTION_NOT_READY` | faltan campos para pasar a `ready` | completá outcome, scope y steps |
| `E_RESOLUTION_INCOMPLETE` | el cierre no tiene respuesta al usuario | escribí `user_response` |
| `E_VERIFICATION_INCOMPLETE` | no hay evidencia de la solución | agregá PR/comando o una verificación observable |
| `E_CONFLICT` | otro agente cambió el ticket | releé con `get_ticket` y reintentá |
