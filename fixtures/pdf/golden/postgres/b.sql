SELECT
  "region",
  "total_revenue_2025",
  "total_revenue_2026",
  "total_revenue_2026" - "total_revenue_2025" AS "total_revenue_delta",
  100.0 * ("total_revenue_2026" - "total_revenue_2025") / NULLIF("total_revenue_2025", 0) AS "total_revenue_pct_change",
  "is_total"
FROM (
  SELECT
    "dim_store"."region" AS "region",
    SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2025) AS "total_revenue_2025",
    SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2026) AS "total_revenue_2026",
    GROUPING("dim_store"."region") = 1 AS "is_total"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" IN (2025, 2026)
  GROUP BY GROUPING SETS (("dim_store"."region"), ())
) AS "agg"
ORDER BY "is_total" ASC, "region" ASC NULLS LAST
