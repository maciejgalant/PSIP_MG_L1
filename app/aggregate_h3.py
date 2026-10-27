"""Agregacja warstw BDOT10k do komorek H3 w PostGIS.

Wymagania: psycopg2, PostGIS, grid.h3_grid, schemat bdot.
Uruchom w kontenerze python z dostepem do hosta 'postgis'.
Wynik: grid.h3_aggregation (jedna kolumna na kazda warstwe BDOT10k).
"""

import os
import re
import sys
import time

import psycopg2
from psycopg2 import sql


def connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgis"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "psip"),
        user=os.getenv("POSTGRES_USER", "psip"),
        password=os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        connect_timeout=20,
    )


def main():
    start = time.monotonic()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT h3_id), COUNT(DISTINCT resolution), COUNT(*) FILTER (WHERE ST_SRID(geom) != 2180 OR NOT ST_IsValid(geom)) FROM grid.h3_grid")
            rows, unique_ids, resolution_count, invalid_geometries = cur.fetchone()
            if rows == 0 or rows != unique_ids or resolution_count != 3 or invalid_geometries:
                raise ValueError("Nieprawidlowa siatka H3: wymagane 3 rozdzielczosci, unikalne h3_id i EPSG:2180")

            cur.execute("""
                SELECT f_table_name, f_geometry_column, type, srid
                FROM geometry_columns
                WHERE f_table_schema = 'bdot'
                ORDER BY f_table_name
            """)
            layers = cur.fetchall()
            if not layers:
                raise ValueError("Brak warstw geometrycznych w schemacie bdot")
            # Avoid PostgreSQL's hard limit of 1600 columns in one table.
            if len(layers) + 4 > 1600:
                raise ValueError("Zbyt wiele warstw BDOT dla szerokiej tabeli: zastosuj model dlugi")
            for table, geom, geometry_type, srid in layers:
                if srid != 2180:
                    raise ValueError(f"bdot.{table}: oczekiwano EPSG:2180, otrzymano {srid}")
                if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", table):
                    raise ValueError(f"Nieoczekiwana nazwa tabeli: {table}")

            cur.execute("CREATE SCHEMA IF NOT EXISTS grid")
            # Tabela odtworzona na kazde uruchomienie; nie modyfikuje BDOT ani grid.h3_grid.
            cur.execute("DROP TABLE IF EXISTS grid.h3_aggregation_new")
            cur.execute("""
                CREATE TABLE grid.h3_aggregation_new AS
                SELECT fid, h3_id, resolution, geom::geometry(Polygon, 2180) AS geom
                FROM grid.h3_grid
            """)
            cur.execute("ALTER TABLE grid.h3_aggregation_new ADD PRIMARY KEY (fid)")
            cur.execute("CREATE UNIQUE INDEX ON grid.h3_aggregation_new (h3_id)")
            cur.execute("CREATE INDEX ON grid.h3_aggregation_new USING GIST (geom)")
            cur.execute("CREATE INDEX ON grid.h3_aggregation_new (resolution)")
            conn.commit()

        print(f"H3: {rows} komorek; BDOT: {len(layers)} warstw", flush=True)
        for idx, (table, geom, geometry_type, srid) in enumerate(layers, 1):
            kind = geometry_type.upper()
            if "POLYGON" in kind:
                suffix, datatype = "m2", "double precision"
                expr = sql.SQL("COALESCE(SUM(ST_Area(ST_CollectionExtract(ST_Intersection(h.geom, b.{g}), 3))), 0)").format(g=sql.Identifier(geom))
                predicate = sql.SQL("ST_Intersects(h.geom, b.{g})").format(g=sql.Identifier(geom))
            elif "LINESTRING" in kind:
                suffix, datatype = "m", "double precision"
                expr = sql.SQL("COALESCE(SUM(ST_Length(ST_CollectionExtract(ST_Intersection(h.geom, b.{g}), 2))), 0)").format(g=sql.Identifier(geom))
                predicate = sql.SQL("ST_Intersects(h.geom, b.{g})").format(g=sql.Identifier(geom))
            elif "POINT" in kind:
                suffix, datatype = "count", "bigint"
                # Border points go to exactly one cell at each resolution:
                # the cell with the lexicographically lowest H3 identifier.
                expr = sql.SQL("COUNT(*)")
                predicate = sql.SQL("""ST_Covers(h.geom, ST_Force2D(b.{g}))
                    AND h.h3_id = (
                        SELECT MIN(h2.h3_id) FROM grid.h3_grid h2
                        WHERE h2.resolution = h.resolution
                          AND h2.geom && ST_Force2D(b.{g})
                          AND ST_Covers(h2.geom, ST_Force2D(b.{g}))
                    )""").format(g=sql.Identifier(geom))
            else:
                raise ValueError(f"Nieobslugiwany typ geometrii: bdot.{table} ({kind})")

            column = f"{table}_{suffix}"
            layer_start = time.monotonic()
            print(f"[{idx}/{len(layers)}] START bdot.{table}", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("ALTER TABLE grid.h3_aggregation_new ADD COLUMN {c} {t} NOT NULL DEFAULT 0").format(
                    c=sql.Identifier(column), t=sql.SQL(datatype)))
                cur.execute(sql.SQL("""
                    WITH aggregated AS MATERIALIZED (
                        SELECT h.h3_id, {value} AS metric
                        FROM bdot.{table} AS b
                        JOIN grid.h3_aggregation_new AS h ON {predicate}
                        WHERE b.{geom} IS NOT NULL
                        GROUP BY h.h3_id
                    )
                    UPDATE grid.h3_aggregation_new AS target
                    SET {column} = aggregated.metric
                    FROM aggregated
                    WHERE target.h3_id = aggregated.h3_id
                """).format(column=sql.Identifier(column), value=expr, table=sql.Identifier(table),
                           geom=sql.Identifier(geom), predicate=predicate))
                conn.commit()
            print(f"[{idx}/{len(layers)}] DONE bdot.{table} -> {column}, {time.monotonic()-layer_start:.1f}s", flush=True)

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS grid.h3_aggregation_old")
            cur.execute("""
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema='grid' AND table_name='h3_aggregation'
                )
            """)
            if cur.fetchone()[0]:
                cur.execute("ALTER TABLE grid.h3_aggregation RENAME TO h3_aggregation_old")
            cur.execute("ALTER TABLE grid.h3_aggregation_new RENAME TO h3_aggregation")
            cur.execute("DROP TABLE IF EXISTS grid.h3_aggregation_old")
            cur.execute("ANALYZE grid.h3_aggregation")
            conn.commit()
        print(f"GOTOWE: grid.h3_aggregation, {rows} heksagonow, {len(layers)} kolumn BDOT; czas {time.monotonic()-start:.1f} s", flush=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BLAD: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
