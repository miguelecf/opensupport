"""Tests del linter. Table-driven: una fila por regla, más los bordes."""

from __future__ import annotations

import pytest

from app.tickets.linter import errors, lint, warnings
from app.tickets.schema import (
    MAX_ACCEPTANCE,
    MAX_SCOPE_ITEMS,
    MAX_STEPS,
    MAX_TITLE,
    Scope,
)
from tests.fakes.valid_draft import make_invalid_draft, make_valid_draft


def codes(draft) -> set[str]:  # noqa: ANN001
    return {v.code for v in errors(lint(draft))}


def test_valid_draft_passes() -> None:
    assert lint(make_valid_draft()) == []


def test_invalid_draft_is_rejected() -> None:
    found = codes(make_invalid_draft())
    assert "E_ACCEPTANCE_VAGUE" in found
    assert "E_STEPS_MISSING" in found


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"title": ""}, "E_TITLE_MISSING"),
        ({"title": "x" * (MAX_TITLE + 1)}, "E_TITLE_MISSING"),
        ({"summary": "corto"}, "E_SUMMARY_MISSING"),
        ({"outcome": "muy corto"}, "E_OUTCOME_MISSING"),
        ({"evidence": []}, "E_EVIDENCE_MISSING"),
        ({"evidence": [{"path": "/abs/path.py"}]}, "E_EVIDENCE_MALFORMED"),
        ({"evidence": [{"path": "../fuera.py"}]}, "E_EVIDENCE_MALFORMED"),
        ({"evidence": [{"path": ""}]}, "E_EVIDENCE_MALFORMED"),
        ({"steps": []}, "E_STEPS_MISSING"),
        ({"acceptance": []}, "E_ACCEPTANCE_MISSING"),
        ({"scope": Scope(include=[], exclude=["x"])}, "E_SCOPE_EMPTY"),
    ],
)
def test_rule_fires(overrides: dict, expected: str) -> None:  # noqa: ANN401
    assert expected in codes(make_valid_draft(**overrides))


@pytest.mark.parametrize("size", [MAX_STEPS, MAX_STEPS + 1])
def test_steps_threshold(size: int) -> None:
    step = "Crear el módulo de verificación con sus tests correspondientes."
    found = codes(make_valid_draft(steps=[step] * size))
    assert ("E_STEPS_TOO_MANY" in found) is (size > MAX_STEPS)


@pytest.mark.parametrize("size", [MAX_ACCEPTANCE, MAX_ACCEPTANCE + 1])
def test_acceptance_threshold(size: int) -> None:
    criterion = "GET /login con un token expirado devuelve el mismo status que antes del cambio."
    found = codes(make_valid_draft(acceptance=[criterion] * size))
    assert ("E_ACCEPTANCE_TOO_MANY" in found) is (size > MAX_ACCEPTANCE)


@pytest.mark.parametrize("size", [MAX_SCOPE_ITEMS, MAX_SCOPE_ITEMS + 1])
def test_scope_threshold(size: int) -> None:
    found = codes(make_valid_draft(scope=Scope(include=["x"] * size, exclude=["y"])))
    assert ("E_SCOPE_TOO_BIG" in found) is (size > MAX_SCOPE_ITEMS)


@pytest.mark.parametrize(
    ("title", "length"),
    [("x" * MAX_TITLE, MAX_TITLE), ("x" * (MAX_TITLE + 1), MAX_TITLE + 1)],
)
def test_title_border(title: str, length: int) -> None:
    found = codes(make_valid_draft(title=title))
    assert ("E_TITLE_MISSING" in found) is (length > MAX_TITLE)


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ("Crear el módulo auth/verify.py con la función pura de verificación.", False),
        ("Mover la lógica del handler a un módulo nuevo y dedicado.", False),
        ("Hacer que el módulo de auth quede testeable de forma aislada.", False),
        # El inspector oficial usó este paso y el linter lo rechazó: bug real.
        ("Verificar que aparece en el board con el codigo asignado.", False),
        ("Comprobar que el endpoint responde igual antes y despues.", False),
        ("Migrar el esquema de sesiones al formato nuevo.", False),
        ("Habilitar la variable de entorno para el despliegue.", False),
        # Tampoco estos: sustantivos, no verbos.
        ("Refactorizacion del modulo de autenticacion completo.", True),
        ("Prueba de integracion contra la base real.", True),
        # Verbos cortos e irregulares: el filtro por longitud los dejaba fuera.
        ("Leer el ticket de nuevo y comparar el codigo.", False),
        ("Ver que el recurso del skill responde.", False),
        ("Hacer un backup antes de tocar el esquema.", False),
        ("Seguir la cadena de errores hasta la causa raiz.", False),
    ],
)
def test_step_verb_detected(step: str, expected: bool) -> None:
    found = codes(make_valid_draft(steps=[step]))
    assert ("E_STEPS_VAGUE" in found) is expected


@pytest.mark.parametrize(
    ("criterion", "expected"),
    [
        ("Funciona correctamente", True),
        ("El login anda bien", True),
        ("Should work as expected", True),
        ("Funciona bien y no falla", True),
        (
            "GET /login con un token expirado devuelve 401 y el body no cambia.",
            False,
        ),
        (
            "El comando `uv run pytest backend/tests/auth` termina con todos los tests en verde.",
            False,
        ),
    ],
)
def test_acceptance_vague_detection(criterion: str, expected: bool) -> None:
    found = codes(make_valid_draft(acceptance=[criterion]))
    assert ("E_ACCEPTANCE_VAGUE" in found) is expected


@pytest.mark.parametrize(
    ("criterion", "expected"),
    [
        # Falsos positivos que el primer diseño producía: criterio corto con
        # palabras funcionales ("no", "se") que no lo hacen vago.
        ("El módulo nuevo no importa nada de app.api.", False),
        ("No cambia el status code del endpoint.", False),
        ("El boton de login cambia a verde y muestra el error al usuario.", False),
        # Y los vagos reales, cortos o largos.
        ("Anda bien y no falla nada.", True),
        ("Todo ok", True),
        ("Funciona correctamente y no debería fallar.", True),
        ("Debe andar bien con cualquier dato.", True),
        ("Bien", True),
    ],
)
def test_vague_detection_has_no_false_positives(criterion: str, expected: bool) -> None:
    found = codes(make_valid_draft(acceptance=[criterion]))
    assert ("E_ACCEPTANCE_VAGUE" in found) is expected


def test_long_enough_criterion_with_vague_token_is_accepted() -> None:
    """Una frase larga con palabras vagas dentro sigue siendo comprobable."""
    criterion = (
        "El botón de login cambia a verde y muestra el error de credenciales inválidas al usuario."
    )
    assert "E_ACCEPTANCE_VAGUE" not in codes(make_valid_draft(acceptance=[criterion]))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"scope": Scope(include=["a"], exclude=[])}, "W_NO_EXCLUDE"),
        ({"risks": []}, "W_NO_RISKS"),
    ],
)
def test_warnings_do_not_block(overrides: dict, expected: str) -> None:  # noqa: ANN401
    result = lint(make_valid_draft(**overrides))
    assert expected in {w.code for w in warnings(result)}
    # Un warning nunca bloquea: no aparece entre los errores.
    assert all(v.code != expected for v in errors(result))


def test_deterministic() -> None:
    draft = make_invalid_draft()
    first = [v.model_dump() for v in lint(draft)]
    for _ in range(50):
        assert [v.model_dump() for v in lint(draft)] == first


def test_violation_shape_is_agent_actionable() -> None:
    for violation in lint(make_invalid_draft()):
        assert violation.code.startswith(("E_", "W_"))
        assert violation.field
        assert violation.message
        assert violation.fix
