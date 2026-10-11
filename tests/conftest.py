import os
from pathlib import Path

import pytest

from t2sql.config import Settings
from t2sql.seed import build_database
from t2sql.service import TextToSQLService

ROOT = Path(__file__).resolve().parents[1]
EVAL_CASES = ROOT / "eval" / "cases.jsonl"


@pytest.fixture(scope="session")
def sqlite_url(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("db") / "analytics.db"
    url = f"sqlite:///{path}"
    build_database(url)
    return url


@pytest.fixture
def settings(sqlite_url) -> Settings:
    return Settings(_env_file=None, database_url=sqlite_url)


@pytest.fixture(scope="session")
def service(sqlite_url) -> TextToSQLService:
    return TextToSQLService(Settings(_env_file=None, database_url=sqlite_url))


@pytest.fixture(scope="session")
def pg_url():
    url = os.getenv("T2SQL_TEST_PG_URL")
    if not url:
        pytest.skip("set T2SQL_TEST_PG_URL to run PostgreSQL integration tests")
    build_database(url)
    return url
