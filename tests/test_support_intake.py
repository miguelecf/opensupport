"""Contrato de entrada: dos modalidades, un mismo contrato de salida.

`implementation` es el contrato histórico (investigaste el código y sabés qué
cambiar). `incident` admite un reporte sin acceso al repo: síntoma y evidencia
observable, sin diagnóstico inventado.
"""

from __future__ import annotations

import pytest

from app.domain.enums import IntakeMode
from app.tickets.linter import errors, lint, warnings
from app.tickets.schema import EvidenceItem
from tests.fakes.valid_draft import make_incident_draft, make_valid_draft


def test_draft_historico_sigue_pasando() -> None:
    found = lint(make_valid_draft())
    assert errors(found) == []


def test_draft_historico_nace_implementation() -> None:
    assert make_valid_draft().intake_mode is IntakeMode.IMPLEMENTATION


def test_incidente_valido_no_tiene_errores() -> None:
    found = lint(make_incident_draft())
    assert errors(found) == [], [v.model_dump() for v in errors(found)]


def test_incidente_no_exige_pasos_ni_criterios() -> None:
    """El contrato de implementación no se le exige a un incidente."""
    codes = {v.code for v in errors(lint(make_incident_draft()))}
    assert "E_STEPS_MISSING" not in codes
    assert "E_ACCEPTANCE_MISSING" not in codes
    assert "E_SCOPE_EMPTY" not in codes


def test_incidente_sin_sintoma_falla() -> None:
    draft = make_incident_draft()
    draft.support_context = None
    codes = {v.code for v in errors(lint(draft))}
    assert "E_SUPPORT_CONTEXT_MISSING" in codes


def test_incidente_sin_evidencia_falla() -> None:
    draft = make_incident_draft()
    draft.evidence = []
    codes = {v.code for v in errors(lint(draft))}
    assert "E_EVIDENCE_MISSING" in codes


def test_incidente_sin_resultado_es_advertencia_no_error() -> None:
    """No saber el comportamiento esperado no impide reportar el problema."""
    draft = make_incident_draft(outcome="")
    assert errors(lint(draft)) == []
    assert "W_NO_OUTCOME" in {w.code for w in warnings(lint(draft))}


def test_implementation_sigue_exigiendo_evidencia_de_codigo() -> None:
    draft = make_valid_draft()
    draft.evidence = []
    codes = {v.code for v in errors(lint(draft))}
    assert "E_EVIDENCE_MISSING" in codes


def test_evidencia_de_codigo_sin_path_falla() -> None:
    draft = make_valid_draft()
    draft.evidence = [EvidenceItem(kind="code", note="sin path")]
    codes = {v.code for v in errors(lint(draft))}
    assert "E_EVIDENCE_MALFORMED" in codes


def test_evidencia_de_observacion_vacia_falla() -> None:
    draft = make_incident_draft()
    draft.evidence = [EvidenceItem(kind="observation")]
    codes = {v.code for v in errors(lint(draft))}
    assert "E_EVIDENCE_MALFORMED" in codes


def test_evidencia_de_comando_exige_comando() -> None:
    draft = make_incident_draft()
    draft.evidence = [EvidenceItem(kind="command", result="ok")]
    codes = {v.code for v in errors(lint(draft))}
    assert "E_EVIDENCE_MALFORMED" in codes


def test_evidencia_de_comando_con_comando_pasa() -> None:
    draft = make_incident_draft()
    draft.evidence = [EvidenceItem(kind="command", command="curl -s /health", result="timeout")]
    assert errors(lint(draft)) == []


def test_evidencia_historica_se_interpreta_como_codigo() -> None:
    """Un item sin 'kind' es del contrato viejo: path relativo."""
    draft = make_valid_draft()
    assert draft.evidence[0].kind.value == "code"
    assert errors(lint(draft)) == []


def test_el_hash_de_contenido_incluye_modalidad_y_sintoma() -> None:
    from app.tickets.schema import compute_content_hash

    a = compute_content_hash({"intake_mode": "incident", "support_context": {"symptom": "a"}})
    b = compute_content_hash({"intake_mode": "implementation", "support_context": {"symptom": "a"}})
    assert a != b


def test_el_modo_invalido_falla_en_el_schema() -> None:
    from pydantic import ValidationError

    from app.tickets.schema import TicketDraft

    with pytest.raises(ValidationError):
        TicketDraft(
            project="p",
            title="t",
            summary="s" * 90,
            outcome="o" * 50,
            intake_mode="inventado",  # type: ignore[arg-type]
        )
