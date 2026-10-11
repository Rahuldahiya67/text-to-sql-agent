"""Database access: a read-only engine, schema introspection and guarded query execution.

Read-only is enforced at the connection level, not only by the SQL validator:
* SQLite opens the file with ``mode=ro``, so writes fail inside the database engine.
* PostgreSQL sessions are set to ``default_transaction_read_only = on`` with a
  ``statement_timeout``.
For production, also connect with a database role that only has SELECT grants.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool = False
    foreign_key: str | None = None        # "table.column"


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    row_count: int = 0
    sample_values: dict[str, list[Any]] = field(default_factory=dict)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    elapsed_ms: float
    truncated: bool = False


class QueryError(Exception):
    """The database rejected or failed a query. The message is passed back to the LLM for self-correction."""


def dialect_of(url: str) -> str:
    name = make_url(url).get_backend_name()
    return {"postgresql": "postgres", "sqlite": "sqlite", "mysql": "mysql"}.get(name, name)


def create_readonly_engine(url: str, statement_timeout_ms: int = 5000) -> Engine:
    u = make_url(url)
    backend = u.get_backend_name()

    if backend == "sqlite":
        db_path = u.database
        if not db_path or db_path == ":memory:":
            raise ValueError("Use a file-backed SQLite database for the read-only engine")
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found. Run `t2sql seed` first.")
        uri = f"file:{Path(db_path).resolve()}?mode=ro"
        engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False))
        engine.dialect.statement_timeout_ms = statement_timeout_ms  # read by run_query
        return engine

    connect_args: dict[str, Any] = {}
    if backend == "postgresql":
        connect_args["options"] = f"-c default_transaction_read_only=on -c statement_timeout={int(statement_timeout_ms)}"
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    engine.dialect.statement_timeout_ms = statement_timeout_ms
    return engine


def introspect(engine: Engine, sample_columns_max_distinct: int = 25) -> dict[str, TableInfo]:
    """Tables, columns, keys, row counts and a few sample values for low-cardinality text columns.
    Sample values let the LLM write filters with the right spelling ("North", not "north")."""
    insp = inspect(engine)
    tables: dict[str, TableInfo] = {}
    with engine.connect() as conn:
        for tname in insp.get_table_names():
            pk = set(insp.get_pk_constraint(tname).get("constrained_columns") or [])
            fks = {}
            for fk in insp.get_foreign_keys(tname):
                for col, ref in zip(fk["constrained_columns"], fk["referred_columns"]):
                    fks[col] = f"{fk['referred_table']}.{ref}"
            cols = [
                ColumnInfo(c["name"], str(c["type"]), bool(c.get("nullable", True)), c["name"] in pk, fks.get(c["name"]))
                for c in insp.get_columns(tname)
            ]
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar_one()
            samples: dict[str, list[Any]] = {}
            for c in cols:
                is_text = any(t in c.type.upper() for t in ("CHAR", "TEXT", "STRING"))
                if is_text and not c.primary_key and not c.foreign_key:
                    vals = conn.execute(
                        text(f'SELECT DISTINCT "{c.name}" FROM "{tname}" WHERE "{c.name}" IS NOT NULL LIMIT :n'),
                        {"n": sample_columns_max_distinct + 1},
                    ).scalars().all()
                    if len(vals) <= sample_columns_max_distinct:
                        samples[c.name] = sorted(vals)
            tables[tname] = TableInfo(tname, cols, count, samples)
    return tables


def run_query(engine: Engine, sql: str, max_rows: int) -> QueryResult:
    timeout_ms = getattr(engine.dialect, "statement_timeout_ms", 5000)
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            if isinstance(raw, sqlite3.Connection):
                deadline = started + timeout_ms / 1000
                # returning non-zero aborts the running statement
                raw.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [list(r) for r in result.fetchmany(max_rows + 1)]
            if isinstance(raw, sqlite3.Connection):
                raw.set_progress_handler(None, 0)
    except Exception as exc:  # noqa: BLE001 - surface the DB's message to the caller
        msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        if "interrupted" in msg.lower():
            msg = f"query exceeded the {timeout_ms} ms time limit"
        raise QueryError(msg) from exc
    elapsed = (time.perf_counter() - started) * 1000
    truncated = len(rows) > max_rows
    return QueryResult(columns, rows[:max_rows], round(elapsed, 1), truncated)


def render_schema(tables: dict[str, TableInfo], names: list[str] | None = None, descriptions: dict | None = None) -> str:
    """Compact schema description for LLM prompts."""
    descriptions = descriptions or {}
    out = []
    for name in names or list(tables):
        t = tables[name]
        tdesc = descriptions.get(name, {}).get("description", "")
        out.append(f"TABLE {name} ({t.row_count} rows){' -- ' + tdesc if tdesc else ''}")
        col_desc = descriptions.get(name, {}).get("columns", {})
        for c in t.columns:
            bits = [c.type]
            if c.primary_key:
                bits.append("PK")
            if c.foreign_key:
                bits.append(f"FK -> {c.foreign_key}")
            line = f"  {c.name} {' '.join(bits)}"
            if c.name in col_desc:
                line += f" -- {col_desc[c.name]}"
            if c.name in t.sample_values:
                line += f" values: {', '.join(map(str, t.sample_values[c.name][:12]))}"
            out.append(line)
    return "\n".join(out)
