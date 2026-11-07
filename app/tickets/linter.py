"""Linter de tickets. Función pura: draft -> lista de violaciones. Sin DB, sin FastAPI.

Este módulo es el corazón del producto: define qué es un ticket aceptable.
Cada regla devuelve un código, el campo, un mensaje y la instrucción de corrección.
"""

from __future__ import annotations

import re

from app.domain.enums import EvidenceKind, IntakeMode
from app.tickets.schema import (
    MAX_ACCEPTANCE,
    MAX_SCOPE_ITEMS,
    MAX_STEPS,
    MAX_TITLE,
    MIN_OUTCOME,
    MIN_STEP,
    MIN_SUMMARY,
    TITLE_MAX_HINT,
    TicketDraft,
    Violation,
)

#: Frases que no describen un resultado comprobable, tal cual. Español e inglés.
VAGUE_PHRASES: tuple[str, ...] = (
    r"\bfunciona\s+(bien|correctamente|igual|ok)\b",
    r"\bfuncione\s+(bien|correctamente|igual|ok)\b",
    r"\bque\s+funcione\b",
    r"\bque\s+anda\b",
    r"\bandar?\s+bien\b",
    r"\bno\s+falla\b",
    r"\bsin\s+errores\b",
    r"\btodo\s+(ok|bien|funciona|anda)\b",
    r"\bdeber[ií]a\s+funcionar\b",
    r"\bno\s+deber[ií]a\s+fallar\b",
    r"\bshould\s+work\b",
    r"\bworks?\s+(fine|properly|as\s+expected)\b",
    r"\bas\s+appropriate\b",
    r"\bno\s+todo\b",
    r"\b(todo|bien|ok|okay)\s*$",
)

#: Palabras que, si son TODO el contenido de un criterio, no dicen nada.
VAGUE_WORDS: frozenset[str] = frozenset(
    {
        "funciona",
        "funcionan",
        "funcione",
        "anda",
        "bien",
        "ok",
        "okay",
        "correcto",
        "correctamente",
        "work",
        "works",
        "working",
        "properly",
        "proper",
        "robust",
        "robustamente",
        "graceful",
        "todo",
        "debería",
        "deberia",
        "nada",
        "juste",
        "justo",
    }
)

#: Palabras funcionales que no aportan contenido. Se descartan antes de.juzgar.
STOPWORDS: frozenset[str] = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "al",
        "y",
        "o",
        "u",
        "que",
        "en",
        "para",
        "con",
        "sin",
        "se",
        "no",
        "a",
        "lo",
        "alguno",
        "alguna",
        "es",
        "está",
        "the",
        "an",
        "of",
        "to",
        "in",
        "for",
        "with",
        "and",
        "or",
        "is",
        "are",
        "be",
        "it",
        "this",
        "that",
        "does",
        "should",
        "must",
    }
)

STEP_VERBS: tuple[str, ...] = (
    "crear",
    "crea",
    "mover",
    "mueve",
    "agregar",
    "agrega",
    "añadir",
    "eliminar",
    "borra",
    "borrar",
    "extraer",
    "extrae",
    "refactorizar",
    "renombrar",
    "delegar",
    "documentar",
    "actualizar",
    "reemplazar",
    "separar",
    "unificar",
    "escribir",
    "implementar",
    "pasar",
    "definir",
    "configurar",
    "convertir",
    "copiar",
    "mover",
    "ordenar",
    "optimizar",
    "instalar",
    "levantar",
    "probar",
    "testear",
    "medir",
    "revisar",
    "migrate",
    "create",
    "move",
    "add",
    "remove",
    "delete",
    "extract",
    "rename",
    "update",
    "replace",
    "write",
    "implement",
    "pass",
    "set",
    "define",
    "convert",
    "copy",
    "sort",
    "optimize",
    "install",
    "test",
)

#: Sufijos de infinitivo español. Un paso que arranca con una palabra terminada
#: así es un verbo de acción aunque no esté en la lista: la lista fija rechazaba
#: "Verificar que el board muestre el ticket", que es un paso perfectamente válido.
SPANISH_VERB_SUFFIXES = ("ar", "er", "ir")

#: Verbos en inglés que no se detectan por forma.
ENGLISH_STEP_WORDS = frozenset(
    {
        "add",
        "allow",
        "avoid",
        "call",
        "change",
        "clean",
        "combine",
        "configure",
        "convert",
        "copy",
        "create",
        "debug",
        "define",
        "delete",
        "deploy",
        "disable",
        "document",
        "enable",
        "enforce",
        "extract",
        "expose",
        "fix",
        "handle",
        "implement",
        "improve",
        "keep",
        "limit",
        "link",
        "make",
        "merge",
        "mock",
        "move",
        "parse",
        "pass",
        "prepare",
        "reduce",
        "refactor",
        "rename",
        "replace",
        "reuse",
        "rewrite",
        "run",
        "set",
        "simplify",
        "split",
        "store",
        "test",
        "track",
        "unify",
        "update",
        "use",
        "validate",
        "verify",
        "wire",
    }
)

#: Verbos cortos o irregulares que el sufijo -ar/-er/-ir no cubre: "leer" es un
#: verbo de acción y son 4 letras, así que el filtro por longitud lo dejaba fuera.
IRREGULAR_SPANISH_VERBS = frozenset(
    {
        "leer",
        "ver",
        "dar",
        "ir",
        "hacer",
        "decir",
        "querer",
        "poder",
        "saber",
        "tener",
        "venir",
        "salir",
        "seguir",
        "poner",
        "traer",
        "caer",
        "estar",
        "ser",
        "haber",
        "oler",
        "reír",
        "reir",
        "soler",
        "dormir",
        "abrir",
    }
)

VAGUE_MIN_LENGTH = 60


def _v(code: str, field: str, message: str, fix: str) -> Violation:
    return Violation(code=code, field=field, message=message, fix=fix)


def _looks_vague(text: str) -> bool:
    """Un criterio es vago si no dice NADA comprobable.

    Dos condiciones, ninguna de las dos por separado:
      1. matchea una frase vaga conocida ('funciona correctamente'), o
      2. todas sus palabras con contenido son palabras vagas ('anda bien').

    La condición 2 descarta las palabras funcionales antes de mirar, así que
    'no importa nada de app.api' NO es vago: tiene contenido comprobable.
    """
    normalized = text.lower().strip()
    if any(re.search(pattern, normalized) for pattern in VAGUE_PHRASES):
        return True
    words = re.findall(r"[a-záéíóúüñ]+", normalized)
    content = {w for w in words if w not in STOPWORDS}
    if not content:
        return True
    return content <= VAGUE_WORDS


def _check_title(draft: TicketDraft, out: list[Violation]) -> None:
    title = draft.title.strip()
    if not title:
        out.append(_v("E_TITLE_MISSING", "title", "El título está vacío.", TITLE_MAX_HINT))
    elif len(title) > MAX_TITLE:
        out.append(
            _v(
                "E_TITLE_MISSING",
                "title",
                f"El título tiene {len(title)} caracteres y el máximo es {MAX_TITLE}.",
                TITLE_MAX_HINT,
            )
        )


def _check_summary(draft: TicketDraft, out: list[Violation]) -> None:
    summary = (draft.summary or "").strip()
    if len(summary) < MIN_SUMMARY:
        out.append(
            _v(
                "E_SUMMARY_MISSING",
                "summary",
                f"El summary tiene {len(summary)} caracteres y el mínimo es {MIN_SUMMARY}.",
                "Describí QUÉ hace y POR QUÉ, en 2 a 5 oraciones.",
            )
        )


def _check_outcome(draft: TicketDraft, out: list[Violation]) -> None:
    outcome = (draft.outcome or "").strip()
    if len(outcome) < MIN_OUTCOME:
        out.append(
            _v(
                "E_OUTCOME_MISSING",
                "outcome",
                f"El outcome tiene {len(outcome)} caracteres y el mínimo es {MIN_OUTCOME}.",
                "Enunciá el cambio observable, no la tarea.",
            )
        )


def _check_evidence(draft: TicketDraft, out: list[Violation]) -> None:
    if not draft.evidence:
        out.append(
            _v(
                "E_EVIDENCE_MISSING",
                "evidence",
                "El ticket no cita ninguna evidencia.",
                "Cite al menos un path:line que leíste, o una observación concreta de lo que pasa.",
            )
        )
        return
    for index, item in enumerate(draft.evidence):
        if item.kind is EvidenceKind.CODE:
            _check_code_evidence(index, item.path or "", item.lines, out)
        elif item.kind is EvidenceKind.COMMAND:
            if not (item.command or "").strip():
                out.append(
                    _v(
                        "E_EVIDENCE_MALFORMED",
                        f"evidence[{index}].command",
                        "La evidencia de tipo 'command' no dice qué comando se corrió.",
                        "Poné el comando en 'command' y lo que devolvió en 'result'.",
                    )
                )
        elif item.kind is EvidenceKind.REFERENCE:
            if not (item.url or "").strip():
                out.append(
                    _v(
                        "E_EVIDENCE_MALFORMED",
                        f"evidence[{index}].url",
                        "La evidencia de tipo 'reference' no dice de dónde sale.",
                        "Poné la URL o el identificador del documento en 'url'.",
                    )
                )
        else:  # observation | log
            if not (item.note or "").strip():
                out.append(
                    _v(
                        "E_EVIDENCE_MALFORMED",
                        f"evidence[{index}].note",
                        f"La evidencia de tipo '{item.kind}' está vacía.",
                        "Describí lo que viste: el mensaje, el registro, el resultado.",
                    )
                )


def _check_code_evidence(index: int, path: str, lines: str | None, out: list[Violation]) -> None:
    if not path.strip():
        out.append(
            _v(
                "E_EVIDENCE_MALFORMED",
                f"evidence[{index}].path",
                "El item de evidencia no tiene path.",
                "Usá path relativo al repo.",
            )
        )
        return
    if path.startswith("/") or path.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", path):
        out.append(
            _v(
                "E_EVIDENCE_MALFORMED",
                f"evidence[{index}].path",
                f"'{path}' es una ruta absoluta.",
                "Usá path relativo al repo, por ejemplo 'backend/app/api/login.py'.",
            )
        )
    if ".." in path.split("/"):
        out.append(
            _v(
                "E_EVIDENCE_MALFORMED",
                f"evidence[{index}].path",
                f"'{path}' sale del repo con '..'.",
                "Copiá el path relativo dentro del repo.",
            )
        )
    if lines and not re.match(r"^\d+(-\d+)?$", lines.strip()):
        out.append(
            _v(
                "E_EVIDENCE_MALFORMED",
                f"evidence[{index}].lines",
                f"'{lines}' no es un rango de líneas.",
                "Usá '42' o '42-88'.",
            )
        )


def _check_support_context(draft: TicketDraft, out: list[Violation]) -> None:
    """Un incidente sin síntoma declarado no es un incidente: es una queja."""
    context = draft.support_context
    if context is None or not (context.symptom or "").strip():
        out.append(
            _v(
                "E_SUPPORT_CONTEXT_MISSING",
                "support_context.symptom",
                "El ticket no dice qué pasa.",
                "En support_context, contá el síntoma en una frase. No hace falta la causa.",
            )
        )
        return
    if len((context.symptom or "").strip()) < 15:
        out.append(
            _v(
                "E_SUPPORT_CONTEXT_MISSING",
                "support_context.symptom",
                "El síntoma es demasiado corto para entender el problema.",
                "Describí qué pasa, con qué dato se ve y desde cuándo.",
            )
        )


def _is_action_verb(word: str) -> bool:
    """¿La primera palabra del paso es un verbo de acción?

    Tres vías, en orden de confianza: lista explícita, sufijo de infinitivo
    español (-ar/-er/-ir) y lista de inglés. La lista sola rechazaba pasos
    legítimos que usan un verbo no contemplado, y eso traba el loop del agente.
    """
    if word in STEP_VERBS or word in ENGLISH_STEP_WORDS:
        return True
    if word in IRREGULAR_SPANISH_VERBS:
        return True
    return len(word) > 4 and word.endswith(SPANISH_VERB_SUFFIXES)


def _check_steps(draft: TicketDraft, out: list[Violation]) -> None:
    if not draft.steps:
        out.append(
            _v(
                "E_STEPS_MISSING",
                "steps",
                "El ticket no tiene pasos de implementación.",
                f"Entre 3 y {MAX_STEPS} pasos, cada uno arrancando con un verbo.",
            )
        )
        return
    if len(draft.steps) > MAX_STEPS:
        out.append(
            _v(
                "E_STEPS_TOO_MANY",
                "steps",
                f"El ticket tiene {len(draft.steps)} pasos y el máximo es {MAX_STEPS}.",
                f"Dividí el ticket: más de {MAX_STEPS} pasos es más de un ticket.",
            )
        )
    for index, step in enumerate(draft.steps):
        text = step.strip()
        if len(text) < MIN_STEP:
            out.append(
                _v(
                    "E_STEPS_VAGUE",
                    f"steps[{index}]",
                    f"El paso tiene {len(text)} caracteres y el mínimo es {MIN_STEP}.",
                    "Describí el paso con suficiente detalle para poder ejecutarlo.",
                )
            )
            continue
        first_word = text.lower().split()[0].rstrip(",.:;")
        if not _is_action_verb(first_word):
            out.append(
                _v(
                    "E_STEPS_VAGUE",
                    f"steps[{index}]",
                    f"El paso arranca con '{first_word}', que no es un verbo de acción.",
                    "Cada paso arranca con un verbo: Crear, Mover, Agregar, Eliminar, Extraer.",
                )
            )


def _check_acceptance(draft: TicketDraft, out: list[Violation]) -> None:
    if not draft.acceptance:
        out.append(
            _v(
                "E_ACCEPTANCE_MISSING",
                "acceptance",
                "El ticket no define cómo se sabe que está terminado.",
                "Al menos un criterio de aceptación verificable.",
            )
        )
        return
    if len(draft.acceptance) > MAX_ACCEPTANCE:
        out.append(
            _v(
                "E_ACCEPTANCE_TOO_MANY",
                "acceptance",
                f"El ticket tiene {len(draft.acceptance)} criterios y el máximo es {MAX_ACCEPTANCE}.",
                f"Recortá: {MAX_ACCEPTANCE} criterios suele ser más de un ticket.",
            )
        )
    for index, criterion in enumerate(draft.acceptance):
        if _looks_vague(criterion):
            out.append(
                _v(
                    "E_ACCEPTANCE_VAGUE",
                    f"acceptance[{index}]",
                    f"'{criterion.strip()}' no describe un resultado verificable.",
                    "Reformulá como algo que se pueda comprobar: "
                    "en vez de 'funciona correctamente', poné "
                    "'GET /login con token expirado devuelve 401'.",
                )
            )


def _check_scope(draft: TicketDraft, out: list[Violation]) -> None:
    include = draft.scope.include
    if not include:
        out.append(
            _v(
                "E_SCOPE_EMPTY",
                "scope.include",
                "El scope no dice qué entra.",
                "Enumerá al menos qué archivos o comportamientos cambian.",
            )
        )
    elif len(include) > MAX_SCOPE_ITEMS:
        out.append(
            _v(
                "E_SCOPE_TOO_BIG",
                "scope.include",
                f"El scope incluye {len(include)} items y el máximo es {MAX_SCOPE_ITEMS}.",
                f"Dividí el ticket: más de {MAX_SCOPE_ITEMS} items de scope es más de un ticket.",
            )
        )
    if not draft.scope.exclude:
        out.append(
            _v(
                "W_NO_EXCLUDE",
                "scope.exclude",
                "El scope no dice qué queda afuera.",
                "¿Qué queda explícitamente fuera de este cambio?",
            )
        )


def _check_risks(draft: TicketDraft, out: list[Violation]) -> None:
    if not draft.risks:
        out.append(
            _v(
                "W_NO_RISKS",
                "risks",
                "El ticket no piensa los riesgos.",
                "¿Qué puede romperse con este cambio?",
            )
        )


def lint(draft: TicketDraft) -> list[Violation]:
    """Devuelve las violaciones del draft. Vacío = ticket aceptable.

    Las reglas dependen de la modalidad de ingreso: un incidente no se juzga
    como una propuesta de implementación. Lo que exige siempre es que el
    reporte diga qué pasa y qué se observó.
    """
    out: list[Violation] = []
    _check_title(draft, out)
    _check_summary(draft, out)
    if draft.intake_mode is IntakeMode.IMPLEMENTATION:
        _check_outcome(draft, out)
        _check_evidence(draft, out)
        _check_steps(draft, out)
        _check_acceptance(draft, out)
        _check_scope(draft, out)
    else:
        _check_incident_outcome(draft, out)
        _check_evidence(draft, out)
        _check_support_context(draft, out)
    _check_risks(draft, out)
    return out


def _check_incident_outcome(draft: TicketDraft, out: list[Violation]) -> None:
    """En un incidente el resultado esperado puede no conocerse todavía."""
    if len((draft.outcome or "").strip()) < MIN_OUTCOME:
        out.append(
            _v(
                "W_NO_OUTCOME",
                "outcome",
                "El ticket no dice qué debería pasar.",
                "Si sabés el comportamiento esperado, agregalo. Si no, dejalo vacío: "
                "no hace falta inventarlo para reportar un incidente.",
            )
        )


def errors(violations: list[Violation]) -> list[Violation]:
    """Solo las que bloquean (sin W_)."""
    return [v for v in violations if not v.is_warning]


def warnings(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.is_warning]
