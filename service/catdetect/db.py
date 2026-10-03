from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401  — регистрация таблиц


def make_engine(db_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Простая миграция: новые nullable-колонки добавляются в существующие таблицы."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                                      f"{col.type.compile(engine.dialect)}"))


def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
