"""Offline planner: turns a business question into a QueryPlan using the semantic layer, then into SQL.

This is the no-API-key path. It handles the shape most analytics questions take:
    <metric> [by <dimension(s)>] [for <filter values>] [in <year / month / quarter>] [top N]
plus follow-ups that modify the previous plan ("now split that by region", "only 2025",
"what about freight cost?", "top 3 instead").

Questions outside that shape raise PlanError; with an LLM provider configured the
agent handles them instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .semantic import Measure, SemanticLayer, words

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

ASC_WORDS = {"lowest", "least", "worst", "bottom", "fewest", "minimum", "min", "smallest", "slowest", "poorest"}
SUPERLATIVE_WORDS = {"highest", "most", "best", "top", "largest", "biggest", "maximum", "max", "leading", "fastest"} | ASC_WORDS
FOLLOWUP_MARKERS = {"now", "same", "that", "it", "this", "instead", "also", "only", "those", "these", "again"}


class PlanError(ValueError):
    """The question could not be mapped to the semantic layer."""


@dataclass
class Filter:
    dimension: str
    value: str


@dataclass
class QueryPlan:
    metric: str
    dimensions: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    year: int | None = None
    month: int | None = None          # requires year
    quarter: int | None = None        # requires year
    limit: int | None = None
    ascending: bool = False
    include_cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "metric": self.metric, "dimensions": list(self.dimensions),
            "filters": [{"dimension": f.dimension, "value": f.value} for f in self.filters],
            "year": self.year, "month": self.month, "quarter": self.quarter, "limit": self.limit,
            "ascending": self.ascending, "include_cancelled": self.include_cancelled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QueryPlan":
        d = dict(d)
        d["filters"] = [Filter(**f) for f in d.get("filters", [])]
        return cls(**d)


# ------------------------------------------------------------------------------------------ parsing
@dataclass
class _Match:
    start: int
    end: int
    kind: str          # metric | dimension | value
    key: str
    value: str | None = None


def _find_phrases(tokens: list[str], phrases: dict[tuple[str, ...], tuple[str, str, str | None]]) -> list[_Match]:
    """All phrase matches, then keep the longest non-overlapping ones."""
    found = []
    for phrase, (kind, key, value) in phrases.items():
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == phrase:
                found.append(_Match(i, i + n, kind, key, value))
    found.sort(key=lambda m: (-(m.end - m.start), m.start, {"value": 0, "metric": 1, "dimension": 2}[m.kind]))
    taken: set[int] = set()
    kept = []
    for m in found:
        span = set(range(m.start, m.end))
        if span & taken:
            continue
        taken |= span
        kept.append(m)
    return sorted(kept, key=lambda m: m.start)


def _phrase_index(layer: SemanticLayer) -> dict[tuple[str, ...], tuple[str, str, str | None]]:
    index: dict[tuple[str, ...], tuple[str, str, str | None]] = {}
    for key, (dim, val) in layer.values.items():
        index[tuple(words(key))] = ("value", dim, val)
    for key, m in layer.metrics.items():
        for syn in m.synonyms:
            index.setdefault(tuple(words(syn)), ("metric", key, None))
    for key, d in layer.dimensions.items():
        for syn in d.synonyms:
            index.setdefault(tuple(words(syn)), ("dimension", key, None))
    return index


def parse_question(question: str, layer: SemanticLayer, previous: QueryPlan | None = None) -> QueryPlan:
    q = question.lower()
    tokens = words(question)
    matches = _find_phrases(tokens, _phrase_index(layer))

    metric = next((m.key for m in matches if m.kind == "metric"), None)
    filters = [Filter(m.key, m.value) for m in matches if m.kind == "value"]
    filtered_dims = {f.dimension for f in filters}
    dims: list[str] = []
    for m in matches:
        if m.kind != "dimension" or m.key in dims:
            continue
        explicit = m.start > 0 and tokens[m.start - 1] in {"by", "per", "each", "across", "wise"}
        # "revenue in the North region": the word "region" names the filter, it is not a group-by
        if m.key in filtered_dims and not explicit:
            continue
        dims.append(m.key)

    # time filters
    year = month = quarter = None
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", q)]
    if years:
        year = years[-1]
    mq = re.search(r"\bq([1-4])\b", q)
    if mq:
        quarter = int(mq.group(1))
    for tok in re.findall(r"[a-z]+", q):
        if tok in MONTHS and not (tok == "may" and re.search(r"\bmay\s+(i|we|you)\b", q)):
            month = MONTHS[tok]
    if (month or quarter) and not year and previous and previous.year:
        year = previous.year

    # ranking
    limit, ascending = None, False
    n = re.search(r"\b(?:top|bottom|best|worst|highest|lowest|first)\s+(\d{1,3})\b", q)
    toks = set(re.findall(r"[a-z]+", q))
    if toks & ASC_WORDS:
        ascending = True
    if n:
        limit = int(n.group(1))
    elif toks & SUPERLATIVE_WORDS and re.match(r"\s*(which|who|what is the|what's the|name the)\b", q):
        limit = 1
    if limit and not dims and previous is None:
        # "top 5 customers by revenue": the dimension word came right after the number
        pass

    include_cancelled = "status" in dims or "cancel" in q

    has_marker = bool(set(tokens) & FOLLOWUP_MARKERS) or bool(re.match(r"\s*(what|how)\s+about\b", q))
    recognised = bool(metric or dims or filters or year or month or quarter or limit)
    is_followup = previous is not None and recognised and (metric is None or has_marker)
    if is_followup:
        assert previous is not None
        plan = replace(previous, filters=list(previous.filters), dimensions=list(previous.dimensions))
        if metric:
            plan.metric = metric
        if dims:
            plan.dimensions = dims if "instead" in toks or not plan.dimensions else (plan.dimensions + [d for d in dims if d not in plan.dimensions])[-2:]
            if not limit:
                plan.limit = None      # a new breakdown should show every group
        if filters:
            new_dims = {f.dimension for f in filters}
            plan.filters = [f for f in plan.filters if f.dimension not in new_dims] + filters
            plan.dimensions = [d for d in plan.dimensions if d not in new_dims or d in dims]
        if year:
            plan.year, plan.month, plan.quarter = year, month, quarter
        elif month or quarter:
            plan.month, plan.quarter = month, quarter
        if limit:
            plan.limit, plan.ascending = limit, ascending
        elif "all" in toks:
            plan.limit = None
        plan.include_cancelled = plan.include_cancelled or include_cancelled
        return plan

    if metric is None:
        hints = ", ".join(sorted(layer.metrics))
        raise PlanError(
            f"I couldn't tell which measure you want. Try asking about one of: {hints}. "
            "For example: 'revenue by region in 2025' or 'top 5 customers by orders'."
        )
    return QueryPlan(
        metric=metric, dimensions=dims[:2], filters=filters, year=year, month=month, quarter=quarter,
        limit=limit, ascending=ascending, include_cancelled=include_cancelled,
    )


# ------------------------------------------------------------------------------------------ compile
def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _join_order(layer: SemanticLayer, aliases: set[str]) -> list[str]:
    ordered: list[str] = []

    def visit(alias: str) -> None:
        for dep in layer.joins[alias].get("requires", []):
            visit(dep)
        if alias not in ordered:
            ordered.append(alias)

    for alias in sorted(aliases):
        visit(alias)
    return ordered


def compile_plan(plan: QueryPlan, layer: SemanticLayer, dialect: str, order_by_metric: bool = True) -> str:
    if plan.metric not in layer.metrics:
        raise PlanError(f"unknown metric {plan.metric}")
    metric: Measure = layer.metrics[plan.metric]
    dims = [layer.dimensions[d] for d in plan.dimensions]
    filter_dims = [layer.dimensions[f.dimension] for f in plan.filters]

    aliases = set(metric.joins)
    for d in dims + filter_dims:
        aliases.update(d.joins)
    joins = _join_order(layer, aliases)

    # Fan-out guard: a per-order amount (freight, delivery days) summed over item-level rows would
    # be counted once per line item. Refuse rather than return a silently wrong number.
    item_joins = [a for a in joins if layer.joins[a].get("grain") == "item"]
    if metric.grain == "order" and item_joins and "DISTINCT" not in metric.sql_for(dialect).upper():
        dim_names = ", ".join(d.label for d in dims + filter_dims if any(j in item_joins for j in d.joins)) or "product"
        raise PlanError(
            f"{metric.label} is recorded per shipment, so it can't be split by {dim_names} "
            "without double counting. Try another breakdown, such as transporter, region or month."
        )

    select = [f"{d.sql_for(dialect)} AS {d.key}" for d in dims]
    select.append(f"{metric.sql_for(dialect)} AS {metric.key}")

    sql = ["SELECT " + ",\n       ".join(select), "FROM orders o"]
    for alias in joins:
        j = layer.joins[alias]
        sql.append(f"JOIN {j['table']} {alias} ON {j['condition']}")

    where = []
    if metric.where:
        where.append(metric.where)
    if metric.exclude_cancelled and not plan.include_cancelled:
        where.append("o.status <> 'cancelled'")
    for f, d in zip(plan.filters, filter_dims):
        where.append(f"{d.sql_for(dialect)} = {_quote(f.value)}")
    if plan.year:
        where.append(f"{layer.dimensions['year'].sql_for(dialect)} = {int(plan.year)}")
        if plan.month:
            where.append(f"{layer.dimensions['month'].sql_for(dialect)} = '{plan.year}-{plan.month:02d}'")
        elif plan.quarter:
            where.append(f"{layer.dimensions['quarter'].sql_for(dialect)} = '{plan.year}-Q{plan.quarter}'")
    if where:
        sql.append("WHERE " + "\n  AND ".join(where))

    if dims:
        sql.append("GROUP BY " + ", ".join(str(i) for i in range(1, len(dims) + 1)))
        metric_order = f"{metric.key} {'ASC' if plan.ascending else 'DESC'}"
        time_order = [d.key for d in dims if d.time]
        # a ranking ("top 3 months") orders by the metric; a plain time series orders by time
        order = ([metric_order] + time_order) if (plan.limit and order_by_metric) else (
            time_order + ([metric_order] if order_by_metric else []))
        order += [d.key for d in dims if not d.time and not order_by_metric]
        sql.append("ORDER BY " + ", ".join(order))
    if plan.limit:
        sql.append(f"LIMIT {int(plan.limit)}")
    return "\n".join(sql)


def describe_plan(plan: QueryPlan, layer: SemanticLayer) -> str:
    """Human-readable title, e.g. 'Revenue (INR) by Region · 2025 · Plant = Pune Plant'."""
    parts = [layer.metrics[plan.metric].label]
    if plan.dimensions:
        parts[0] += " by " + " and ".join(layer.dimensions[d].label for d in plan.dimensions)
    if plan.year:
        if plan.month:
            parts.append(f"{list(MONTHS)[plan.month - 1].title()} {plan.year}")
        elif plan.quarter:
            parts.append(f"Q{plan.quarter} {plan.year}")
        else:
            parts.append(str(plan.year))
    for f in plan.filters:
        parts.append(f"{layer.dimensions[f.dimension].label} = {f.value}")
    if plan.limit:
        parts.append(f"{'bottom' if plan.ascending else 'top'} {plan.limit}")
    return " · ".join(parts)
