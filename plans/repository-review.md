# Revisión integral y próximos pasos de OpenSupport

## Contexto

El objetivo es leer el repositorio completo, identificar capacidades existentes y proponer funcionalidades y seguimientos priorizados con evidencia del código. Se solicita contrastar el análisis con instancias de Pi mediante Orca, usando MiniMax y DeepSeek del proveedor OpenCode Go.

La fase actual es exclusivamente de planificación: no modificar producto, instalar dependencias, crear commits ni escribir tickets en un servidor. Solo se permiten documentos Markdown dentro del repositorio.

## Objetivo de producto aclarado

El usuario busca que OpenSupport indique a cualquier agente, de forma estructurada, cómo presentar tickets de soporte del proyecto donde trabaja o de otro proyecto. La revisión no debe convertirse en una lista genérica de funcionalidades: debe evaluar el recorrido de descubrimiento del contrato, identificación del proyecto, recopilación de información, validación, publicación y seguimiento.

El servidor debe enseñar y exigir el contrato de publicación, sin analizar repositorios ni incorporar un LLM. El agente externo aporta el análisis.

El usuario amplió el objetivo: OpenSupport debe ser primero nativo para agentes y ofrecer un camino preciso hasta la resolución, conservando investigación, hallazgos, progreso y respuesta al usuario dentro del sistema mediante MCP, sin documentos Markdown ni commits como mecanismo de soporte. Ejemplo esperado: «Andá al proyecto X, decime qué tickets tenemos; luego ejecutemos el proyecto y registremos como bug lo que encontremos». El agente ejecuta el proyecto en su entorno autorizado; OpenSupport no lo ejecuta ni puede garantizar que el usuario lea la respuesta. No se incorpora un LLM en esta propuesta; el usuario no descarta esa posibilidad futura.

Decisión confirmada: los incidentes ingresan como `open` y pasan a `ready` al tener contexto suficiente para actuar. Los hallazgos ya investigados pueden ingresar como `ready`. Esto requiere actualizar explícitamente el contrato normativo actual de `AGENTS.md` y `CONTEXT.md`, que hoy establece que todo ticket nace `ready`.

La lectura de `app/skill/ticket-authoring.md` confirma que ya existe un contrato detallado y un flujo de búsqueda, validación y creación. El usuario confirmó que el producto debe cubrir ambos casos: hallazgos con acceso al repositorio e incidentes sin acceso al código. El contrato actual presupone lectura de código, evidencia `path:line` y pasos de implementación; se debe estudiar cómo admitir síntomas, reproducción y registros sin obligar al agente a inventar evidencia, causa ni solución.

## Enfoque recomendado

Evolucionar el contrato y los servicios existentes, sin reemplazar la aplicación ni agregar un orquestador de agentes. Priorizar un recorrido completo por MCP antes que más pantallas: descubrir proyecto, consultar contexto, registrar evidencia, investigar, actuar, verificar y comunicar la resolución.

Separar tres ejes: `type` describe la naturaleza del trabajo; `intake_mode` identifica si se recibió un incidente o una propuesta investigada; `status` expresa el avance. No añadir `incident` a `TicketType`: un incidente puede ser un `bug` y luego conservar esa identidad durante su resolución.

La guía debe tener dos formas coherentes: instrucciones breves descubribles en MCP y datos estructurados calculados por el servicio para el ticket actual. Un texto estático por sí solo no satisface el objetivo.

## Hallazgos iniciales

- Arquitectura: FastAPI + FastMCP, servicios compartidos, SQLite con WAL y FTS5, interfaz Jinja2 + HTMX.
- El contrato excluye LLM en el servidor, lectura o ejecución de repositorios, API REST y múltiples equipos aislados en una misma instancia.
- Ya existen sesiones de planificación, creación de tickets por lotes, exportación, estadísticas del linter y comandos de respaldo/restauración. Deben revisarse antes de proponer ampliaciones.
- `git status --short` no muestra cambios al comienzo de la revisión.
- `README.md` y `AGENTS.md` enlazan `docs/ticket-contract.md`, que no existe. El contrato operativo está repartido entre el skill, los modelos y el linter; hace falta documentar sus dos recorridos sin introducir otra fuente divergente de reglas.

## Evidencia del contrato y brechas confirmadas por lectura

- `app/tickets/schema.py:EvidenceItem`: la evidencia solo admite `path`, `lines` y `note`. No modela síntomas, registros o referencias externas de un incidente.
- `app/tickets/linter.py:_check_evidence`, `_check_steps` y `_check_scope`: todos los tickets requieren archivos y pasos de implementación, incluso los de tipo `bug`. El tipo no cambia las reglas.
- `app/tickets/service.py:create_ticket`: todo ticket nace `ready`. Esto confunde un incidente bien reportado con una tarea ya investigada y lista para implementar.
- `app/tickets/service.py:close_ticket`: exige PR o commit y comandos. Falta estudiar cierres válidos sin cambios de código, manteniendo evidencia verificable.
- `app/tickets/service.py:update_ticket`: modifica campos sin volver a pasar el linter. La calidad exigida en creación no está garantizada durante el seguimiento.
- `app/api/mcp/server.py:get_ticket`: anuncia historial, pero la respuesta no incluye comentarios ni eventos; el agente puede escribir comentarios sin recuperar la conversación mediante esta herramienta.
- `app/api/mcp/server.py:search_tickets`: no permite filtrar por proyecto y omite el proyecto en los resultados, aunque los códigos se repiten entre proyectos.
- El descubrimiento ya existe mediante instrucciones de inicialización, recursos planos, `get_schema` y referencias al skill desde varias herramientas. Hay que ampliarlo, no sustituirlo.
- `opensupport://meta` enumera proyectos, pero no sus descripciones ni el alcance de escritura del token. La tabla de violaciones menciona `list_projects`, que no existe como herramienta en el servidor leído.

## Revisiones externas solicitadas

- Autenticación comprobada mediante `pi auth check --provider opencode-go --no-refresh --json`: disponible, sin mostrar credenciales.
- Modelos encontrados: `opencode-go/minimax-m3` y `opencode-go/deepseek-v4-pro`.
- Limitación comprobada: `orca agent launch --agent pi --model ...` devuelve `invalid_argument`: Pi no admite selección de modelo en ese mecanismo de lanzamiento.
- Se iniciaron consultas puntuales mediante terminales Orca con Pi en modo de impresión, sin persistencia de sesión, sin extensiones ni MCP, y herramientas limitadas a `read,grep,find,ls`. No hay seguimiento estructurado de trabajador.
- MiniMax, contrato y descubrimiento: `term_7db21876-0826-469f-b2a7-6d79f3b7bcd5`.
- DeepSeek, ciclo de soporte y persistencia: `term_ec47130a-dfdc-46ec-9a32-01d50bb79c44`.
- Ambas consultas finalizaron y sus respuestas completas se recuperaron con lecturas por cursor de las terminales. Coinciden en las brechas del contrato único, el estado inicial, el cierre restringido a código y la conversación no recuperable por MCP. No se ejecutaron pruebas.
- Se descartan como conclusiones verificadas los recuentos, números de línea y afirmaciones excesivas de los informes: por ejemplo, `lines` no es obligatorio, un comportamiento esperado puede conocerse sin causa diagnosticada y el servidor no puede detectar por sí solo una causa inventada.
- Recomendación de diseño a consolidar: separar modalidad de reporte de `type`; un `bug` puede originarse con o sin acceso al repositorio. No convertir automáticamente todos los reportes sin código en una categoría técnica distinta.

## Hallazgos adicionales de seguimiento y seguridad

- `app/api/web/views.py`: las rutas async hacen acceso síncrono a DB; contradicen la regla de no bloquear el bucle de eventos. Afecta la convivencia web/MCP.
- `app/templates/base.html` inyecta CSRF solo en eventos HTMX, mientras los formularios de mutación son HTML normal: los tests envían el encabezado manualmente y no cubren el uso real de esos formularios.
- Las mutaciones web no propagan `project` al servicio y el detalle usa el objeto `ticket.project` en varias URLs en lugar de su `slug`: riesgo de acciones fallidas o ambiguas entre proyectos.
- El modelo `Comment` no define las relaciones `author_user`/`author_agent` que usa `ticket.html`; la conversación humana necesita cobertura con comentarios reales.
- `app/api/mcp/auth.py`: no conserva `token_id` al reconstruir el principal, y varias herramientas llaman `require_write` dos veces. El límite declarado por token termina identificado por usuario y puede consumir dos unidades por llamada.
- `_load_mutable`, `add_comment`, `review_ticket`, `link_tickets` y el cierre de sesiones no verifican de forma uniforme el alcance de proyecto de escritura; el permiso debe comprobarse en servicios y no solo confiar en el adaptador.
- Los fragmentos FTS se renderizan con `safe`, y algunas respuestas HTML interpolan datos sin autoescape. Se deben tratar los tickets y registros como entrada no confiable.
- `update_ticket` y `close_ticket` necesitan validación atómica antes de modificar persistencia y registrar eventos; el hash debe incluir toda información sustantiva nueva.

## Recorrido objetivo

1. **Ubicarse:** el agente descubre proyectos por nombre o slug, descripción y permisos efectivos. No deduce el proyecto por el directorio local ni lo crea automáticamente.
2. **Consultar:** lista y busca tickets del proyecto; obtiene detalle, conversación, evidencia y estado con identidad inequívoca `(project, code)`.
3. **Reproducir externamente:** con autorización del usuario, ejecuta el proyecto o sigue la reproducción en su entorno y conserva comando, resultado, fecha y entorno. OpenSupport no ejecuta nada ni descarga referencias.
4. **Registrar:** busca duplicados y publica un incidente `open` con síntomas y evidencia, o una propuesta investigada `ready` con un plan verificable. La validación devuelve correcciones concretas.
5. **Investigar:** agrega hallazgos y preguntas al mismo ticket; distingue observaciones de hipótesis y registra lo desconocido. Otro agente puede retomar sin leer el chat anterior.
6. **Preparar y actuar:** el servicio indica qué falta para pasar a `ready`; un actor autorizado completa el contexto y empieza la resolución mediante `in_progress`. Si falta información, se documenta sin inventarla.
7. **Resolver:** registra solución aplicada, comprobaciones con resultados y respuesta para el usuario. Cierra con evidencia apropiada al tipo de resolución; si no está resuelto, no se marca `done`.
8. **Retomar o reabrir:** un agente nuevo lee el historial y continúa; una regresión permite reapertura conservando la resolución anterior como histórica.

Ningún paso del soporte exige crear un archivo Markdown o subir un documento a Git. Los documentos internos del producto y el export opcional pueden seguir existiendo.

## Iniciativas priorizadas

Esfuerzo relativo: S = acotado; M = varios componentes; L = cambio transversal. No son estimaciones de calendario. El orden considera seguridad y dependencias.

### 0. Corregir la base que sostiene el recorrido — P0 · L

- Propagar `(project, code)` en lecturas, mutaciones, enlaces, formularios y redirecciones. Resolver el destino de vínculos dentro del proyecto explícito; no elegir un ticket homónimo de otro proyecto.
- Aplicar permisos de escritura y alcance de proyecto en los servicios para cada mutación. Mantener lectura compartida y comentarios/revisiones por miembros autorizados, sin quitar la distinción entre opinar y modificar contenido.
- Preservar `token_id` en autenticación y consumir el límite una sola vez por operación. Revisar el caso de límite cero para no indexar una ventana vacía en `RateLimiter._evict`.
- Ejecutar el acceso a DB web y de readiness en un hilo. Mantener un solo proceso y la arquitectura síncrona de persistencia.
- Corregir formularios con HTMX para enviar el encabezado CSRF existente, mostrar errores del servicio y conservar el proyecto; probar su envío real. No desactivar la protección para hacerlos funcionar.
- Escapar contenido de búsquedas y respuestas HTML; no devolver tokens nuevos en URLs ni permitir revocación de credenciales ajenas sin autorización.
- Validar actualizaciones antes de mutar. Corregir el lote parcial para aislar también errores estructurales y fallos de dependencias por elemento: `_draft_from_dict` hoy procesa todo antes del servicio y el rollback interno puede invalidar el aislamiento del lote.

**Aceptación:** dos proyectos con `OS-001` no se confunden; tokens fuera de alcance no escriben ni dejan eventos; cada token tiene su propio límite; formularios reales funcionan y solicitudes sin CSRF siguen fallando; contenido hostil se muestra como texto; un lote con un elemento mal formado o una dependencia inválida conserva los válidos.

### 1. Descubrir el proyecto y su contexto — P1 · M

- Añadir `list_projects(query?, limit, offset)` y `get_project(project)` en MCP, delegando en un nuevo servicio de proyectos y reutilizando el repositorio.
- Devolver slug, nombre, descripción, estado archivado y permisos efectivos de lectura/escritura. No exponer credenciales ni interpretar `project_scope=[]` como ausencia de permisos: en el código actual significa sin restricción.
- Ampliar `search_tickets` con `project` y devolver `project` en cada resultado. Combinar texto y filtros también en la web.
- Incorporar límites acotados y señales de paginación; las listas no deben fingir que un subconjunto es el total. Usar descripción existente y configuración humana, sin inspeccionar repositorios desde el servidor.

**Aceptación:** «qué tickets tenemos en X» se resuelve sin abrir SQLite, la web o archivos del repositorio; la búsqueda no mezcla proyectos y un nombre ambiguo pide elección explícita. Los proyectos siguen creándose por la administración existente.

### 2. Contrato de entrada para los dos casos — P1 · L

- Añadir `intake_mode` con valores `incident` e `implementation`. Por compatibilidad, omitirlo equivale al contrato actual `implementation`; `type` conserva categorías como `bug`, `feature` y `security`.
- Añadir un objeto tipado `support_context` para síntoma/resultado observado, comportamiento esperado cuando se conoce, impacto, entorno, reproducción o motivo por el que no pudo reproducirse. Hipótesis y causa confirmada no son requisitos de entrada.
- Ampliar evidencia con variantes tipadas para código, observación, registro, comando con resultado y referencia externa. Interpretar los elementos antiguos sin `kind` como evidencia de código.
- En ingreso `incident`, exigir proyecto, título, descripción útil, síntoma y al menos una evidencia observable; permitir declarar desconocidos y no exigir archivos, plan de implementación, estimación ni diagnóstico. En ingreso `implementation`, conservar el contrato de calidad actual.
- Exponer las mismas entradas en `lint_ticket`, `create_ticket` y lote, incluyendo campos anidados en el esquema MCP, no solo en documentación. Los errores de forma y contenido deben identificar campo y corrección de manera estructurada.
- Conservar `external_key` estable por reporte y proyecto; documentar su reutilización en reintentos. No usar el título como deduplicación semántica ni crear claves nuevas en cada intento.
- Guardar contexto y modalidad mediante una nueva migración; no reescribir migraciones históricas. Las columnas JSON actuales admiten las variantes de evidencia sin tabla de adjuntos.

**Aceptación:** un `bug` sin acceso al código pasa con observaciones reales y nace `open`; un reporte vacío falla con instrucciones; el borrador histórico continúa pasando y nace `ready`; un reintento no duplica; no se requiere inventar una solución para publicar.

### 3. Seguimiento recuperable y guía por estado — P1 · L

- Añadir `get_ticket_activity(project, code, limit, cursor?)` con comentarios y eventos ordenados, autor y fecha, paginación estable y límite de tamaño. Reutilizar `Comment` y `TicketEvent`; no crear otra entidad de conversación.
- Ampliar `get_ticket` con contexto de soporte, última resolución y `workflow`: transiciones permitidas, campos faltantes, bloqueos, capacidades del actor, acciones posibles y referencias al contrato aplicable. Reutilizar `allowed_targets` y validadores puros; no calcular reglas en el adaptador MCP.
- Definir validación por etapa: actualizar un incidente `open` no exige todavía un plan completo; promoverlo a `ready` sí exige objetivo comprobable, alcance y pasos de la intervención o diagnóstico planificado. Una acción de código exige evidencia de código; una acción operativa exige evidencia observable adecuada.
- Validar el resultado combinado de cada patch, sin mutaciones parciales al rechazarlo. Incluir modalidad, síntomas y demás información sustantiva en el hash de revisión; conservar lock optimista y auditoría.
- Mantener `open → ready → in_progress → done`, con `blocked` y reapertura existentes. Para permanecer en `open`, se pueden pedir datos mediante comentarios sin falsear que el problema está listo para implementar.
- Exigir explicación en descarte `wont`. No convertir una revisión en una aprobación obligatoria ni dar al servidor capacidad de decidir si una afirmación es verdadera.

**Aceptación:** otro agente recupera lo observado, lo intentado y lo pendiente; una promoción incompleta devuelve los campos faltantes; una actualización inválida no altera contenido, hash ni eventos; las acciones sugeridas no exceden los permisos ni permiten ejecutar código desde OpenSupport.

### 4. Resolución verificable y respuesta para el usuario — P1 · M

- Ampliar `close_ticket` con un objeto tipado `resolution`: clase de solución (`code_fix`, `configuration`, `workaround`, `guidance`), resumen de lo aplicado, respuesta para el usuario y verificaciones con resultado observado y momento de ejecución.
- Para `code_fix`, conservar PR o commit y comandos. Para una solución sin cambios de código, exigir verificación observable del resultado sin inventar un commit; puede ser una prueba manual documentada, no necesariamente un comando.
- Mantener compatibilidad con la firma actual `pr/commit/commands/notes` normalizándola al cierre de código existente. En cierres nuevos de incidentes, exigir la respuesta al usuario y resultados, no solo nombres de comandos.
- Guardar la resolución actual en `verification` y su historial en los eventos existentes. Al reabrir, mostrarla como histórica hasta una nueva verificación; no presentarla como solución vigente.
- No usar `done` para «no investigado», «no reproducido» o «estamos esperando información». Un descarte motivado usa `wont`; una espera se documenta según el estado.

**Aceptación:** una corrección de código conserva sus exigencias; una configuración corregida puede cerrarse con evidencia sin PR; una respuesta sin verificación no cierra; un agente y una persona pueden recuperar la solución y el mensaje final sin depender del chat. No se exige confirmación de lectura.

### 5. Descubrimiento y experiencia coherente — P1 · M

- Actualizar las instrucciones de inicialización, descripciones de todas las herramientas de escritura y `get_schema` para explicar los dos ingresos y el recorrido hasta resolución.
- Mantener `opensupport://skill/ticket-authoring` como entrada y añadir un recurso plano `opensupport://workflow/support` para el ciclo completo. Mostrarlo en `resources/list` y desde las descripciones; no depender solo de plantillas o de que el cliente lea recursos espontáneamente.
- Extender `get_schema` para publicar los contratos de entrada, etapas y resolución, con versión de contrato y ejemplos mínimos válidos para ambos casos. Las tablas de violaciones y la guía deben coincidir con los validadores reales.
- Adaptar detalle y conversación web para mostrar síntomas, pasos de reproducción separados de intervención, evidencia tipada, actividad, siguiente acción y respuesta final. Corregir autores de comentarios y fechas de revisiones que hoy se serializan como texto y se tratan como datetime en el template.
- Actualizar demo con ambos casos. Crear `docs/ticket-contract.md` como explicación enlazada a los recursos/esquemas canónicos; actualizar `README.md`, `AGENTS.md`, `CONTEXT.md` y arquitectura. Señalar `docs/PLAN.md` como plan histórico, no descripción del estado actual.
- Añadir un bloque de conexión para Pi junto a los clientes existentes, usando el mecanismo HTTP+Bearer configurado por el usuario; no instalar clientes, modificar configuraciones globales ni imprimir secretos en la documentación.

**Aceptación:** un cliente que solo conoce URL y credencial encuentra el recorrido en `tools/list`; los recursos son legibles; los ejemplos pasan los validadores; el usuario ve la solución y la conversación real, no solo datos de demostración sin actividad.

## Archivos a modificar tras aprobación de implementación

| Área | Rutas críticas |
|---|---|
| Reglas y contratos | `app/tickets/schema.py`, `app/tickets/linter.py`, `app/domain/enums.py`, `app/domain/transitions.py` |
| Servicios y lectura | `app/tickets/service.py`, `app/tickets/repository.py`, nuevo `app/projects/service.py`, `app/sessions/service.py` para permisos y lectura consistente |
| Persistencia | `app/domain/models.py`, nueva `alembic/versions/0003_support_lifecycle.py`; conservar los datos y las migraciones anteriores |
| MCP | `app/api/mcp/server.py`, `app/api/mcp/auth.py`, `app/auth/tokens.py`, `app/ratelimit.py` |
| Interfaz y seguridad | `app/api/web/views.py`, `app/templates/base.html`, `index.html`, `ticket.html`, `session.html`, `tokens.html`, `partials/_board.html`, `app/main.py` |
| Incorporación y ejemplos | `app/cli.py`, `app/demo.py`, `app/skill/ticket-authoring.md`, nuevo `app/skill/support-workflow.md` |
| Documentación | `AGENTS.md`, `CONTEXT.md`, `README.md`, `docs/architecture.md`, nuevo `docs/ticket-contract.md`, nota histórica en `docs/PLAN.md` |
| Pruebas | `tests/fakes/valid_draft.py`, pruebas existentes de linter, servicio, transiciones, sesiones, protocolo, integración, CSRF, límites, lotes, demo y migraciones; nuevos casos de recorrido y seguridad |

En esta fase solo se modifica `plans/repository-review.md`. La propuesta no incluye nuevas dependencias, cambios de hosting ni una reescritura del tablero.

### Estado tras la ejecución

- 276 tests en verde; `ruff check`, `ruff format --check` y `mypy app` limpios.
- Archivos nuevos: `app/skill/support-workflow.md`, `app/projects/service.py`,
  `docs/ticket-contract.md`, `alembic/versions/0003_support_intake.py`,
  `tests/test_support_intake.py`, `tests/test_support_lifecycle.py`,
  `tests/test_web_flow.py`.
- Sin commits: el árbol de trabajo quedó sin commitear, a la espera de que se
  pida explícitamente.

## Reutilización

- `lint`, `errors`, `warnings` y `Violation` en `app/tickets/linter.py` / `schema.py`: validación determinista y correcciones accionables.
- `Result`, `_resolve_project`, `_resolve_ticket`, `_can_mutate`, `create_ticket`, `update_ticket`, `close_ticket` y `_add_event` en `app/tickets/service.py`: una sola fuente de verdad; corregir sus brechas en lugar de duplicarlas.
- `allowed_targets`, `can_transition`, `requires_close_evidence` en `app/domain/transitions.py`: guía y transiciones consistentes.
- `repo.get_project_by_slug`, `all_projects`, `list_tickets`, `search_tickets`, `get_ticket_by_external_key`: descubrimiento e idempotencia.
- `Comment`, `TicketEvent`, `TicketReview` y `verification`: conversación, auditoría y resolución existentes.
- `_run_in_thread`, `session_scope`, `require_read`, `require_write`, `require_csrf`: acceso seguro a persistencia y superficies; completar su aplicación uniforme.
- `make_valid_draft`, fixtures temporales y servidor real de `tests/test_integration.py`: ampliar con un incidente válido y ambos recorridos.

## Pasos

### Revisión realizada

- [x] Leer instrucciones, inventariar el repositorio y aclarar objetivo de producto.
- [x] Leer código, documentación, migraciones, configuración, plantillas y pruebas.
- [x] Consultar disponibilidad y autenticación de modelos sin mostrar secretos.
- [x] Ejecutar y recuperar ambas revisiones de Pi mediante Orca, sin escritura de producto.
- [x] Contrastar informes con código y separar recomendaciones de hechos observados.
- [x] Preparar iniciativas, dependencias y aceptación verificable.

### Ejecución realizada (2026-10-21)

- [x] 0. Pruebas de regresión y corrección de identidad, permisos, aislamiento de lote y seguridad del recorrido.
- [x] 1. Descubrimiento del proyecto (`list_projects`, `get_project`) y búsqueda acotada por proyecto.
- [x] 2. Contrato de entrada con `intake_mode`, `support_context` y evidencia tipada, con migración `0003_support_intake`.
- [x] 3. Actividad recuperable (`get_ticket_activity`), validación por etapa y orientación estructurada (`workflow`).
- [x] 4. Cierre tipado con `resolution` y respuesta para el usuario, sin exigir Git.
- [x] 5. Recursos, ejemplos, representación web del recorrido, demo con ambos casos y documentación.
- [x] Verificar ambos recorridos completos contra el servidor real, la compatibilidad y la seguridad.

### Pendiente (fuera del primer recorrido)

- [ ] P2 · sesiones: `list_sessions(status="closed")` nunca obtiene cerradas; el contador del resource suma tickets del proyecto; el snapshot puede escribir participantes al leer.
- [ ] P2 · operación: revisar restauración con SQLite/WAL y servicio detenido; `railway.toml` invoca `uv run` pese al arranque directo del contenedor.
- [ ] P2 · validación: unificar tabla de violaciones, mensajes y documentación.
- [ ] P3 · experiencia posterior: relaciones de duplicado explícitas, consultas de actividad por proyecto y plantillas específicas por proyecto.

Las entregas se mantienen revisables e incrementales; no se propone implementar todo en un único cambio ni crear tickets reales, commits, PRs o despliegues sin solicitud expresa.

## Verificación tras aprobación

1. **TDD del dominio:** caso positivo y borde por variante de evidencia, etapa, transición, actualización, alcance, vínculo, lote y cierre. Mantener la factory histórica y añadir una de incidentes.
2. **Migración en una base temporal poblada:** actualizar desde el esquema actual, preservar tickets/comentarios/revisiones/vínculos e índice FTS; verificar downgrade en una copia de prueba sin descartar silenciosamente información nueva.
3. **Protocolo:** comprobar `tools/list`, esquemas anidados, referencias al contrato en toda escritura, `resources/list`, paridad de entradas de lint/creación y violaciones estructuradas.
4. **Recorrido A contra servidor real:** descubrir X, consultar tickets, publicar incidente sin código, registrar reproducción externa, recuperar actividad desde otro cliente, completar contexto, pasar a `ready`, resolver, verificar y recuperar respuesta final. Sin escribir documentos en el proyecto reportado.
5. **Recorrido B:** el borrador histórico nace `ready`, un reintento conserva identidad, se implementa externamente y se cierra con PR/commit y verificación. Reabrir no borra la historia.
6. **Casos adversos:** dos proyectos con `OS-001`; lectura compartida; escritura fuera de alcance y sobre ticket ajeno; tokens solo lectura; conflicto optimista; lotes con forma inválida y dependencia faltante; paginación sin pérdidas; logs y HTML hostiles; ausencia de confirmación de lectura.
7. **Web real:** formularios de comentario/revisión/estado y administración con CSRF, dos proyectos, autores y fechas reales, filtros combinados y detalle de resolución. Si se automatiza con Playwright, un solo trabajador y sin nuevas instalaciones no aprobadas.
8. **Calidad:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`. Se ejecutarán en el entorno de desarrollo tras aprobación, no sobre bases reales.
9. **Prueba de autonomía:** con servicio y datos temporales, un agente que solo recibe URL, credencial de prueba y objetivo recorre ambas rutas leyendo MCP. Registrar herramientas usadas y puntos donde necesita explicación humana; no confundir esa prueba con las revisiones de lectura ya realizadas.

Durante esta revisión no se ejecutaron tests, migraciones, servidor ni herramientas de producto con escritura. El estado funcional se infiere de los archivos leídos; no se afirma que la suite esté aprobada.

Se intentó ejecutar el verificador de idioma indicado en `AGENTS.md`, pero `~/.minimax/skills/solo-en-es/scripts/check_language.py` no existe en este entorno. La revisión lingüística del plan fue manual; no se instaló ni sustituyó el verificador.

## Cobertura de lectura y límites

Inventario: 75 archivos versionados, incluidos 14 inicializadores vacíos. Se revisaron todos los archivos de código fuente, pruebas, plantillas, estilos, migraciones, documentación y configuración no vacíos. `uv.lock` se procesó completo con el analizador TOML y se inspeccionaron formato y versiones principales: 116 paquetes; no se auditaron artefactos, hashes ni dependencias transitivas por vulnerabilidades.

`docs/PLAN.md` se leyó como antecedente: contiene decisiones y descripciones históricas contradictorias con el código actual, por lo que no se tomó como prueba de funcionalidad implementada. No se accedió a bases de datos, secretos, cachés ni código de dependencias. No se comprobó hosting ni información externa de precios/planes mencionada en documentación antigua.

## Seguimientos posteriores, fuera del primer recorrido

- **P2 · sesiones:** `list_sessions(status="closed")` nunca obtiene cerradas porque parte de `open_sessions`; el contador del recurso suma tickets del proyecto, no de cada sesión, y el snapshot puede escribir participantes al leer. Corregir listados, paginación y separar consulta de incorporación.
- **P2 · operación:** revisar restauración con SQLite/WAL y servicio detenido, probar respaldo/restauración con datos, y hacer que readiness JSON devuelva 503 cuando falla. `railway.toml` invoca `uv run` pese al arranque directo del contenedor; verificarlo antes de desplegar.
- **P2 · validación:** unificar tabla de violaciones, mensajes y documentación; cubrir discrepancias actuales de umbrales y campos desconocidos sin cambiar reglas por intuición.
- **P3 · experiencia posterior:** relaciones de duplicado explícitas, consultas de actividad por proyecto y plantillas específicas por proyecto, solo tras observar uso real del recorrido básico.

Fuera de alcance: LLM en servidor, orquestación o ejecución de agentes/proyectos desde OpenSupport, REST, múltiples equipos aislados, sincronización GitHub, notificaciones, confirmación de lectura, adjuntos binarios y despliegue. Se admiten referencias a evidencia externa sin descargarlas ni confiar en sus instrucciones.

## Decisiones resueltas y límites de garantía

- Dos entradas: con acceso al repositorio y sin él.
- Incidentes `open`; propuestas investigadas `ready`; no hay aprobación humana obligatoria para publicar.
- El servicio valida estructura, etapas y permisos; el agente reúne datos y propone/aplica soluciones en su entorno autorizado.
- OpenSupport conserva la respuesta al usuario y la evidencia declarada, pero no garantiza que el usuario la lea ni que un comando haya sido realmente ejecutado.
- El soporte vive en OpenSupport por MCP; los documentos de implementación de este repositorio no se convierten en un requisito para reportar un problema.
- La aprobación de esta propuesta no implica permiso para publicar datos, desplegar o modificar otros proyectos.
