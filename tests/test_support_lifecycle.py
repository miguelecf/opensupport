"""Ciclo de soporte:ifecycle, permisos, aislamiento y cierre tipado.

Es la parte del contrato que no es el linter: qué nace en qué estado, qué
hace falta para promover, qué se rechaza y con qué mensaje.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.tickets import service
from app.tickets.schema import TicketPatch
from tests.fakes.valid_draft import make_incident_draft, make_valid_draft


def codes(result) -> set[str]:  # noqa: ANN001
    return {v.code for v in result.violations}


# --------------------------------------------------------------------------- #
# Estado inicial
# --------------------------------------------------------------------------- #


def test_un_incidente_nace_open(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    assert created.ok
    assert created.data["ticket_status"] == "open"


def test_una_propuesta_investigada_nace_ready(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    assert created.data["ticket_status"] == "ready"


def test_el_modo_y_el_contexto_se_guardan(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_incident_draft())
    ticket = get_ticket_by_code(session, created.data["code"])
    assert ticket is not None
    assert ticket.intake_mode == "incident"
    assert ticket.support_context is not None
    assert ticket.support_context["symptom"].startswith("La sesión móvil")


# --------------------------------------------------------------------------- #
# Promoción a ready
# --------------------------------------------------------------------------- #


def test_no_se_promueve_un_incidente_incompleto(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    result = service.set_status(session, principal, created.data["code"], "ready")
    assert not result.ok
    assert "E_PROMOTION_NOT_READY" in codes(result)
    fields = {m["field"] for m in result.data["missing"]}
    assert {"outcome", "scope.include", "steps"} <= fields


def test_se_promueve_un_incidente_completo(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    code = created.data["code"]
    service.update_ticket(
        session,
        principal,
        code,
        TicketPatch(
            outcome=(
                "La sesión móvil dura una hora como en la versión 1.3 y no redirige "
                "al login al volver al foreground."
            ),
            steps=[
                "Revisar el TTL del token en el cliente móvil.",
                "Comparar la configuración de refresh entre 1.3 y 1.4.",
            ],
            scope={"include": ["configuración de sesión del cliente móvil"], "exclude": []},
        ),
        None,
    )
    result = service.set_status(session, principal, code, "ready")
    assert result.ok, result.violations


def test_descartar_sin_motivo_falla(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    result = service.set_status(session, principal, created.data["code"], "wont")
    assert not result.ok
    assert "E_WONT_REASON_REQUIRED" in codes(result)


def test_descartar_con_motivo_anda(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    result = service.set_status(
        session, principal, created.data["code"], "wont", "Duplicado de OS-004."
    )
    assert result.ok


# --------------------------------------------------------------------------- #
# Actualizaciones: se valida antes de mutar
# --------------------------------------------------------------------------- #


def test_una_actualizacion_invalida_no_muta_nada(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_events, get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    before = get_ticket_by_code(session, code)
    assert before is not None
    hash_before = before.content_hash
    events_before = len(get_events(session, before.id))

    result = service.update_ticket(
        session, principal, code, TicketPatch(acceptance=["Funciona correctamente"]), None
    )
    assert not result.ok
    assert "E_ACCEPTANCE_VAGUE" in codes(result)

    after = get_ticket_by_code(session, code)
    assert after is not None
    assert after.content_hash == hash_before
    assert after.acceptance == before.acceptance
    assert len(get_events(session, after.id)) == events_before


def test_una_actualizacion_valida_cambia_el_hash(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    before = get_ticket_by_code(session, code)
    assert before is not None
    hash_before = before.content_hash
    result = service.update_ticket(
        session,
        principal,
        code,
        TicketPatch(
            summary="Un resumen nuevo, suficientemente largo para no romper el contrato del ticket ni perder la validación."
        ),
        None,
    )
    assert result.ok
    after = get_ticket_by_code(session, code)
    assert after is not None
    assert after.content_hash != hash_before


# --------------------------------------------------------------------------- #
# Identidad y permisos
# --------------------------------------------------------------------------- #


def test_no_se_vincula_un_ticket_de_otro_proyecto(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.domain.models import Project

    otro = Project(slug="otro", name="Otro")
    session.add(otro)
    session.flush()
    principal.project_scope = ["mi-tesis", "otro"]
    a = service.create_ticket(session, principal, make_valid_draft(external_key="a"))
    # Un segundo ticket en 'otro' para que su código no choque con el de 'a'.
    service.create_ticket(session, principal, make_valid_draft(external_key="b", project="otro"))
    b = service.create_ticket(
        session, principal, make_valid_draft(external_key="c", project="otro")
    )
    assert b.data["code"] != a.data["code"]
    result = service.link_tickets(
        session, principal, a.data["code"], b.data["code"], "relates_to", project="mi-tesis"
    )
    assert not result.ok
    assert "E_TICKET_OTHER_PROJECT" in codes(result)


def test_un_token_fuera_de_alcance_no_comenta(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    principal.project_scope = ["otro-proyecto"]
    result = service.add_comment(session, principal, created.data["code"], "hola")
    assert not result.ok
    assert "E_PROJECT_OUT_OF_SCOPE" in codes(result)


def test_una_dependencia_invalida_no_deja_ticket(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import list_tickets

    before = len(list_tickets(session, limit=100))
    result = service.create_ticket(
        session, principal, make_valid_draft(external_key="con-dep", deps=["OS-999"])
    )
    assert not result.ok
    assert len(list_tickets(session, limit=100)) == before


# --------------------------------------------------------------------------- #
# Lote: un item mal formado no tira los válidos
# --------------------------------------------------------------------------- #


def test_un_item_mal_formado_no_tira_el_lote(session: OrmSession, principal) -> None:  # noqa: ANN001
    results = service.create_tickets_batch(
        session,
        principal,
        [
            make_valid_draft(external_key="lote-1").model_dump(mode="json"),
            {"project": "mi-tesis", "title": 12345},  # forma inválida: no es un string
            make_valid_draft(external_key="lote-3").model_dump(mode="json"),
        ],
    )
    assert [r.ok for r in results] == [True, False, True]
    assert "E_INVALID_DRAFT" in codes(results[1])
    assert results[1].data["index"] == 1


# --------------------------------------------------------------------------- #
# Cierre tipado
# --------------------------------------------------------------------------- #


def _in_progress(session: OrmSession, principal, code: str) -> None:  # noqa: ANN001
    service.set_status(session, principal, code, "in_progress")


def _ready_incident(session: OrmSession, principal, code: str) -> None:  # noqa: ANN001
    """Un incidente pasa por 'ready' antes de poder trabajarse."""
    service.update_ticket(
        session,
        principal,
        code,
        TicketPatch(
            outcome=(
                "La sesión móvil dura una hora como en la versión 1.3 y no redirige "
                "al login al volver al foreground."
            ),
            steps=[
                "Revisar el TTL del token en el cliente móvil.",
                "Comparar la configuración de refresh entre 1.3 y 1.4.",
            ],
            scope={"include": ["configuración de sesión del cliente móvil"], "exclude": []},
        ),
        None,
    )
    service.set_status(session, principal, code, "ready")


def test_una_solucion_sin_codigo_se_cierra_con_verificacion(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    code = created.data["code"]
    _ready_incident(session, principal, code)
    _in_progress(session, principal, code)
    result = service.close_ticket(
        session,
        principal,
        code,
        {
            "kind": "configuration",
            "summary": "Se corrigió el TTL del cliente móvil.",
            "user_response": "Actualizá la app a la 1.4.1 y la sesión dura una hora.",
            "verifications": [
                {"result": "La sesión sigue viva a los 10 minutos", "command": "pytest movil"}
            ],
        },
    )
    assert result.ok, result.violations
    assert result.data["status"] == "done"


def test_una_solucion_sin_verificacion_no_cierra(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    code = created.data["code"]
    _ready_incident(session, principal, code)
    _in_progress(session, principal, code)
    result = service.close_ticket(
        session,
        principal,
        code,
        {
            "kind": "configuration",
            "summary": "Se corrigió algo.",
            "user_response": "Listo.",
        },
    )
    assert not result.ok
    assert "E_VERIFICATION_INCOMPLETE" in codes(result)


def test_una_solucion_sin_respuesta_al_usuario_no_cierra(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_incident_draft())
    code = created.data["code"]
    _ready_incident(session, principal, code)
    _in_progress(session, principal, code)
    result = service.close_ticket(
        session,
        principal,
        code,
        {
            "kind": "workaround",
            "summary": "Se докуyalo como workaround.",
            "user_response": "   ",
            "verifications": [{"result": "Se verificó a mano"}],
        },
    )
    assert not result.ok
    assert "E_RESOLUTION_INCOMPLETE" in codes(result)


def test_un_arranque_de_codigo_sigue_exigiendo_pr_o_commit(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    _in_progress(session, principal, code)
    result = service.close_ticket(
        session,
        principal,
        code,
        {
            "kind": "code_fix",
            "summary": "Se extrajo el módulo.",
            "user_response": "No tenés que hacer nada.",
            "verifications": [{"result": "los tests pasan"}],
        },
    )
    assert not result.ok
    assert "E_VERIFICATION_INCOMPLETE" in codes(result)


def test_el_contrato_viejo_de_cierre_sigue_andando(session: OrmSession, principal) -> None:  # noqa: ANN001
    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    _in_progress(session, principal, code)
    result = service.close_ticket(
        session, principal, code, {"pr": "https://example.test/pr/1", "commands": ["pytest"]}
    )
    assert result.ok


# --------------------------------------------------------------------------- #
# Actividad y workflow
# --------------------------------------------------------------------------- #


def test_la_actividad_trae_comentarios_y_eventos(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    service.add_comment(session, principal, code, "Lo revisé: el problema es el TTL.")
    ticket = get_ticket_by_code(session, code)
    assert ticket is not None
    activity = service.ticket_activity(session, ticket)
    kinds = {item["kind"] for item in activity["items"]}
    assert "comment" in kinds
    assert "event" in kinds
    assert any(
        item.get("body") == "Lo revisé: el problema es el TTL." for item in activity["items"]
    )


def test_el_workflow_dice_que_falta_para_promover(session: OrmSession, principal) -> None:  # noqa: ANN001
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_incident_draft())
    ticket = get_ticket_by_code(session, created.data["code"])
    assert ticket is not None
    workflow = service.workflow_for(ticket, principal)
    assert workflow["status"] == "open"
    assert workflow["missing_for_ready"]
    assert workflow["capabilities"]["can_mutate"] is True
    ready_action = next(a for a in workflow["actions"] if a["target"] == "ready")
    assert ready_action["available"] is False


@pytest.mark.parametrize("mode", ["incident", "implementation"])
def test_el_workflow_no_ofrece_mas_que_las_transiciones_validas(
    session: OrmSession, principal, mode: str
) -> None:  # noqa: ANN001
    from app.domain.transitions import allowed_targets
    from app.tickets.repository import get_ticket_by_code

    draft = make_incident_draft() if mode == "incident" else make_valid_draft()
    created = service.create_ticket(session, principal, draft)
    ticket = get_ticket_by_code(session, created.data["code"])
    assert ticket is not None
    workflow = service.workflow_for(ticket, principal)
    assert set(workflow["allowed_transitions"]) == set(allowed_targets(ticket.status))


def test_al_reabrir_la_resolucion_anterior_queda_como_historia(
    session: OrmSession, principal
) -> None:  # noqa: ANN001
    """Reabrir no deja la solución vieja como si siguiera vigente."""
    from app.tickets.repository import get_ticket_by_code

    created = service.create_ticket(session, principal, make_valid_draft())
    code = created.data["code"]
    service.set_status(session, principal, code, "in_progress")
    service.close_ticket(
        session, principal, code, {"pr": "https://example.test/pr/1", "commands": ["pytest"]}
    )
    reabierto = service.set_status(session, principal, code, "in_progress")
    assert reabierto.ok
    ticket = get_ticket_by_code(session, code)
    assert ticket is not None
    assert ticket.verification is None
