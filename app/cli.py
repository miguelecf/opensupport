"""CLI de OpenSupport. `opensupport init --demo` deja todo listo para conectar un agente."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.auth.tokens import Principal
from app.config import settings
from app.domain.enums import EventKind
from app.logging_setup import RedactingFilter

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def _migrate() -> None:
    if settings.database_url.startswith("sqlite:///"):
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(), "head")


def cmd_init(args: argparse.Namespace) -> int:
    """Crea la DB, el admin, un proyecto demo y un token. Imprime el bloque de conexión."""
    _migrate()
    from app.db.base import session_scope

    with session_scope() as db:
        from app.auth.tokens import create_token, ensure_user
        from app.db.base import utcnow
        from app.domain.models import Project
        from app.tickets.service import ensure_agent

        user = ensure_user(db, args.user, is_admin=True)
        agent = ensure_agent(
            db, _as_principal(user.id, user.name, args.user), args.agent, "claude_code"
        )
        if args.project:
            exists = db.query(Project).filter(Project.slug == args.project).one_or_none()
            if exists is None:
                db.add(Project(slug=args.project, name=args.project, created_at=utcnow()))
                db.flush()
        plain, _ = create_token(
            db,
            user=user,
            name=f"{args.agent}-inicial",
            agent=agent,
            project_scope=[args.project] if args.project else [],
        )
        principal = _as_principal(user.id, user.name, args.agent)
        if args.demo:
            from app.demo import seed

            seeded = seed(db, principal)
        db.commit()

    if args.demo:
        print(
            f"\nEjemplo cargado: ticket {seeded.get('ticket') or 'OS-001'}"
            + (f" · sesión {seeded['session']}" if seeded.get("session") else "")
        )
    print(f"\nBase de datos: {settings.sqlite_path}")
    print("\nConectá tu agente pegando este bloque:\n")
    print(
        f"── Claude Code ──\nclaude mcp add --transport http opensupport "
        f'{args.host}/mcp \\\n  --header "Authorization: Bearer {plain}"\n'
    )
    print(f"── Codex ──\nexport OPENSUPPORT_TOKEN={plain}")
    print("# en ~/.codex/config.toml:\n[mcp_servers.opensupport]")
    print(f'url = "{args.host}/mcp"\nbearer_token_env_var = "OPENSUPPORT_TOKEN"\n')
    print(f"── Pi ──\nexport OPENSUPPORT_TOKEN={plain}")
    print(
        f"pi mcp add opensupport --url {args.host}/mcp --bearer-token-env-var OPENSUPPORT_TOKEN\n"
    )
    print(f"Token: {plain}   (solo se muestra una vez)\n")
    print("Después, en el chat del agente:")
    print('  "Conectate a opensupport, leé opensupport://skill/ticket-authoring y')
    print("   opensupport://workflow/support, recorré este repo y creá los tickets")
    print('   que falten."\n')
    return 0


def _as_principal(user_id: int, username: str, agent: str) -> Principal:
    return Principal(
        user_id=user_id,
        username=username,
        is_admin=True,
        agent_id=1,
        agent_name=agent,
        scopes=["tickets:read", "tickets:write"],
        project_scope=[],
    )


def cmd_create_token(args: argparse.Namespace) -> int:
    from app.auth.tokens import create_token, ensure_user, get_user
    from app.db.base import session_scope

    _migrate()
    with session_scope() as db:
        user = get_user(db, args.user) or ensure_user(db, args.user)
        plain, _ = create_token(
            db,
            user=user,
            name=args.name,
            scopes=["tickets:read", "tickets:write"],
            project_scope=args.projects,
        )
        db.commit()
    print(f"{args.name}: {plain}")
    print("Copiá ahora: el token no se vuelve a mostrar.")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    from app.auth.tokens import ensure_user
    from app.db.base import session_scope

    _migrate()
    with session_scope() as db:
        user = ensure_user(db, args.name, is_admin=args.admin)
        db.commit()
    print(f"Usuario '{user.name}' listo (admin={user.is_admin}).")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Usa la API backup de sqlite3: `cp` no es atómico con WAL."""
    _migrate()
    source = settings.sqlite_path
    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"opensupport-{stamp}.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = sorted(target_dir.glob("opensupport-*.db"))
    for old in backups[:-10]:
        old.unlink()
    print(f"Backup: {target} ({target.stat().st_size // 1024} KB). Quedan {len(backups[:10])}.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    target = settings.sqlite_path
    backup = Path(args.file)
    if not backup.exists():
        print(f"No existe el backup {backup}", file=sys.stderr)
        return 1
    if target.exists():
        shutil.copy2(target, target.with_suffix(".db.bak"))
    shutil.copy2(backup, target)
    print(f"Restaurado desde {backup}. Anterior guardado en {target.with_suffix('.db.bak')}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from app.db.base import session_scope
    from app.export import export_github_markdown, export_markdown

    _migrate()
    with session_scope() as db:
        if args.format == "gh":
            text_out = export_github_markdown(db, project=args.project)
        else:
            text_out = export_markdown(db, project=args.project)
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        print(f"Escrito en {args.out}")
    else:
        print(text_out)
    return 0


def cmd_lint_stats(args: argparse.Namespace) -> int:  # noqa: ARG001 - no recibe flags
    """Por qué rechazan los tickets los agentes.

    Es el único dato que justifica cambiar los umbrales del linter, así que
    tiene que ser visible sin abrir un SQLite a mano.
    """
    from sqlalchemy import desc, func, select

    from app.db.base import session_scope
    from app.domain.models import TicketEvent
    from app.tickets.schema import (
        MAX_ACCEPTANCE,
        MAX_SCOPE_ITEMS,
        MAX_STEPS,
        MAX_TITLE,
        MIN_OUTCOME,
        MIN_SUMMARY,
    )

    _migrate()
    with session_scope() as db:
        code_expr = func.json_extract(TicketEvent.payload, "$.code")
        count_expr = func.count(TicketEvent.id)
        rows = db.execute(
            select(code_expr, count_expr)
            .where(TicketEvent.kind == EventKind.LINT_FAILED)
            .group_by(code_expr)
            .order_by(desc(count_expr))
        ).all()
        total = sum(int(c) for _code, c in rows)

    if not total:
        print("Sin rechazos registrados: ningún draft falló el linter.")
        print("Los umbrales actuales son 12 pasos / 20 criterios / 15 items de scope.")
        return 0

    print(f"Rechazos del linter: {total} en total\n")
    width = max(len(str(c)) for _code, c in rows)
    for code, count in rows:
        bar = "#" * max(1, int(30 * int(count) / total))
        print(f"  {str(code):26} {str(count):>{width}}  {bar}")

    print(
        "\nUmbrales actuales: "
        f"{MAX_STEPS} pasos · {MAX_ACCEPTANCE} criterios · {MAX_SCOPE_ITEMS} items de scope · "
        f"título {MAX_TITLE} · summary {MIN_SUMMARY} · outcome {MIN_OUTCOME}"
    )
    top = rows[0][0]
    print(
        f"\nLa más frecuente es {top}. Si es *_TOO_MANY o *_TOO_BIG, subí el umbral en "
        "app/tickets/schema.py. Si es *_VAGUE o *_MISSING, el problema no es el número."
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ.setdefault("OPENSUPPORT_DATABASE_URL", settings.database_url)
    _migrate()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=1,  # SQLite exige un solo worker. No subirlo.
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Regla 4 de AGENTS.md: ningún token llega al log.
    logging.getLogger("app").addFilter(RedactingFilter())
    logging.getLogger("uvicorn.access").addFilter(RedactingFilter())
    logging.getLogger("uvicorn.error").addFilter(RedactingFilter())
    parser = argparse.ArgumentParser(
        prog="opensupport", description="Board de tickets agent-native"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Crea la DB y deja un token listo")
    p_init.add_argument("--demo", action="store_true", help="Datos de ejemplo + bloque de conexión")
    p_init.add_argument("--user", default="fede")
    p_init.add_argument("--agent", default="claude-code-fede")
    p_init.add_argument("--project", default="mi-tesis")
    p_init.add_argument("--host", default="http://127.0.0.1:8000")
    p_init.set_defaults(func=cmd_init)

    p_tok = sub.add_parser("create-token")
    p_tok.add_argument("--user", default="fede")
    p_tok.add_argument("--name", required=True)
    p_tok.add_argument("--projects", nargs="*", default=[])
    p_tok.set_defaults(func=cmd_create_token)

    p_user = sub.add_parser("create-user")
    p_user.add_argument("--name", required=True)
    p_user.add_argument("--admin", action="store_true")
    p_user.set_defaults(func=cmd_create_user)

    p_bak = sub.add_parser("backup")
    p_bak.add_argument("--dir", default="data/backups")
    p_bak.set_defaults(func=cmd_backup)

    p_res = sub.add_parser("restore")
    p_res.add_argument("file")
    p_res.set_defaults(func=cmd_restore)

    p_exp = sub.add_parser("export")
    p_exp.add_argument("--format", choices=["markdown", "gh"], default="markdown")
    p_exp.add_argument("--project", default=None)
    p_exp.add_argument("--out", default=None)
    p_exp.set_defaults(func=cmd_export)

    p_ls = sub.add_parser("lint-stats", help="Por qué rechazan los tickets los agentes")
    p_ls.set_defaults(func=cmd_lint_stats)

    p_srv = sub.add_parser("serve")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument("--log-level", default="info")
    p_srv.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
