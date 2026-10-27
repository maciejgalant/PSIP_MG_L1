"""Import county and all H3 resolutions into PostGIS (EPSG:2180)."""
import os
import subprocess
from pathlib import Path

import psycopg2

HOST = os.getenv("POSTGRES_HOST", "postgis")
PORT = os.getenv("POSTGRES_PORT", "5432")
DB = os.getenv("POSTGRES_DB", "psip")
USER = os.getenv("POSTGRES_USER", "psip")
PASSWORD = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
COUNTY = Path(os.getenv("H3_INPUT_FILE", "/app/data/powiat.shp"))
H3_FILE = Path(os.getenv("H3_OUTPUT_FILE", "/app/hexagons.gpkg"))


def import_layer(path, source_layer, target):
    command = [
        "ogr2ogr", "-f", "PostgreSQL",
        f"PG:host={HOST} port={PORT} dbname={DB} user={USER}",
        str(path),
    ]
    if source_layer:
        command.append(source_layer)
    command.extend([
        "-nln", target, "-overwrite", "-t_srs", "EPSG:2180",
        "-lco", "GEOMETRY_NAME=geom", "-lco", "SPATIAL_INDEX=GIST",
    ])
    subprocess.run(command, check=True, env=os.environ.copy())


def main():
    if not PASSWORD:
        raise RuntimeError("Missing PGPASSWORD or POSTGRES_PASSWORD")
    if not COUNTY.is_file() or not H3_FILE.is_file():
        raise FileNotFoundError(f"Expected {COUNTY} and {H3_FILE}")
    with psycopg2.connect(host=HOST, port=PORT, dbname=DB, user=USER, password=PASSWORD) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS grid")
    import_layer(COUNTY, None, "public.powiat")
    import_layer(H3_FILE, "h3_grid", "grid.h3_grid")
    with psycopg2.connect(host=HOST, port=PORT, dbname=DB, user=USER, password=PASSWORD) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(DISTINCT h3_id),
                       COUNT(DISTINCT resolution),
                       COUNT(*) FILTER (WHERE ST_SRID(geom) <> 2180 OR NOT ST_IsValid(geom))
                FROM grid.h3_grid
            """)
            total, unique_ids, resolutions, invalid = cur.fetchone()
            if total == 0 or unique_ids != total or resolutions != 3 or invalid:
                raise ValueError(f"Invalid H3 grid: count={total} unique={unique_ids} resolutions={resolutions} invalid={invalid}")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS h3_grid_h3_id_idx ON grid.h3_grid (h3_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS h3_grid_resolution_idx ON grid.h3_grid (resolution)")
            cur.execute("ANALYZE grid.h3_grid")
            cur.execute("SELECT resolution, COUNT(*) FROM grid.h3_grid GROUP BY resolution ORDER BY resolution")
            for resolution, count in cur.fetchall():
                print(f"H3 res={resolution}: {count} cells", flush=True)
    print(f"Imported {total} cells in EPSG:2180", flush=True)


if __name__ == "__main__":
    main()
