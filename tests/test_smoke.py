"""S0: the package imports and the fixtures are sound."""
import walt_compiler
from tests.conftest import run
from tests.helpers import assert_result, pdf_expected


def test_package_exposes_version():
    assert walt_compiler.__version__ == "0.1.0"


def test_pdf_fixture_loads(pdf_duckdb):
    assert pdf_duckdb.execute("SELECT count(*) FROM fact_sales").fetchone() == (9,)
    assert pdf_duckdb.execute("SELECT count(*) FROM dim_store").fetchone() == (4,)
    assert pdf_duckdb.execute("SELECT count(*) FROM dim_calendar").fetchone() == (8,)


def test_orphan_rows_have_no_dimension_match(orphan_duckdb):
    orphans = orphan_duckdb.execute("""
        SELECT f.order_id, s.region, c.fiscal_year
        FROM fact_sales f
        LEFT JOIN dim_store s ON f.store_id = s.store_id
        LEFT JOIN dim_calendar c ON f.order_date = c.date
        WHERE s.store_id IS NULL OR c.date IS NULL
        ORDER BY f.order_id
    """).fetchall()
    assert orphans == [("O-900", None, 2026), ("O-901", "North", None)]


# --- The expected-result fixtures are checked against hand-written reference SQL, so that
# --- later compiler tests compare against numbers we have independently verified.

def test_expected_a_matches_reference_sql(pdf_duckdb):
    cols, rows = run(pdf_duckdb, """
        SELECT SUM(f.revenue) FILTER (WHERE f.channel = 'Online') AS online_rev,
               SUM(f.revenue) AS total_revenue
        FROM fact_sales f LEFT JOIN dim_calendar c ON f.order_date = c.date
        WHERE c.fiscal_year = 2026
    """)
    assert_result(cols, rows, pdf_expected("a"))


def test_expected_b_matches_reference_sql(pdf_duckdb):
    cols, rows = run(pdf_duckdb, """
        SELECT region, total_revenue_2025, total_revenue_2026,
               total_revenue_2026 - total_revenue_2025 AS total_revenue_delta,
               100.0 * (total_revenue_2026 - total_revenue_2025) / NULLIF(total_revenue_2025, 0) AS total_revenue_pct_change,
               is_total
        FROM (
          SELECT s.region,
                 SUM(f.revenue) FILTER (WHERE c.fiscal_year = 2025) AS total_revenue_2025,
                 SUM(f.revenue) FILTER (WHERE c.fiscal_year = 2026) AS total_revenue_2026,
                 GROUPING(s.region) = 1 AS is_total
          FROM fact_sales f
          LEFT JOIN dim_store s ON f.store_id = s.store_id
          LEFT JOIN dim_calendar c ON f.order_date = c.date
          WHERE c.fiscal_year IN (2025, 2026)
          GROUP BY GROUPING SETS ((s.region), ())
        ) agg
        ORDER BY is_total ASC, region ASC NULLS LAST
    """)
    assert_result(cols, rows, pdf_expected("b"))


def test_expected_c_matches_reference_sql(pdf_duckdb):
    cols, rows = run(pdf_duckdb, """
        SELECT region, order_count, is_total FROM (
          SELECT s.region, COUNT(DISTINCT f.order_id) AS order_count,
                 GROUPING(s.region) = 1 AS is_total
          FROM fact_sales f LEFT JOIN dim_store s ON f.store_id = s.store_id
          GROUP BY GROUPING SETS ((s.region), ())
        ) agg
        ORDER BY is_total ASC, region ASC NULLS LAST
    """)
    assert_result(cols, rows, pdf_expected("c"))
