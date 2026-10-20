
import os
import subprocess
from pathlib import Path
import psycopg2

# Konfiguracja polaczenia z PostgreSQL
DB_HOST = os.getenv("POSTGRES_HOST", "postgis")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "psip")
DB_USER = os.getenv("POSTGRES_USER", "psip")
DB_PASSWORD = os.getenv("PGPASSWORD")

if not DB_PASSWORD:
    raise RuntimeError("Brak zmiennej PGPASSWORD")

connection = (
    f"PG:host={DB_HOST} "
    f"port={DB_PORT} "
    f"dbname={DB_NAME} "
    f"user={DB_USER}"
)

environment = os.environ.copy()


def run_ogr(command):
    subprocess.run(
        command,
        env=environment,
        check=True
    )


# 1. Import granicy powiatu
powiat = Path("/app/data/powiat.shp")

if not powiat.exists():
    raise FileNotFoundError(powiat)

run_ogr([
    "ogr2ogr",
    "-f", "PostgreSQL",
    connection,
    str(powiat),
    "-nln", "public.powiat",
    "-lco", "GEOMETRY_NAME=geom",
    "-overwrite",
])

print("Załadowano granice powiatu.")


# Automatyczne tworzenie schematu grid
import psycopg2

with psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD
) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            "CREATE SCHEMA IF NOT EXISTS grid;"
        )

print("Schemat grid jest gotowy.")

