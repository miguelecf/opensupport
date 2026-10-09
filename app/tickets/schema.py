"""Draft de ticket. Pydantic valida la forma; el linter valida el contenido."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import Priority, TicketType

MAX_TITLE = 120
MIN_SUMMARY = 80
MIN_OUTCOME = 40
MIN_STEP = 15
MAX_STEPS = 12
MAX_ACCEPTANCE = 20
MAX_SCOPE_ITEMS = 15

TITLE_MAX_HINT = f"Máximo {MAX_TITLE} caracteres; fórmula: verbo + objeto + dónde."


class EvidenceItem(BaseModel):
    path: str = Field(description="Path relativo al repo. Sin ruta absoluta ni '..'.")
    lines: str | None = Field(default=None, description="Rango 'a-b' o línea única.")
    note: str | None = None


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
    evidence: list[EvidenceItem] | None = None
    steps: list[str] | None = None
    acceptance: list[str] | None = None
    scope: Scope | None = None
    risks: list[str] | None = None
    tags: list[str] | None = None
    estimate: str | None = None
    deps: list[str] | None = None


def compute_content_hash(parts: dict[str, Any]) -> str:
    """Hash estable del contenido sustantivo del ticket (ignora status y owner)."""
    payload = {
        "title": parts.get("title", ""),
        "summary": parts.get("summary", ""),
        "outcome": parts.get("outcome", ""),
        "evidence": parts.get("evidence", []),
        "steps": parts.get("steps", []),
        "acceptance": parts.get("acceptance", []),
        "scope": parts.get("scope", {}),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
