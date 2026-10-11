"""Loads the semantic layer (semantic.yaml) and does schema-aware table selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .db import TableInfo

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    toks = _WORD.findall(text.lower())
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in toks]


@dataclass
class Measure:
    """A metric or a dimension."""

    key: str
    label: str
    sql: dict[str, str]
    joins: list[str]
    synonyms: list[str]
    grain: str = "order"
    where: str | None = None
    exclude_cancelled: bool = False
    format: str = "decimal"
    time: bool = False

    def sql_for(self, dialect: str) -> str:
        return self.sql.get(dialect, self.sql["default"])


@dataclass
class SemanticLayer:
    tables: dict[str, dict[str, Any]]
    joins: dict[str, dict[str, Any]]
    metrics: dict[str, Measure]
    dimensions: dict[str, Measure]
    # filterable values discovered from the database: value (lower) -> (dimension key, exact value)
    values: dict[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SemanticLayer":
        raw = (
            Path(path).read_text(encoding="utf-8")
            if path
            else resources.files("t2sql").joinpath("semantic.yaml").read_text(encoding="utf-8")
        )
        data = yaml.safe_load(raw)

        def measure(key: str, d: dict) -> Measure:
            sql = d["sql"] if isinstance(d["sql"], dict) else {"default": d["sql"]}
            return Measure(
                key=key, label=d.get("label", key), sql=sql, joins=list(d.get("joins", [])),
                synonyms=list(d.get("synonyms", [])) + [key.replace("_", " ")], grain=d.get("grain", "order"),
                where=d.get("where"), exclude_cancelled=bool(d.get("exclude_cancelled", False)),
                format=d.get("format", "decimal"), time=bool(d.get("time", False)),
            )

        return cls(
            tables=data.get("tables", {}),
            joins=data.get("joins", {}),
            metrics={k: measure(k, v) for k, v in data.get("metrics", {}).items()},
            dimensions={k: measure(k, v) for k, v in data.get("dimensions", {}).items()},
        )

    def table_descriptions(self) -> dict[str, dict]:
        return self.tables

    def learn_values(self, engine, dialect: str) -> None:
        """Read distinct values of low-cardinality text dimensions (regions, plants, categories, ...)
        so questions like "revenue in the North region" can be turned into filters."""
        from sqlalchemy import text

        from .planner import QueryPlan, compile_plan

        values: dict[str, tuple[str, str]] = {}
        with engine.connect() as conn:
            for key, dim in self.dimensions.items():
                if dim.time:
                    continue
                plan = QueryPlan(metric="orders", dimensions=[key], include_cancelled=True, limit=500)
                sql = compile_plan(plan, self, dialect, order_by_metric=False)
                rows = conn.execute(text(sql)).fetchall()
                if len(rows) > 100:          # too many values to be useful as keywords (e.g. none here)
                    continue
                for row in rows:
                    val = row[0]
                    if isinstance(val, str) and len(val) > 2:
                        values.setdefault(val.lower(), (key, val))
        self.values = values


def select_tables(question: str, tables: dict[str, TableInfo], layer: SemanticLayer, max_tables: int = 5) -> list[str]:
    """Pick the tables relevant to a question, then add the tables needed to join them.

    Scoring uses table names, column names, business synonyms and known data values.
    Keeping the prompt to the relevant tables matters on real warehouses with hundreds of tables:
    it cuts tokens and stops the model from joining the wrong table.
    """
    q = set(words(question))
    qtext = " " + " ".join(words(question)) + " "
    scores: dict[str, float] = {}
    for name, info in tables.items():
        meta = layer.tables.get(name, {})
        score = 0.0
        if set(words(name)) & q:
            score += 3
        for syn in meta.get("synonyms", []):
            if f" {' '.join(words(syn))} " in qtext:
                score += 2
        score += 0.5 * len(set(words(" ".join(info.column_names()))) & q)
        for col_vals in info.sample_values.values():
            if any(f" {' '.join(words(str(v)))} " in qtext for v in col_vals):
                score += 2
        if score:
            scores[name] = score

    picked = sorted(scores, key=scores.get, reverse=True)[:max_tables] or ["orders"]
    # add join paths: everything connects through orders in this schema; follow FKs generally
    fk_graph: dict[str, set[str]] = {n: set() for n in tables}
    for name, info in tables.items():
        for c in info.columns:
            if c.foreign_key:
                ref = c.foreign_key.split(".")[0]
                fk_graph[name].add(ref)
                fk_graph.setdefault(ref, set()).add(name)

    needed = set(picked)
    if len(needed) > 1:
        anchor = picked[0]
        for target in picked[1:]:
            path = _shortest_path(fk_graph, anchor, target)
            needed.update(path)
    return sorted(needed, key=lambda n: (n not in picked, picked.index(n) if n in picked else 0, n))


def _shortest_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    frontier, seen = [[start]], {start}
    while frontier:
        path = frontier.pop(0)
        if path[-1] == goal:
            return path
        for nxt in sorted(graph.get(path[-1], ())):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(path + [nxt])
    return [start, goal]
