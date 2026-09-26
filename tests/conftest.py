import os
import uuid

import duckdb
import pytest

from tests.helpers import FIXTURES, PDF

SYNTHETIC = FIXTURES / "synthetic"


@pytest.fixture
def pdf_duckdb():
    """In-memory DuckDB loaded with the PDF's schema and data."""
    con = duckdb.connect(":memory:")
    con.execute((PDF / "duckdb_schema.sql").read_text())
    yield con
    con.close()


@pytest.fixture
def orphan_duckdb(pdf_duckdb):
    """PDF data plus rows whose store / date have no dimension match."""
    pdf_duckdb.execute((FIXTURES / "orphan_rows.sql").read_text())
    return pdf_duckdb


def run(con, sql):
    """Execute on DuckDB or Postgres (psycopg) and return (column names, rows)."""
    cur = con.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


# --- Postgres (tests marked `postgres`, run by `make test-pg`) ---------------------------------
# The server comes from docker-compose.yml (host port 55432). Override with WALT_PG_DSN.

PG_DSN = os.environ.get("WALT_PG_DSN",
                        "host=127.0.0.1 port=55432 user=walt password=walt dbname=walt connect_timeout=3")


@pytest.fixture(scope="session")
def pg_admin():
    """One autocommit connection used only to create and drop per-test schemas.

    Skips (never errors) when the server is unreachable, so `pytest -m postgres` without Docker
    reports a clear reason instead of a wall of connection tracebacks.
    """
    psycopg = pytest.importorskip("psycopg")
    try:
        con = psycopg.connect(PG_DSN, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres is not reachable at {PG_DSN!r} (start it with `make test-pg`): "
                    f"{str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__}")
    yield con
    con.close()


@pytest.fixture
def pg_schema(pg_admin):
    """A fresh, empty schema per test, dropped afterwards; yields a connection whose search_path is it.

    The connection is autocommit, so a statement that fails (e.g. division by zero) does not leave
    an aborted transaction behind for the next statement in the same test.
    """
    import psycopg

    schema = f"walt_test_{uuid.uuid4().hex[:16]}"
    pg_admin.execute(f'CREATE SCHEMA "{schema}"')
    con = psycopg.connect(PG_DSN, autocommit=True, options=f"-c search_path={schema}")
    try:
        yield con
    finally:
        con.close()
        pg_admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def pdf_pg(pg_schema):
    """Postgres schema loaded with the PDF's Postgres DDL and data."""
    pg_schema.execute((PDF / "postgres_schema.sql").read_text())
    return pg_schema


@pytest.fixture
def orphan_pg(pdf_pg):
    """PDF data on Postgres plus the orphan rows (store / date with no dimension match)."""
    pdf_pg.execute((FIXTURES / "orphan_rows.sql").read_text())
    return pdf_pg


@pytest.fixture
def synth_pg(pg_schema):
    """Postgres schema loaded with the synthetic seed (the same file DuckDB loads)."""
    pg_schema.execute((SYNTHETIC / "seed.sql").read_text())
    return pg_schema
