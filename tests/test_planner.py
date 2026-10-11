import pytest

from t2sql.planner import PlanError, QueryPlan, compile_plan, describe_plan, parse_question


@pytest.fixture
def layer(service):
    return service.layer


def test_metric_dimension_and_year(layer):
    plan = parse_question("Revenue by region in 2025", layer)
    assert (plan.metric, plan.dimensions, plan.year) == ("revenue", ["region"], 2025)


def test_top_n_and_superlatives(layer):
    assert parse_question("Top 5 customers by revenue", layer).limit == 5
    plan = parse_question("Which transporter has the worst on-time delivery rate?", layer)
    assert plan.metric == "on_time_rate" and plan.limit == 1 and plan.ascending


def test_value_filters_are_detected(layer):
    plan = parse_question("How many orders did the Pune Plant get in March 2026?", layer)
    assert plan.metric == "orders"
    assert [(f.dimension, f.value) for f in plan.filters] == [("plant", "Pune Plant")]
    assert plan.dimensions == []                        # "plant" names the filter, not a group-by
    assert (plan.year, plan.month) == (2026, 3)


def test_quarter_filter(layer):
    plan = parse_question("Which category sold the most units in Q1 2026?", layer)
    assert (plan.metric, plan.dimensions, plan.quarter, plan.year, plan.limit) == ("quantity", ["category"], 1, 2026, 1)


def test_longest_phrase_wins(layer):
    # "average order value" must not be read as the "orders" metric
    assert parse_question("average order value by segment", layer).metric == "avg_order_value"
    # "Delta Freight" is a transporter, not the freight metric
    plan = parse_question("orders shipped by Delta Freight", layer)
    assert plan.metric == "orders" and plan.filters[0].value == "Delta Freight"


def test_followups_modify_previous_plan(layer):
    base = parse_question("Revenue by region in 2025", layer)
    split = parse_question("now split that by category", layer, base)
    assert split.dimensions == ["region", "category"] and split.year == 2025
    west = parse_question("only the West region", layer, split)
    assert west.dimensions == ["category"] and west.filters[0].value == "West"
    metric = parse_question("what about units?", layer, west)
    assert metric.metric == "quantity" and metric.filters == west.filters
    top = parse_question("top 3 instead", layer, metric)
    assert top.limit == 3


def test_unrelated_question_is_not_a_followup(layer):
    base = parse_question("Revenue by region", layer)
    with pytest.raises(PlanError):
        parse_question("what is the weather today", layer, base)


def test_unknown_question_raises(layer):
    with pytest.raises(PlanError, match="which measure"):
        parse_question("tell me a joke", layer)


def test_fan_out_is_refused(layer):
    plan = QueryPlan(metric="freight_cost", dimensions=["product"])
    with pytest.raises(PlanError, match="double counting"):
        compile_plan(plan, layer, "sqlite")


def test_distinct_metrics_can_use_item_joins(layer):
    sql = compile_plan(QueryPlan(metric="orders", dimensions=["category"]), layer, "sqlite")
    assert "COUNT(DISTINCT o.order_id)" in sql and "JOIN products pr" in sql


def test_compile_uses_dialect_specific_dates(layer):
    plan = QueryPlan(metric="revenue", dimensions=["month"], year=2025)
    assert "strftime('%Y-%m'" in compile_plan(plan, layer, "sqlite")
    assert "TO_CHAR(o.order_date, 'YYYY-MM')" in compile_plan(plan, layer, "postgres")


def test_joins_follow_dependencies(layer):
    sql = compile_plan(QueryPlan(metric="revenue", dimensions=["region"]), layer, "sqlite")
    assert sql.index("JOIN customers c") < sql.index("JOIN regions r")
    assert "o.status <> 'cancelled'" in sql


def test_plan_round_trip_and_description(layer):
    plan = parse_question("Top 3 customers by revenue in the North region in 2025", layer)
    assert QueryPlan.from_dict(plan.to_dict()) == plan
    assert describe_plan(plan, layer) == "Revenue (INR) by Customer · 2025 · Region = North · top 3"
