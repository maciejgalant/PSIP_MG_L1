-- LAB 3: separate QGIS layers for the three H3 resolutions.
-- Read-only views preserve one source of truth in grid.h3_aggregation.
CREATE OR REPLACE VIEW grid.h3_res7 AS
SELECT * FROM grid.h3_aggregation WHERE resolution = 7;

CREATE OR REPLACE VIEW grid.h3_res8 AS
SELECT * FROM grid.h3_aggregation WHERE resolution = 8;

CREATE OR REPLACE VIEW grid.h3_res9 AS
SELECT * FROM grid.h3_aggregation WHERE resolution = 9;
