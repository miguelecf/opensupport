"""tickets.code pasa a ser único por proyecto, igual que sessions.code

El código se numera por proyecto (projects.next_ticket_seq), así que dos
proyectos distintos generan legítimamente OS-001. Con el UNIQUE global de
tickets.code, el primer ticket del segundo proyecto moría con IntegrityError
y create_ticket devolvía E_INTERNAL en vez de un ticket.

SQLite no tiene DROP CONSTRAINT. El batch_alter_table de Alembic tampoco ayuda
acá: dropear y re-agregar la misma columna dentro de un batch deja
_alembic_tmp_tickets colgada. Por eso la tabla se reconstruye a mano, con el
mismo criterio que usa 0001_initial para la tabla FTS5.

No hay datos que reparar: el UNIQUE global nunca permitió dos tickets con el
mismo código, así que la tabla vieja es consistente con la nueva.

Revisa: app/domain/models.py:152
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_ticket_code_per_project"
down_revision: str | None = "0001_initial"
branch_labels = None
depends_on = None

_COLUMNS = """
    id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    session_id INTEGER,
    owner_user_id INTEGER NOT NULL,
    author_agent_id INTEGER,
    author_user_id INTEGER,
    external_key VARCHAR(200),
    type VARCHAR(16) NOT NULL DEFAULT 'feature',
    priority VARCHAR(4) NOT NULL DEFAULT 'p2',
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    title VARCHAR(200) NOT NULL,
    summary TEXT NOT NULL,
    problem TEXT,
    outcome TEXT NOT NULL,
    evidence JSON NOT NULL,
    steps JSON NOT NULL,
    acceptance JSON NOT NULL,
    scope JSON NOT NULL,
    risks JSON NOT NULL,
    tags JSON NOT NULL,
    estimate VARCHAR(4),
    verification JSON,
    content_hash VARCHAR(64) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ticket_project_external_key UNIQUE (project_id, external_key),
    CONSTRAINT uq_ticket_project_code UNIQUE (project_id, code),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT,
    FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    FOREIGN KEY(author_agent_id) REFERENCES agents (id) ON DELETE SET NULL,
    FOREIGN KEY(author_user_id) REFERENCES users (id) ON DELETE SET NULL
"""

_INDEXES = (
    ("ix_tickets_project_id", ["project_id"]),
    ("ix_tickets_session_id", ["session_id"]),
    ("ix_ticket_owner_status", ["owner_user_id", "status"]),
    ("ix_ticket_author", ["author_agent_id"]),
)


_TRIGGERS = (
    (
        "tickets_fts_ai",
        """
        CREATE TRIGGER tickets_fts_ai AFTER INSERT ON tickets BEGIN
            INSERT INTO tickets_fts(rowid, code, title, summary, outcome, acceptance_text, tags_text)
            VALUES (new.id, new.code, new.title, new.summary, new.outcome,
                    json_extract(new.acceptance, '$'), json_extract(new.tags, '$'));
        END
    """,
    ),
    (
        "tickets_fts_ad",
        """
        CREATE TRIGGER tickets_fts_ad AFTER DELETE ON tickets BEGIN
            DELETE FROM tickets_fts WHERE rowid = old.id;
        END
    """,
    ),
    (
        "tickets_fts_au",
        """
        CREATE TRIGGER tickets_fts_au AFTER UPDATE ON tickets BEGIN
            DELETE FROM tickets_fts WHERE rowid = old.id;
            INSERT INTO tickets_fts(rowid, code, title, summary, outcome, acceptance_text, tags_text)
            VALUES (new.id, new.code, new.title, new.summary, new.outcome,
                    json_extract(new.acceptance, '$'), json_extract(new.tags, '$'));
        END
    """,
    ),
)


def _rebuild(code_column: str) -> None:
    """Recrea `tickets` con la definición de `code` que le pasamos y copia los datos.

    Los triggers de FTS5 se caen con el DROP TABLE, así que los sacamos antes y
    los volvemos a crear después. Con los triggers puestos, el DELETE de cada
    fila borraría su entrada del índice; como las copiamos a ciegas, al final
    reconstruimos el índice entero con el comando 'rebuild' de FTS5.
    """
    for name, _ in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")

    op.execute("DROP TABLE IF EXISTS _tickets_rebuild")
    op.execute(f"CREATE TABLE _tickets_rebuild ({code_column}, {_COLUMNS})")
    columns = [
        "id",
        "code",
        "project_id",
        "session_id",
        "owner_user_id",
        "author_agent_id",
        "author_user_id",
        "external_key",
        "type",
        "priority",
        "status",
        "title",
        "summary",
        "problem",
        "outcome",
        "evidence",
        "steps",
        "acceptance",
        "scope",
        "risks",
        "tags",
        "estimate",
        "verification",
        "content_hash",
        "created_at",
        "updated_at",
    ]
    joined = ", ".join(columns)
    op.execute(f"INSERT INTO _tickets_rebuild ({joined}) SELECT {joined} FROM tickets")
    op.execute("DROP TABLE tickets")
    op.execute("ALTER TABLE _tickets_rebuild RENAME TO tickets")

    for name, cols in _INDEXES:
        op.create_index(name, "tickets", cols)
    for _, body in _TRIGGERS:
        op.execute(body)
    op.execute("INSERT INTO tickets_fts(tickets_fts) VALUES('rebuild')")


def upgrade() -> None:
    _rebuild("code VARCHAR(32) NOT NULL")


def downgrade() -> None:
    # Volver al UNIQUE global exige que no queden códigos repetidos entre
    # proyectos. Si esto falla con IntegrityError, hay que renumerar antes:
    # la restricción que estaba rota era la del UNIQUE global, no los datos.
    _rebuild("code VARCHAR(32) NOT NULL UNIQUE")
