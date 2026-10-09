"""Migración inicial: 11 tablas + FTS5 para búsqueda de tickets."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255)),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_ticket_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("client", sa.String(32), nullable=False, server_default="other"),
        sa.Column("model_reported", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("project_scope", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("conventions", sa.JSON()),
        sa.Column("next_session_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_ticket_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("project_id", "code", name="uq_session_project_code"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])

    op.create_table(
        "session_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(16), nullable=False, server_default="contributor"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) != (agent_id IS NOT NULL)", name="ck_participant_exactly_one_identity"
        ),
        sa.UniqueConstraint("session_id", "user_id", name="uq_participant_session_user"),
        sa.UniqueConstraint("session_id", "agent_id", name="uq_participant_session_agent"),
    )
    op.create_index("ix_session_participants_session_id", "session_participants", ["session_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("author_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("external_key", sa.String(200)),
        sa.Column("type", sa.String(16), nullable=False, server_default="feature"),
        sa.Column("priority", sa.String(4), nullable=False, server_default="p2"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("problem", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("acceptance", sa.JSON(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("estimate", sa.String(4)),
        sa.Column("verification", sa.JSON()),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "external_key", name="uq_ticket_project_external_key"),
    )
    op.create_index("ix_tickets_project_id", "tickets", ["project_id"])
    op.create_index("ix_tickets_session_id", "tickets", ["session_id"])
    op.create_index("ix_ticket_owner_status", "tickets", ["owner_user_id", "status"])
    op.create_index("ix_ticket_author", "tickets", ["author_agent_id"])

    op.create_table(
        "ticket_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="relates_to"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("from_ticket_id", "to_ticket_id", "kind", name="uq_link_once"),
        sa.CheckConstraint("from_ticket_id != to_ticket_id", name="ck_link_not_self"),
    )
    op.create_index("ix_ticket_links_from_ticket_id", "ticket_links", ["from_ticket_id"])
    op.create_index("ix_ticket_links_to_ticket_id", "ticket_links", ["to_ticket_id"])

    op.create_table(
        "ticket_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("reviewer_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("ticket_content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(reviewer_user_id IS NOT NULL) != (reviewer_agent_id IS NOT NULL)",
            name="ck_review_exactly_one_identity",
        ),
    )
    op.create_index("ix_review_ticket", "ticket_reviews", ["ticket_id"])

    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("author_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_comments_ticket_id", "comments", ["ticket_id"])

    op.create_table(
        "ticket_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(24), nullable=False, server_default="updated"),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_ticket", "ticket_events", ["ticket_id", "created_at"])

    # FTS5: tabla autonomous. No usamos content='tickets' porque las columnas
    # derivadas (acceptance_text, tags_text) no existen en la tabla de contenido
    # y FTS5 las necesita para las operaciones 'delete'.
    op.execute(
        """
        CREATE VIRTUAL TABLE tickets_fts USING fts5(
            code, title, summary, outcome, acceptance_text, tags_text,
            tokenize='unicode61'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER tickets_fts_ai AFTER INSERT ON tickets BEGIN
            INSERT INTO tickets_fts(rowid, code, title, summary, outcome, acceptance_text, tags_text)
            VALUES (new.id, new.code, new.title, new.summary, new.outcome,
                    json_extract(new.acceptance, '$'), json_extract(new.tags, '$'));
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tickets_fts_ad AFTER DELETE ON tickets BEGIN
            DELETE FROM tickets_fts WHERE rowid = old.id;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER tickets_fts_au AFTER UPDATE ON tickets BEGIN
            DELETE FROM tickets_fts WHERE rowid = old.id;
            INSERT INTO tickets_fts(rowid, code, title, summary, outcome, acceptance_text, tags_text)
            VALUES (new.id, new.code, new.title, new.summary, new.outcome,
                    json_extract(new.acceptance, '$'), json_extract(new.tags, '$'));
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tickets_fts_au")
    op.execute("DROP TRIGGER IF EXISTS tickets_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS tickets_fts_ai")
    op.execute("DROP TABLE IF EXISTS tickets_fts")
    for table in (
        "ticket_events",
        "comments",
        "ticket_reviews",
        "ticket_links",
        "tickets",
        "session_participants",
        "sessions",
        "projects",
        "api_tokens",
        "agents",
        "users",
    ):
        op.drop_table(table)
