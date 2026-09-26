-- Extra trips for the division-by-zero tests (DESIGN.md §6.3: "left to the dialect"). Loaded ON TOP of
-- fixtures/synthetic/seed.sql; never used by any other test. Portable: runs on DuckDB and Postgres.
-- Three days after the seed's last day, one trip each, so fare_total per day is exactly:
--   2026-01-08: 0.0     2026-01-09: 5.0     2026-01-10: 0.0
-- compare 08 -> 09 (primary 09): pct = 100.0 * (5.0 - 0.0) / 0.0   (non-zero / zero)
-- compare 08 -> 10 (primary 10): pct = 100.0 * (0.0 - 0.0) / 0.0   (zero / zero)
INSERT INTO trips VALUES
  ('T09','C1','D1', 104, '2026-01-08 09:00:00', '2026-01-08', 0.0, 1.0, TRUE),
  ('T10','C1','D1', 104, '2026-01-09 09:00:00', '2026-01-09', 5.0, 1.0, TRUE),
  ('T11','C1','D1', 104, '2026-01-10 09:00:00', '2026-01-10', 0.0, 1.0, TRUE);
