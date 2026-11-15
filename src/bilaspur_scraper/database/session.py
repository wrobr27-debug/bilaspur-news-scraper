"""Engine/session setup for SQLite v1 (`PLAN.md` §11, §11.1).

WAL mode + enforced foreign keys. A single `DATABASE_URL` is resolved
once by configuration and passed in - never hardcoded in modules.
"""

from __future__ import annotations

import logging
from pathlib import Path

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """SQLite cannot create its parent directory; do it once, here."""
    if not database_url.startswith("sqlite:///"):
        return
    raw = database_url.removeprefix("sqlite:///")
    if not raw or raw.startswith("/"):  # absolute POSIX path or :memory:
        return
    parent = Path(raw).expanduser().parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str) -> Engine:
    _ensure_sqlite_parent_dir(database_url)
    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    # Import the media models so their tables join the metadata.
    from . import media_models  # noqa: F401
    from .models import Base

    Base.metadata.create_all(engine)
    ensure_schema_columns(engine)


def _column_ddl(column, engine: Engine) -> str:
    """Compile one column for `ALTER TABLE ... ADD COLUMN` (SQLite).

    SQLite only accepts a NOT NULL column when a constant default is
    supplied, so the server default travels with the type. A NOT NULL
    column without a server default is added nullable instead (the ORM
    default still applies on insert) and the deviation is logged.
    """
    ddl = column.type.compile(engine.dialect)
    server_default = column.server_default
    default_sql = None
    if server_default is not None:
        arg = getattr(server_default, "arg", server_default)
        default_sql = (f"'{arg}'" if isinstance(arg, str) else str(arg))
    if default_sql is not None:
        ddl += f" DEFAULT {default_sql}"
    if not column.nullable:
        if default_sql is None:
            logger.warning("column %s is NOT NULL without a server default; "
                           "adding it nullable", column.name)
        else:
            ddl += " NOT NULL"
    return ddl


def ensure_schema_columns(engine: Engine) -> list[str]:
    """Additive migration: add columns that exist in the models but not in
    an older database file.

    `create_all` only creates missing *tables*; a database created before
    a model change keeps the old shape forever. This step adds any new
    column with `ALTER TABLE ... ADD COLUMN` (SQLite-friendly, no data
    loss) and creates the matching indexes. Returns the applied changes.
    """
    from sqlalchemy import inspect

    from .models import Base

    inspector = inspect(engine)
    applied: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = _column_ddl(column, engine)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'
                ))
                applied.append(f"{table.name}.{column.name}")
                logger.info("schema migration: added column %s.%s",
                            table.name, column.name)
        # indexes for freshly added columns
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            for index in table.indexes:
                index_name = index.name
                if not index_name:
                    continue
                exists = conn.execute(
                    text("SELECT count(*) FROM sqlite_master WHERE type='index' "
                         "AND name = :name"),
                    {"name": index_name},
                ).scalar_one()
                if exists:
                    continue
                cols = ", ".join(f'"{c.name}"' for c in index.columns)
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                    f'ON "{table.name}" ({cols})'
                ))
                applied.append(f"index {index_name}")
    return applied


def schema_version(engine: Engine) -> int:
    """Cheap existence check used by the CLI to report DB state."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM sqlite_master WHERE type='table'")
        ).scalar_one()
    return int(rows)


@contextmanager
def open_session(database_url: str):
    """One-shot session: engine + schema (idempotent) + Session."""
    engine = create_db_engine(database_url)
    create_schema(engine)
    session = Session(engine, future=True)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
