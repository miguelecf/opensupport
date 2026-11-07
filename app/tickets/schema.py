"""Draft de ticket. Pydantic valida la forma; el linter valida el contenido."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import (
    EvidenceKind,
    IntakeMode,
    Priority,
    ResolutionKind,
    TicketType,
)

MAX_TITLE = 120
MIN_SUMMARY = 80
MIN_OUTCOME = 40
MIN_STEP = 15
MAX_STEPS = 12
MAX_ACCEPTANCE = 20
MAX_SCOPE_ITEMS = 15

TITLE_MAX_HINT = f"Máximo {MAX_TITLE} caracteres; fórmula: verbo + objeto + dónde."


class EvidenceItem(BaseModel):
    """Evidencia de un item. La forma depende del `kind`.

    Los items sin `kind` se interpretan como `code`, que es lo que produce
    el contrato histórico: un path relativo al repo con líneas opcionales.
    """

    kind: EvidenceKind = Field(
        default=EvidenceKind.CODE,
        description=(
            "code: lectura del repo. observation: lo visto. log: registro. "
            "command: comando ejecutado. reference: fuente externa citada."
        ),
    )
    path: str | None = Field(
        default=None, description="Path relativo al repo. Sin ruta absoluta ni '..'."
    )
    lines: str | None = Field(default=None, description="Rango 'a-b' o línea única.")
    note: str | None = None
    command: str | None = Field(default=None, description="Comando ejecutado, para kind='command'.")
    result: str | None = Field(default=None, description="Salida observada del comando.")
    url: str | None = Field(default=None, description="Referencia externa, para kind='reference'.")


class SupportContext(BaseModel):
    """Contexto de soporte: lo que se sabe del problema, sin inventar diagnóstico.

    Es lo que permite admitir un incidente sin acceso al repositorio: el
    síntoma y lo observado son datos, la causa y la solución no.
    """

    symptom: str | None = Field(default=None, description="Qué pasa, en una frase.")
    observed: str | None = Field(default=None, description="Qué se vio exactamente.")
    expected: str | None = Field(default=None, description="Comportamiento esperado, si se conoce.")
    impact: str | None = Field(default=None, description="A quién afecta y cómo.")
    environment: str | None = Field(
        default=None, description="Sistema, versión, contexto de ejecución."
    )
    reproduction: list[str] = Field(default_factory=list, description="Pasos para reproducir.")
    unknown: list[str] = Field(default_factory=list, description="Lo que todavía no se sabe.")


class Verification(BaseModel):
    """Comprobación observable: qué se corrió y qué se obtuvo."""

    result: str = Field(description="Resultado observado. No el nombre del comando: lo que pasó.")
    command: str | None = Field(default=None, description="Comando ejecutado, si hubo.")
    ran_at: str | None = Field(default=None, description="Momento de la ejecución (ISO 8601).")


class Resolution(BaseModel):
    """Cierre tipado. Distingue el arreglo de código de las otras salidas."""

    kind: ResolutionKind = Field(description="Clase de solución aplicada.")
    summary: str = Field(description="Qué se hizo, en pocas líneas.")
    user_response: str = Field(description="Respuesta para el usuario: qué hacer y qué pasó.")
    verifications: list[Verification] = Field(default_factory=list)
    pr: str | None = None
    commit: str | None = None
    commands: list[str] = Field(default_factory=list)


class Scope(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class Violation(BaseModel):
    code: str
    field: str
    message: str
    fix: str

    @property
    def is_warning(self) -> bool:
        return self.code.startswith("W_")


class TicketDraft(BaseModel):
    external_key: str | None = Field(
        default=None,
        description="Clave estable para idempotencia. Única por proyecto. Ej: 'mi-tesis:auth:extraer-jwt'.",
    )
    project: str = Field(description="Slug del proyecto.")
    owner_user: str | None = Field(
        default=None, description="Quién responde. Default: el dueño del agente."
    )
    session: str | None = Field(default=None, description="Código de sesión (PLN-002). Opcional.")

    title: str
    summary: str
    problem: str | None = None
    outcome: str

    type: TicketType = TicketType.FEATURE
    priority: Priority = Priority.P2

    intake_mode: IntakeMode = Field(
        default=IntakeMode.IMPLEMENTATION,
        description=(
            "incident: se reportó algo que pasa y no se investigó. "
            "implementation: ya se sabe qué hay que cambiar. "
            "Omitirlo equivale a 'implementation'."
        ),
    )
    support_context: SupportContext | None = Field(
        default=None,
        description="Síntoma, lo observado, impacto y entorno. Sin causa ni diagnóstico.",
    )

    evidence: list[EvidenceItem] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    scope: Scope = Field(default_factory=Scope)
    risks: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    estimate: str | None = None
    deps: list[str] = Field(default_factory=list)


class TicketPatch(BaseModel):
    """Cambios parciales sobre un ticket existente. Todo opcional."""

    title: str | None = None
    summary: str | None = None
    problem: str | None = None
    outcome: str | None = None
    type: TicketType | None = None
    priority: Priority | None = None
    intake_mode: IntakeMode | None = None
    support_context: SupportContext | None = None
    evidence: list[EvidenceItem] | None = None
    steps: list[str] | None = None
    acceptance: list[str] | None = None
    scope: Scope | None = None
    risks: list[str] | None = None
    tags: list[str] | None = None
    estimate: str | None = None
    deps: list[str] | None = None


def draft_from_dict(data: dict[str, Any], session: str | None = None) -> TicketDraft:
    """Arma un TicketDraft desde un dict plano (items de lote o MCP).

    Vive acá para que el lote y la tool single usen exactamente el mismo
    contrato: si divergen, el agente lintea una cosa y crea otra.
    """
    scope = data.get("scope") or {}
    return TicketDraft(
        project=data.get("project") or "",
        title=data.get("title") or "",
        summary=data.get("summary") or "",
        outcome=data.get("outcome") or "",
        problem=data.get("problem"),
        type=data.get("type") or "feature",
        priority=data.get("priority") or "p2",
        intake_mode=data.get("intake_mode") or "implementation",
        support_context=data.get("support_context"),
        evidence=data.get("evidence") or [],
        steps=data.get("steps") or [],
        acceptance=data.get("acceptance") or [],
        scope={"include": scope.get("include", []), "exclude": scope.get("exclude", [])},
        risks=data.get("risks") or [],
        tags=data.get("tags") or [],
        estimate=data.get("estimate"),
        deps=data.get("deps") or [],
        external_key=data.get("external_key"),
        owner_user=data.get("owner_user"),
        session=data.get("session", session),
    )


def compute_content_hash(parts: dict[str, Any]) -> str:
    """Hash estable del contenido sustantivo del ticket (ignora status y owner)."""
    payload = {
        "title": parts.get("title", ""),
        "summary": parts.get("summary", ""),
        "outcome": parts.get("outcome", ""),
        "problem": parts.get("problem", ""),
        "evidence": parts.get("evidence", []),
        "steps": parts.get("steps", []),
        "acceptance": parts.get("acceptance", []),
        "scope": parts.get("scope", {}),
        "risks": parts.get("risks", []),
        "tags": parts.get("tags", []),
        "intake_mode": parts.get("intake_mode", ""),
        "support_context": parts.get("support_context", {}),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
