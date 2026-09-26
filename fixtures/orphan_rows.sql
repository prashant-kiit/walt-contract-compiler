-- Extra rows for join-semantics tests. Loaded ON TOP of the PDF data; never used by the A/B/C fixtures.
-- O-900: store S9 does not exist in dim_store       -> region is NULL after the LEFT JOIN.
-- O-901: 2026-07-01 does not exist in dim_calendar  -> fiscal_year is NULL after the LEFT JOIN.
INSERT INTO fact_sales VALUES
  ('O-900','2026-02-01','2026-02-03','S9','Online', 40),
  ('O-901','2026-07-01','2026-07-02','S1','Retail', 25);
