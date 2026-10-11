"""Ties it together: question -> SQL (offline planner or LLM agent) -> guardrails -> execute -> answer."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .agent import SYSTEM_PROMPT, AgentError, ChatModel, SQLAgent, build_model
from .config import Settings, get_settings
from .db import QueryError, QueryResult, create_readonly_engine, dialect_of, introspect, render_schema, run_query
from .guard import UnsafeSQLError, check_sql
from .memory import ConversationStore, build_memory
from .planner import PlanError, QueryPlan, compile_plan, describe_plan, parse_question
from .semantic import SemanticLayer, select_tables


@dataclass
class Answer:
    question: str
    session_id: str
    ok: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    summary: str = ""
    title: str = ""
    chart: dict | None = None
    tables_used: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    engine: str = "offline-planner"
    truncated: bool = False
    elapsed_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _fmt(value: Any, kind: str = "decimal") -> str:
    if value is None:
        return "-"
    if not isinstance(value, (int, float)):
        return str(value)
    if kind == "currency":
        return f"Rs {value / 1e7:,.2f} Cr" if abs(value) >= 1e7 else (f"Rs {value / 1e5:,.2f} lakh" if abs(value) >= 1e5 else f"Rs {value:,.0f}")
    if kind == "percent":
        return f"{value:.1f}%"
    if kind == "integer":
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def suggest_chart(columns: list[str], rows: list[list[Any]], time_columns: set[str]) -> dict | None:
    """Line chart for time series, bar chart for one category + one number, nothing otherwise."""
    if len(rows) < 2 or len(columns) < 2:
        return None
    numeric = [i for i in range(len(columns)) if all(isinstance(r[i], (int, float)) or r[i] is None for r in rows)]
    text = [i for i in range(len(columns)) if i not in numeric]
    if not numeric:
        return None
    y = columns[numeric[-1]]
    x_idx = text[0] if text else (numeric[0] if len(numeric) > 1 else None)
    if x_idx is None:
        return None
    x = columns[x_idx]
    if x in time_columns or x.lower() in {"month", "date", "year", "quarter", "week", "day"}:
        series = columns[text[1]] if len(text) > 1 else None
        return {"type": "line", "x": x, "y": y, "series": series}
    if len(text) > 1:
        return {"type": "bar", "x": x, "y": y, "series": columns[text[1]]}
    return {"type": "bar", "x": x, "y": y, "series": None}


class TextToSQLService:
    def __init__(self, settings: Settings | None = None, model: ChatModel | None = None, memory: ConversationStore | None = None):
        self.settings = settings or get_settings()
        self.engine = create_readonly_engine(self.settings.database_url, self.settings.statement_timeout_ms)
        self.dialect = dialect_of(self.settings.database_url)
        self.tables = introspect(self.engine)
        self.layer = SemanticLayer.load()
        self.layer.learn_values(self.engine, self.dialect)
        self.model = model if model is not None else build_model(self.settings)
        self.memory = memory or build_memory(self.settings)

    # ---------------------------------------------------------------- helpers
    @property
    def engine_name(self) -> str:
        return self.model.name if self.model else "offline-planner"

    def _execute_checked(self, sql: str) -> tuple[str, QueryResult, list[str]]:
        checked = check_sql(sql, set(self.tables), self.dialect, self.settings.max_rows)
        result = run_query(self.engine, checked.sql, self.settings.max_rows)
        return checked.sql, result, checked.tables

    def _metrics_prompt(self) -> str:
        lines = []
        for m in self.layer.metrics.values():
            lines.append(f"- {m.key}: {m.sql_for(self.dialect)}  ({m.label}; joins orders o to {', '.join(m.joins) or 'nothing'})")
        joins = "; ".join(f"{a} = {j['table']} ON {j['condition']}" for a, j in self.layer.joins.items())
        return "\n".join(lines) + f"\nAliases used above: o = orders; {joins}"

    def _summarize(self, plan: QueryPlan | None, columns: list[str], rows: list[list[Any]]) -> str:
        if not rows:
            return "The query returned no rows for these filters."
        kind = self.layer.metrics[plan.metric].format if plan else "decimal"
        label = self.layer.metrics[plan.metric].label if plan else columns[-1]
        if len(columns) == 1 and len(rows) == 1:
            return f"{label}: {_fmt(rows[0][0], kind)}."
        vals = [r[-1] for r in rows if isinstance(r[-1], (int, float))]
        if not vals:
            return f"{len(rows)} rows returned."
        is_time = plan is not None and plan.dimensions and self.layer.dimensions[plan.dimensions[0]].time
        if is_time and len(plan.dimensions) == 1 and len(rows) > 1:
            first, last = rows[0], rows[-1]
            change = ""
            if isinstance(first[-1], (int, float)) and first[-1]:
                pct = (last[-1] - first[-1]) / abs(first[-1]) * 100
                change = f" ({pct:+.0f}%)"
            peak = max(rows, key=lambda r: r[-1] or 0)
            return (f"{label} went from {_fmt(first[-1], kind)} in {first[0]} to {_fmt(last[-1], kind)} in {last[0]}{change}; "
                    f"the peak was {peak[0]} at {_fmt(peak[-1], kind)}.")
        top = rows[0]
        name = " / ".join(str(v) for v in top[:-1])
        short = label.split(" (")[0]
        short = short if short.isupper() or short[:2].isupper() else short[0].lower() + short[1:]
        if len(rows) == 1:
            word = "lowest" if plan and plan.ascending else "highest"
            return f"{name} has the {word} {short}: {_fmt(top[-1], kind)}."
        if kind in {"percent", "decimal"} or not plan:
            return f"{name} ranks {'lowest' if plan and plan.ascending else 'first'} at {_fmt(top[-1], kind)} across {len(rows)} groups."
        total = sum(vals)
        share = f" ({top[-1] / total * 100:.0f}% of the total shown)" if total and len(rows) > 1 else ""
        word = "lowest" if plan and plan.ascending else "highest"
        return f"{name} has the {word} {short} at {_fmt(top[-1], kind)}{share}."

    # ---------------------------------------------------------------- main entry
    def ask(self, question: str, session_id: str | None = None) -> Answer:
        session_id = session_id or uuid.uuid4().hex[:12]
        history = self.memory.history(session_id, self.settings.history_turns)
        previous_plan = next((QueryPlan.from_dict(h["plan"]) for h in reversed(history) if h.get("plan")), None)
        ans = Answer(question=question, session_id=session_id, ok=False, engine=self.engine_name)

        plan: QueryPlan | None = None
        try:
            if self.model is None:
                plan = parse_question(question, self.layer, previous_plan)
                sql = compile_plan(plan, self.layer, self.dialect)
                checked_sql, result, used = self._execute_checked(sql)
                ans.attempts = [{"sql": checked_sql, "error": None}]
                ans.title = describe_plan(plan, self.layer)
            else:
                checked_sql, result, used, summary, attempts = self._ask_llm(question, history)
                ans.attempts, ans.summary = attempts, summary or ""
                ans.title = question
        except (PlanError, UnsafeSQLError, QueryError, AgentError) as exc:
            ans.error = str(exc)
            if isinstance(exc, (PlanError,)):
                ans.summary = str(exc)
            self.memory.append(session_id, {"question": question, "sql": None, "plan": None, "error": ans.error})
            return ans

        ans.ok = True
        ans.sql = checked_sql
        ans.columns = result.columns
        ans.rows = [[_jsonable(v) for v in row] for row in result.rows]
        ans.truncated, ans.elapsed_ms = result.truncated, result.elapsed_ms
        ans.tables_used = used
        if not ans.summary:
            ans.summary = self._summarize(plan, ans.columns, ans.rows)
        time_cols = {d for d in (plan.dimensions if plan else []) if self.layer.dimensions[d].time}
        ans.chart = suggest_chart(ans.columns, ans.rows, time_cols)
        self.memory.append(session_id, {"question": question, "sql": checked_sql, "plan": plan.to_dict() if plan else None})
        return ans

    def _ask_llm(self, question: str, history: list[dict]):
        assert self.model is not None
        names = select_tables(question + " " + " ".join(h["question"] for h in history[-2:]), self.tables, self.layer)
        system = SYSTEM_PROMPT.format(
            dialect=self.dialect, metrics=self._metrics_prompt(),
            schema=render_schema(self.tables, names, self.layer.table_descriptions()),
        )
        last: dict = {}

        def execute(sql: str) -> dict:
            try:
                checked_sql, result, used = self._execute_checked(sql)
            except (UnsafeSQLError, QueryError) as exc:
                return {"error": str(exc)}
            last.update(sql=checked_sql, result=result, used=used)
            return {"sql": checked_sql, "columns": result.columns, "rows": [[_jsonable(v) for v in r] for r in result.rows]}

        agent = SQLAgent(self.model, max_attempts=self.settings.max_attempts)
        out = agent.run(question, system, history, execute)
        if not last or last["sql"] != out.sql:
            # final SQL differs from the last executed one: run it so the returned rows match
            res = execute(out.sql or "")
            if "error" in res:
                raise QueryError(res["error"])
        return last["sql"], last["result"], last["used"], out.summary, out.attempts

    def schema(self) -> dict:
        return {
            name: {
                "rows": t.row_count,
                "description": self.layer.tables.get(name, {}).get("description"),
                "columns": [{"name": c.name, "type": c.type, "pk": c.primary_key, "fk": c.foreign_key} for c in t.columns],
            }
            for name, t in self.tables.items()
        }
