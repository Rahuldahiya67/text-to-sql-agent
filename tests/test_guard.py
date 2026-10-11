import pytest

from t2sql.guard import UnsafeSQLError, check_sql

TABLES = {"orders", "customers", "regions", "order_items"}


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select count(*) from orders o join customers c on c.customer_id = o.customer_id",
        "WITH x AS (SELECT * FROM orders) SELECT * FROM x",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT * FROM orders;",
    ],
)
def test_safe_queries_pass(sql, dialect):
    checked = check_sql(sql, TABLES, dialect, max_rows=50)
    assert "LIMIT" in checked.sql.upper()


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("DELETE FROM orders", "only SELECT"),
        ("UPDATE orders SET status = 'x'", "only SELECT"),
        ("DROP TABLE orders", "only SELECT"),
        ("INSERT INTO orders VALUES (1)", "only SELECT"),
        ("SELECT 1; DROP TABLE orders", "single statement"),
        ("SELECT * INTO backup FROM orders", "INTO"),
        ("SELECT * FROM salaries", "does not exist"),
        ("SELECT * FROM pg_catalog.pg_tables", "schema-qualified"),
        ("SELECT pg_sleep(30)", "pg_sleep"),
        ("SELECT * FROM orders WHERE", "parse"),
        ("", "empty"),
    ],
)
def test_unsafe_queries_are_rejected(sql, reason):
    with pytest.raises(UnsafeSQLError, match=reason):
        check_sql(sql, TABLES, "postgres")


def test_limit_is_added_and_capped():
    assert check_sql("SELECT * FROM orders", TABLES, "sqlite", 100).sql.endswith("LIMIT 100")
    capped = check_sql("SELECT * FROM orders LIMIT 100000", TABLES, "sqlite", 100)
    assert capped.sql.endswith("LIMIT 100") and capped.limit_applied
    kept = check_sql("SELECT * FROM orders LIMIT 5", TABLES, "sqlite", 100)
    assert kept.sql.endswith("LIMIT 5") and not kept.limit_applied


def test_union_is_wrapped_so_limit_covers_everything():
    out = check_sql("SELECT order_id FROM orders UNION SELECT customer_id FROM customers", TABLES, "postgres", 10)
    assert out.sql.strip().endswith("LIMIT 10")
    assert "AS q" in out.sql


def test_cte_names_are_not_treated_as_tables():
    out = check_sql("WITH monthly AS (SELECT * FROM orders) SELECT * FROM monthly", TABLES, "sqlite")
    assert out.tables == ["orders"]
