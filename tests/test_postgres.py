"""Integration tests against a real PostgreSQL. Run with:

    T2SQL_TEST_PG_URL=postgresql+psycopg://postgres:postgres@localhost:5432/analytics pytest tests/test_postgres.py
"""

import pytest

from t2sql.config import Settings
from t2sql.db import QueryError, create_readonly_engine, run_query
from t2sql.evaluation import run_evaluation
from t2sql.service import TextToSQLService

from .conftest import EVAL_CASES


@pytest.fixture(scope="module")
def pg_service(pg_url):
    return TextToSQLService(Settings(_env_file=None, database_url=pg_url))


def test_postgres_session_is_read_only(pg_url):
    engine = create_readonly_engine(pg_url)
    with pytest.raises(QueryError, match="read-only"):
        run_query(engine, "DELETE FROM orders", 10)


def test_postgres_statement_timeout(pg_url):
    engine = create_readonly_engine(pg_url, statement_timeout_ms=300)
    with pytest.raises(QueryError, match="timeout"):
        run_query(engine, "SELECT pg_sleep(2)", 10)


def test_postgres_execution_accuracy(pg_service, tmp_path):
    assert pg_service.dialect == "postgres"
    out = run_evaluation(pg_service, EVAL_CASES, tmp_path)
    assert out["summary"]["execution_accuracy"] >= 0.95, [r.id for r in out["results"] if not r.correct]
