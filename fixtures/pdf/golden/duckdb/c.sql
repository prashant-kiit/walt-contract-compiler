SELECT
  "region",
  "order_count",
  "is_total"
FROM (
  SELECT
    "dim_store"."region" AS "region",
    COUNT(DISTINCT "fact_sales"."order_id") AS "order_count",
    GROUPING("dim_store"."region") = 1 AS "is_total"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
  GROUP BY GROUPING SETS (("dim_store"."region"), ())
) AS "agg"
ORDER BY "is_total" ASC, "region" ASC NULLS LAST
