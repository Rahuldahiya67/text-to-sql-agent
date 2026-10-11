import pytest

from t2sql.db import QueryError, create_readonly_engine, dialect_of, render_schema, run_query
from t2sql.semantic import select_tables


def test_sqlite_engine_is_read_only(sqlite_url):
    engine = create_readonly_engine(sqlite_url)
    with pytest.raises(QueryError, match="readonly"):
        run_query(engine, "DELETE FROM orders WHERE order_id = 1", 10)
    assert run_query(engine, "SELECT COUNT(*) FROM orders", 10).rows[0][0] == 4000


def test_statement_timeout(sqlite_url):
    engine = create_readonly_engine(sqlite_url, statement_timeout_ms=200)
    with pytest.raises(QueryError, match="time limit"):
        run_query(engine, "SELECT COUNT(*) FROM order_items a, order_items b, order_items c", 10)


def test_rows_are_capped(sqlite_url):
    res = run_query(create_readonly_engine(sqlite_url), "SELECT * FROM orders", 25)
    assert len(res.rows) == 25 and res.truncated


def test_missing_sqlite_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="seed"):
        create_readonly_engine(f"sqlite:///{tmp_path}/nope.db")


def test_introspection_finds_keys_and_sample_values(service):
    tables = service.tables
    assert set(tables) == {"regions", "plants", "customers", "products", "transporters", "orders", "order_items", "shipments"}
    oi = {c.name: c for c in tables["order_items"].columns}
    assert oi["order_id"].foreign_key == "orders.order_id"
    assert tables["regions"].sample_values["name"] == ["East", "North", "South", "West"]
    assert "status" in tables["orders"].sample_values


def test_render_schema_includes_descriptions_and_values(service):
    text = render_schema(service.tables, ["orders", "regions"], service.layer.table_descriptions())
    assert "TABLE orders" in text and "Base table for sales" in text
    assert "values: East, North, South, West" in text
    assert "TABLE customers" not in text


def test_table_selection_adds_join_path(service):
    picked = select_tables("revenue by region for pumps", service.tables, service.layer)
    assert {"order_items", "regions", "products"} <= set(picked)
    # regions only connect to order_items through customers and orders
    assert {"customers", "orders"} <= set(picked)


def test_dialect_detection():
    assert dialect_of("sqlite:///x.db") == "sqlite"
    assert dialect_of("postgresql+psycopg://u@h/db") == "postgres"
