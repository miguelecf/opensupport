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
DEMO_INCIDENT_KEY = "mi-tesis:soporte:token-expirado"
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
            "exclude": [
                "cambio del formato del token",
                "rotación de secrets",
                "cambios en el frontend",
            ],
        },
        risks=["cambiar el orden de validación alteraría el error que devuelve el login"],
        tags=["auth", "testabilidad"],
        estimate="s",
    )


def demo_incident_draft() -> TicketDraft:
    """El segundo caso: un incidente reportado sin acceso al repo.

    Sale de la sesión a propósito: el soporte llega por otro lado, no dentro
    de una sesión de planificación.
    """
    return TicketDraft(
        external_key=DEMO_INCIDENT_KEY,
        project=DEMO_PROJECT,
        title="El login expira antes de tiempo en la app móvil",
        summary=(
            "Desde la versión 1.4 la sesión de la app móvil se cierra a los cinco minutos "
            "aunque el usuario siga usando la app. En la web no pasa y con la 1.3 tampoco. "
            "No tuve acceso al código para revisarlo: lo reporto con lo que se ve."
        ),
        type="bug",
        priority="p1",
        intake_mode="incident",
        support_context={
            "symptom": "La sesión móvil se cierra a los 5 minutos aunque la app esté en uso.",
            "observed": "Al volver al foreground la app redirige al login. En la 1.3 duraba una hora.",
            "impact": "Los usuarios tienen que loguearse de nuevo varias veces por sesión.",
            "environment": "App móvil 1.4.0, iOS 18, backend de staging.",
            "reproduction": [
                "Entrar con la app móvil 1.4.0.",
                "Dejar la app en foreground sin tocar nada.",
                "Volver a la app después de 5 minutos: redirige al login.",
            ],
            "unknown": ["No sé si es el TTL del token o un refresh que no llega."],
        },
        evidence=[
            {
                "kind": "observation",
                "note": "La app vuelve al login a los 5 minutos exactos, no de forma aleatoria.",
            },
            {
                "kind": "log",
                "note": "En el log del cliente: 'session expired' cada vez que vuelve al foreground.",
            },
        ],
        outcome="",
        steps=[],
        acceptance=[],
        scope={"include": [], "exclude": []},
        risks=[],
        tags=["soporte", "movil"],
    )


def seed(db: Any, principal: Principal) -> dict[str, Any]:  # noqa: ANN401
    """Crea proyecto, tickets de ejemplo y sesión abierta. Idempotente."""
    created: dict[str, Any] = {"project": False, "ticket": None, "incident": None, "session": None}

    if db.query(Project).filter_by(slug=DEMO_PROJECT).one_or_none() is None:
        db.add(
            Project(
                slug=DEMO_PROJECT,
                name="Mi tesis",
                description="Proyecto de ejemplo",
                created_at=utcnow(),
            )
        )
        db.flush()
        created["project"] = True
    else:
        # El CLI lo pudo haber creado solo con el slug: el board agrupado por
        # proyecto muestra nombre y descripción, así que los completamos.
        project = db.query(Project).filter_by(slug=DEMO_PROJECT).one()
        if project.name == project.slug:
            project.name = "Mi tesis"
        if not project.description:
            project.description = "Proyecto de ejemplo"

    # La sesión va PRIMERO: si el ticket se crea después sin session=, la sesión
    # queda vacía y el primero que abre la web ve un board sin nada dentro.
    session = sessions.start_session(
        db, principal, DEMO_PROJECT, DEMO_SESSION_TITLE, DEMO_SESSION_GOAL
    )
    session_code = str(session.data["code"]) if session.ok else None
    if session_code:
        created["session"] = session_code

    draft = demo_draft()
    if session_code:
        draft = draft.model_copy(update={"session": session_code})

    result = tickets.create_ticket(db, principal, draft)
    if not result.ok:
        logger.warning("No se pudo crear el ticket de demo: %s", result.violations[0].message)
        return created
    code = str(result.data["code"])
    created["ticket"] = code

    if session_code:
        sessions.join_session(db, principal, session_code)
        sessions.join_session(db, principal, session_code, role="observer")
        tickets.add_comment(
            db,
            principal,
            code,
            "Este es un ticket de ejemplo. Leelo: muestra el nivel de detalle que OpenSupport exige. "
            "Podés borrarlo con `set_status` cuando quieras.",
        )

    # El segundo caso: un incidente sin acceso al repo. Nace en 'open'.
    incident_result = tickets.create_ticket(db, principal, demo_incident_draft())
    if incident_result.ok:
        created["incident"] = str(incident_result.data["code"])
    else:
        logger.warning(
            "No se pudo crear el incidente de demo: %s", incident_result.violations[0].message
        )
    return created
