"""Contenido de demostración para `opensupport init --demo`.

Sin esto, `init --demo` deja un board vacío y un humano que no sabe qué está
mirando. Con esto, el primer segundo muestra un ticket completo —el mismo
ejemplo que documenta el skill— y una sesión abierta.
"""

from __future__ import annotations

import logging
from typing import Any

from app.auth.tokens import Principal
from app.db.base import utcnow
from app.domain.models import Project
from app.sessions import service as sessions
from app.tickets import service as tickets
from app.tickets.schema import TicketDraft

logger = logging.getLogger(__name__)

DEMO_PROJECT = "mi-tesis"
DEMO_TICKET_KEY = "mi-tesis:auth:extraer-jwt"
DEMO_SESSION_TITLE = "Auth: migrar a JWT"
DEMO_SESSION_GOAL = "Que /login deje de usar sesiones en memoria y quede testeable."


def demo_draft() -> TicketDraft:
    """El mismo ejemplo del skill: si divergen, uno de los dos miente."""
    return TicketDraft(
        external_key=DEMO_TICKET_KEY,
        project=DEMO_PROJECT,
        title="Extraer la verificación de JWT a un módulo testeable",
        summary=(
            "La verificación de JWT vive inline dentro del endpoint /login y no se puede "
            "testear sin levantar toda la app. Moverla a auth/verify.py permite probarla "
            "como función pura y agrega tests unitarios que hoy no existen."
        ),
        problem=(
            "El test del endpoint necesita TestClient y base de datos real; hay tres tests "
            "que podrían ser tres asserts sobre una función."
        ),
        outcome=(
            "auth.verify_token(token, secret, leeway) queda como función pura con tests "
            "unitarios propios, y /login la llama sin cambiar el comportamiento observable."
        ),
        type="refactor",
        priority="p2",
        evidence=[
            {"path": "app/api/mcp/auth.py", "lines": "40-70", "note": "verificación de token"},
            {"path": "app/api/web/views.py", "lines": "150-170", "note": "login por cookie"},
        ],
        steps=[
            "Crear app/auth/verify.py con verify_token(token, secret, leeway=0).",
            "Mover la lógica actual de auth sin cambiar la semántica observada.",
            "Pasar secret como parámetro en vez de importarlo como global.",
            "Agregar tests con los casos válido, expirado, firma inválida y malformado.",
            "Delegar desde el login y borrar el bloque inline.",
        ],
        acceptance=[
            "El comando `uv run pytest` termina con todos los tests en verde.",
            "El módulo nuevo no importa nada de la capa web.",
            "El login con una sesión expirada devuelve el mismo status que antes del cambio.",
        ],
        scope={
            "include": ["extracción del módulo de verificación", "tests unitarios nuevos"],
            "exclude": ["cambio del formato del token", "rotación de secrets", "cambios en el frontend"],
        },
        risks=["cambiar el orden de validación alteraría el error que devuelve el login"],
        tags=["auth", "testabilidad"],
        estimate="s",
    )


def seed(db: Any, principal: Principal) -> dict[str, Any]:  # noqa: ANN401
    """Crea proyecto, ticket de ejemplo y sesión abierta. Idempotente."""
    created: dict[str, Any] = {"project": False, "ticket": None, "session": None}

    if db.query(Project).filter_by(slug=DEMO_PROJECT).one_or_none() is None:
        db.add(Project(slug=DEMO_PROJECT, name="Mi tesis", description="Proyecto de ejemplo", created_at=utcnow()))
        db.flush()
        created["project"] = True

    result = tickets.create_ticket(db, principal, demo_draft())
    if not result.ok:
        logger.warning("No se pudo crear el ticket de demo: %s", result.violations[0].message)
        return created
    code = str(result.data["code"])
    created["ticket"] = code

    session = sessions.start_session(db, principal, DEMO_PROJECT, DEMO_SESSION_TITLE, DEMO_SESSION_GOAL)
    if session.ok:
        session_code = str(session.data["code"])
        sessions.join_session(db, principal, session_code)
        sessions.join_session(db, principal, session_code, role="observer")
        tickets.add_comment(
            db,
            principal,
            code,
            "Este es un ticket de ejemplo. Leelo: muestra el nivel de detalle que OpenSupport exige. "
            "Podés borrarlo con `set_status` cuando quieras.",
        )
        created["session"] = session_code
    return created
