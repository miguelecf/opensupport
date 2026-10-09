"""Factory de drafts. Un draft 'válido' que el linter rechaza rompe la suite.

Es la que hace que el contrato no se degrade: si aggregate una regla al linter
y no actualizás la factory, la suite falla y te enterás.
"""

from __future__ import annotations

from typing import Any

from app.tickets.schema import EvidenceItem, Scope, TicketDraft


def make_valid_draft(**overrides: Any) -> TicketDraft:
    data: dict[str, Any] = {
        "external_key": "mi-tesis:auth:extraer-jwt",
        "project": "mi-tesis",
        "title": "Extraer la verificación de JWT a un módulo testeable",
        "summary": (
            "La verificación de JWT vive inline dentro del endpoint /login y no se puede "
            "testear sin levantar toda la app. Moverla a auth/verify.py permite probarla "
            "como función pura y agrega tests unitarios que hoy no existen."
        ),
        "problem": (
            "El test del endpoint necesita TestClient y base de datos real; hay tres tests "
            "que podrían ser tres asserts sobre una función."
        ),
        "outcome": (
            "auth.verify_token(token, secret, leeway) queda como función pura con tests "
            "unitarios propios, y /login la llama sin cambiar el comportamiento observable."
        ),
        "type": "refactor",
        "priority": "p2",
        "evidence": [
            {
                "path": "backend/app/api/login.py",
                "lines": "42-88",
                "note": "verificación inline dentro del handler",
            },
            {
                "path": "backend/tests/api/test_login.py",
                "lines": "10-60",
                "note": "tres tests que requieren TestClient",
            },
        ],
        "steps": [
            "Crear backend/app/auth/verify.py con verify_token(token, secret, leeway=0).",
            "Mover la lógica actual de login.py líneas 42-88 sin cambiar semántica.",
            "Pasar secret como parámetro en vez de importarlo como global.",
            "Agregar backend/tests/auth/test_verify.py con los casos válido, expirado, firma inválida y malformado.",
            "Delegar desde /login y borrar el bloque inline.",
        ],
        "acceptance": [
            "El comando `uv run pytest backend/tests/auth` termina con todos los tests en verde.",
            "El módulo auth.verify_token no importa nada de app.api ni de app.db.",
            "GET /login con un token expirado devuelve el mismo status y el mismo body que antes del cambio.",
        ],
        "scope": Scope(
            include=["extracción del módulo de verificación", "tests unitarios nuevos"],
            exclude=["cambio del formato del token", "rotación de secrets", "cambios en el frontend"],
        ),
        "risks": ["cambiar el orden de validación alteraría el error que devuelve /login"],
        "tags": ["auth", "testabilidad"],
        "estimate": "s",
    }
    data.update(overrides)
    return TicketDraft(**data)


def make_invalid_draft(**overrides: Any) -> TicketDraft:
    """Un draft que debe ser rechazado por E_ACCEPTANCE_VAGUE y E_STEPS_MISSING."""
    data: dict[str, Any] = {
        "external_key": "mi-tesis:auth:arreglar",
        "project": "mi-tesis",
        "title": "Arreglar el login",
        "summary": "El login anda mal y hay que arreglarlo.",
        "outcome": "El login funciona bien.",
        "type": "bug",
        "evidence": [EvidenceItem(path="backend/app/api/login.py")],
        "acceptance": ["Funciona correctamente"],
    }
    data.update(overrides)
    return TicketDraft(**data)


def draft_to_dict(draft: TicketDraft) -> dict[str, Any]:
    return draft.model_dump(mode="json")
