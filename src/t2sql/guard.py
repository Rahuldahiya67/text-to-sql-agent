"""SQL guardrails: parse the generated SQL and only let safe, bounded SELECT queries through.

Checks (all done on the parsed syntax tree, not with regexes):
1. exactly one statement
2. it is a query (SELECT / WITH ... SELECT / UNION), nothing that writes or changes schema
3. every table it reads is on the allow-list (CTE names are allowed)
4. no dangerous functions (sleep, file access, extensions)
5. a LIMIT is present and not above ``max_rows``
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Grant, exp.TruncateTable, exp.Copy, exp.Set, exp.Pragma, exp.Into,
)
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
    "dblink", "dblink_exec", "load_extension", "readfile", "writefile", "pg_terminate_backend", "set_config",
}


class UnsafeSQLError(ValueError):
    """Raised when generated SQL fails a guardrail. The message is safe to show the LLM and the user."""


@dataclass
class CheckedSQL:
    sql: str
    tables: list[str]
    limit_applied: bool


def check_sql(sql: str, allowed_tables: set[str], dialect: str, max_rows: int = 200) -> CheckedSQL:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        raise UnsafeSQLError(f"could not parse SQL: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("only a single statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError(f"only SELECT queries are allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"{node.key.upper()} is not allowed in a read-only query")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            fname = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if fname in FORBIDDEN_FUNCTIONS:
                raise UnsafeSQLError(f"function {fname}() is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    used = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in cte_names:
            continue
        if table.args.get("db") and table.text("db").lower() not in {"public", "main"}:
            raise UnsafeSQLError(f"schema-qualified table {table.sql()} is not allowed")
        if name not in allowed:
            raise UnsafeSQLError(f"table '{table.name}' does not exist or is not allowed. Available: {', '.join(sorted(allowed))}")
        used.append(name)

    limit_applied = False
    limit_node = tree.args.get("limit")
    current = None
    if limit_node is not None:
        try:
            current = int(limit_node.expression.this) if limit_node.expression is not None else None
        except (TypeError, ValueError):
            current = None
    if current is None or current > max_rows:
        if isinstance(tree, exp.Select):
            tree = tree.limit(max_rows)
        else:                                 # UNION etc.: wrap so the limit applies to the whole result
            tree = exp.select("*").from_(tree.subquery("q")).limit(max_rows)
        limit_applied = True

    return CheckedSQL(sql=tree.sql(dialect=dialect, pretty=True), tables=sorted(set(used)), limit_applied=limit_applied)
