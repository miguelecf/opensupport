"""Tickets admiten dos modalidades de ingreso y contexto de soporte.

`intake_mode` separa "me passeritgo y no lo investigué" (incident) de "ya sé
qué hay que cambiar" (implementation). Es un eje distinto del `type`: un bug
puede entrar como incidente y conservar esa identidad mientras se investiga.

`support_context` guarda síntoma, lo observado, entorno y lo que no se sabe.
Es lo que permite admitir un incidente sin acceso al repositorio: la causa y
la solución no son requisitos de entrada.

Es una migración aditiva: dos columnas nuevas, sin tocar las anteriores. Los
tickets ya guardados nacieron 'implementation', que es lo que su contrato
exigía, así que no hay datos que reparar.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0003_support_intake"
down_revision: str | None = "0002_ticket_code_per_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column(
            "intake_mode",
            sa.String(16),
            nullable=False,
            server_default="implementation",
        ),
    )
    op.add_column("tickets", sa.Column("support_context", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("support_context")
        batch.drop_column("intake_mode")
