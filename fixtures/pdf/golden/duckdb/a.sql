SELECT
  "online_rev",
  "total_revenue"
FROM (
  SELECT
    SUM("fact_sales"."revenue") FILTER (WHERE "fact_sales"."channel" = 'Online') AS "online_rev",
    SUM("fact_sales"."revenue") AS "total_revenue"
  FROM "fact_sales"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" = 2026
) AS "agg"
