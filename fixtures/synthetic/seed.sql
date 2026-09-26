-- Synthetic bike-share warehouse: a second model that shares no name with the PDF's (DESIGN.md §2.5, §10).
-- Portable DDL: runs unchanged on DuckDB and Postgres (DOUBLE PRECISION, DECIMAL, TIMESTAMP, BOOLEAN).
--
-- Snowflake:  trips -> cycles -> cycle_models -> makers -(one_to_one)-> maker_hq
--             trips -> docks  -(one_to_many, never followable)-> dock_audits
--
-- Deliberate gaps, so NULL / orphan behaviour is visible:
--   T04: fare is NULL                     (ignored by sum/avg/count(fare))
--   T06: rider_ref is NULL                (ignored by count_distinct);  cycle C4 -> model CM-X does not exist
--        (hop-2 orphan: electric/frame_kg known, launch_year/list_price/maker_* NULL)
--   T07: cycle C9 does not exist          (hop-1 orphan: every cycle/model/maker dimension NULL)
--   T08: dock D9 does not exist           (district NULL)
-- Ties: CM-B and CM-C share list_price 1499.50; C2 and C3 share frame_kg 22.0; T02 and T05 share km 6.0.

CREATE TABLE makers ( maker_key TEXT PRIMARY KEY, maker_label TEXT, country TEXT );
CREATE TABLE maker_hq ( maker_key TEXT PRIMARY KEY, hq_city TEXT );
CREATE TABLE cycle_models ( model_ref TEXT PRIMARY KEY, maker_key TEXT, list_price DECIMAL(8,2), launch_year INTEGER );
CREATE TABLE cycles ( tag TEXT PRIMARY KEY, model_ref TEXT, is_electric BOOLEAN, frame_kg DOUBLE PRECISION );
CREATE TABLE docks ( dock_code TEXT PRIMARY KEY, district TEXT );
CREATE TABLE dock_audits ( audit_id TEXT PRIMARY KEY, dock_code TEXT, auditor TEXT );
CREATE TABLE trips ( -- grain: one ride
  trip_id TEXT, cycle_tag TEXT, dock_code TEXT, rider_ref INTEGER,
  started_at TIMESTAMP, trip_day DATE,
  fare DOUBLE PRECISION, km DOUBLE PRECISION, is_member BOOLEAN
);

INSERT INTO makers VALUES ('MK1','Velo','NL'), ('MK2','Rad','DE');
INSERT INTO maker_hq VALUES ('MK1','Utrecht'), ('MK2','Berlin');

INSERT INTO cycle_models VALUES
  ('CM-A','MK1', 899.00, 2023),
  ('CM-B','MK1',1499.50, 2025),
  ('CM-C','MK2',1499.50, 2024);

INSERT INTO cycles VALUES
  ('C1','CM-A', FALSE, 14.5),
  ('C2','CM-B', TRUE,  22.0),
  ('C3','CM-C', TRUE,  22.0),
  ('C4','CM-X', FALSE, 12.0);   -- CM-X is not a cycle model

INSERT INTO docks VALUES ('D1','Harbor'), ('D2','Smith''s Quay');

INSERT INTO dock_audits VALUES ('A1','D1','kim'), ('A2','D1','lee');

INSERT INTO trips VALUES
  ('T01','C1','D1', 101, '2026-01-05 08:00:00', '2026-01-05', 3.0,  2.5, TRUE),
  ('T02','C2','D1', 102, '2026-01-05 09:30:00', '2026-01-05', 5.0,  6.0, FALSE),
  ('T03','C3','D2', 101, '2026-01-05 18:15:00', '2026-01-05', 4.0,  4.0, TRUE),
  ('T04','C1','D2', 103, '2026-01-06 07:45:00', '2026-01-06', NULL, 1.5, TRUE),
  ('T05','C2','D2', 102, '2026-01-06 12:00:00', '2026-01-06', 6.0,  6.0, FALSE),
  ('T06','C4','D1', NULL,'2026-01-06 20:00:00', '2026-01-06', 2.0,  1.0, FALSE),
  ('T07','C9','D1', 101, '2026-01-07 10:00:00', '2026-01-07', 7.0,  8.0, TRUE),
  ('T08','C3','D9', 103, '2026-01-07 11:11:11', '2026-01-07', 3.0,  3.0, FALSE);
