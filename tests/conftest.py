import duckdb
import pytest

from tests.helpers import FIXTURES, PDF


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
    cur = con.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()
