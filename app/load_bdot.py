"""Importuje wszystkie warstwy BDOT10k z GeoPackage do schematu bdot w PostGIS.

Uruchomienie w kontenerze python środowiska env-test:
    python /app/load_bdot.py

Wymagania: ogr2ogr (GDAL), psycopg2, zmienne POSTGRES_HOST,
POSTGRES_PORT, POSTGRES_USER, POSTGRES_DB i PGPASSWORD.
"""

import os
import re
import sqlite3
import subprocess
from pathlib import Path

import psycopg2
from psycopg2 import sql

SOURCE = Path(os.getenv("BDOT_FILE", "/app/bdot10k_kutnowski.gpkg"))
DB_HOST = os.getenv("POSTGRES_HOST", "postgis")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "psip")
DB_USER = os.getenv("POSTGRES_USER", "psip")
DB_PASSWORD = os.getenv("PGPASSWORD")


def layers_from_gpkg(path: Path):
    with sqlite3.connect(path) as db:
        return db.execute("""
            SELECT gc.table_name, gc.geometry_type_name, gc.srs_id
            FROM gpkg_geometry_columns AS gc
            JOIN gpkg_contents AS c ON c.table_name = gc.table_name
            WHERE c.data_type = 'features'
            ORDER BY gc.table_name
        """).fetchall()


def target_table(layer_name: str, geometry_type: str) -> str:
    # Nazwy warstw QuickBDOT mają postać: "ADMS — Miejscowość [P]".
    code = layer_name.split(" — ", 1)[0].strip().lower()
    code = re.sub(r"[^a-z0-9_]", "_", code)
    kind = geometry_type.upper()
    if "POINT" in kind:
        suffix = "p"
    elif "LINESTRING" in kind or "CURVE" in kind:
        suffix = "l"
    elif "POLYGON" in kind or "SURFACE" in kind:
        suffix = "a"
    else:
        raise ValueError(f"Nieobsługiwany typ geometrii {geometry_type}: {layer_name}")
    return f"{code}_{suffix}"


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Brak pliku BDOT10k: {SOURCE}")
    if not DB_PASSWORD:
        raise RuntimeError("W kontenerze nie ustawiono PGPASSWORD")

    layers = layers_from_gpkg(SOURCE)
    if not layers:
        raise RuntimeError("Nie znaleziono warstw wektorowych w GeoPackage")
    names = [target_table(name, kind) for name, kind, _ in layers]
    if len(set(names)) != len(names):
        raise RuntimeError("Konflikt nazw tabel docelowych; import zatrzymany")
    invalid_srs = [(name, srid) for name, _, srid in layers if srid != 2180]
    if invalid_srs:
        raise RuntimeError(f"Warstwy spoza EPSG:2180: {invalid_srs}")

    conn_args = {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }
    with psycopg2.connect(**conn_args) as conn:
        with conn.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS bdot")

    pg_dsn = f"PG:host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER}"
    total = 0
    for index, (layer_name, kind, _srid) in enumerate(layers, start=1):
        target = target_table(layer_name, kind)
        print(f"[{index}/{len(layers)}] {layer_name} -> bdot.{target}", flush=True)
        cmd = [
            "ogr2ogr", "-f", "PostgreSQL", pg_dsn, str(SOURCE), layer_name,
            "-nln", f"bdot.{target}", "-overwrite", "-lco", "GEOMETRY_NAME=geom",
            "-lco", "SPATIAL_INDEX=GIST", "-gt", "65536",
        ]
        subprocess.run(cmd, check=True)
        with psycopg2.connect(**conn_args) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier("bdot"), sql.Identifier(target)
                    )
                )
                count = cursor.fetchone()[0]
        total += count
        print(f"    Zaimportowano: {count} obiektów", flush=True)

    print(f"GOTOWE: {len(layers)} warstw, łącznie {total} obiektów", flush=True)


if __name__ == "__main__":
    main()
