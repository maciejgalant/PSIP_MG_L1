"""LAB 4: Indeks przejezdnosci terenu (IOP) na siatce H3 - metoda VRF.

Metoda wg K. Pokonieczny (2018), "Comparison of land passability maps created
with use of different spatial data bases", Geografie 123/3, wzory (1) i (2), rys. 4:
  1. Normalizacja kryteriow (wzor 1, min-max) do przedzialu 0..1:
       k' = (V - Vmin) / (Vmax - Vmin)
     liczona osobno dla kazdej rozdzielczosci H3 (jak osobno dla kazdego rozmiaru
     pola podstawowego w artykule).
  2. Wspolczynniki IVRF (wagi) w przedziale -1..1 z iop_weights.csv:
     dodatnie ulatwiaja przejazd, ujemne utrudniaja, 0 = neutralne.
     Zapis do bazy jako grid.iop_weights.
  3. IOP = sum(k'_i * IVRF_i) (wzor 2)                    -> kolumna iop_suma
     dodatkowo wariant z zajec: IOP / n (n = liczba kryteriow) -> iop_srednia (-1..1)
  4. Ponowna normalizacja min-max IOP do 0..1              -> kolumna iop_norm
     (0 = najgorsza przejezdnosc w powiecie, 1 = najlepsza).
  5. Klasy: 5 rownych przedzialow 0..1 (czerwony -> bialy -> zielony).

Wynik: grid.h3_iop (+ widoki grid.h3_iop_res7/8/9 i styl QGIS w public.layer_styles).
Uruchom w kontenerze python: python /app/compute_iop.py
"""

import csv
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import AsIs, register_adapter
from psycopg2.extras import execute_values

# numpy -> SQL (np.float64 dziedziczy po float, liczby calkowite trzeba zarejestrowac).
register_adapter(np.int64, AsIs)
register_adapter(np.int32, AsIs)

WEIGHTS_FILE = Path(os.getenv("IOP_WEIGHTS_FILE", Path(__file__).with_name("iop_weights.csv")))
STYLE_FILE = Path(os.getenv("IOP_STYLE_FILE", Path(__file__).with_name("iop_style.qml")))
SOURCE = "h3_aggregation"
TARGET = "h3_iop"
KEY_COLUMNS = {"fid", "h3_id", "resolution", "geom"}
IDENT = re.compile(r"[a-z][a-z0-9_]*")

# 5 rownych przedzialow w zakresie 0..1 (granice wspolne z iop_style.qml).
CLASS_EDGES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
CLASS_LABELS = [
    "bardzo trudna",
    "trudna",
    "przeciętna",
    "dobra",
    "bardzo dobra",
]


def connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgis"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "psip"),
        user=os.getenv("POSTGRES_USER", "psip"),
        password=os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        connect_timeout=20,
    )


def read_weights(path):
    """Lista wag: [(kryterium, waga, klasa_bdot, uzasadnienie), ...]."""
    if not path.is_file():
        raise FileNotFoundError(f"Brak pliku wag: {path}")
    weights = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = row["kryterium"].strip()
            if not IDENT.fullmatch(name):
                raise ValueError(f"Nieprawidlowa nazwa kryterium: {name!r}")
            value = float(row["waga"])
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{name}: waga {value} poza przedzialem -1..1")
            weights.append((name, value, row.get("klasa_bdot", "").strip(), row.get("uzasadnienie", "").strip()))
    names = [w[0] for w in weights]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Powtorzone kryteria w pliku wag: {duplicates}")
    if not any(w[1] for w in weights):
        raise ValueError("Wszystkie wagi sa zerowe - IOP nie ma sensu")
    return weights


def source_columns(cur):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"grid.{SOURCE}",))
    if not cur.fetchone()[0]:
        raise RuntimeError("Brak grid.h3_aggregation - najpierw uruchom load_bdot.py i aggregate_h3.py")
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'grid' AND table_name = %s ORDER BY ordinal_position
    """, (SOURCE,))
    columns = dict(cur.fetchall())
    missing_keys = KEY_COLUMNS - columns.keys()
    if missing_keys:
        raise RuntimeError(
            f"grid.{SOURCE} nie ma kolumn {sorted(missing_keys)}: tabela pochodzi ze starej wersji agregacji. "
            "Uruchom ponownie h3_generator.py, load.py i aggregate_h3.py (3 rozdzielczosci).")
    metrics = [c for c in columns if c not in KEY_COLUMNS]
    non_numeric = [c for c in metrics if columns[c] not in ("double precision", "bigint", "integer", "numeric", "real")]
    if non_numeric:
        raise RuntimeError(f"Kolumny nieliczbowe w {SOURCE}: {non_numeric}")
    return metrics


def min_max(values, groups):
    """Wzor (1): (V - Vmin) / (Vmax - Vmin) -> 0..1, osobno w kazdej grupie (rozdzielczosci).

    Gdy Vmax == Vmin (kryterium stale w calej rozdzielczosci) wynik = 0.
    """
    grouped = values.groupby(groups)
    vmin = grouped.transform("min")
    span = grouped.transform("max") - vmin
    return ((values - vmin) / span.where(span > 0)).fillna(0.0)


def normalize(frame, metrics):
    """Krok 1: normalizacja kazdego kryterium min-max w obrebie rozdzielczosci."""
    values = frame[metrics].astype(float)
    if values.isna().any().any():
        raise ValueError("Puste wartosci (NULL) w agregacji")
    return min_max(values, frame["resolution"])


def classify(iop):
    # digitize: 1..5; wartosc 1.0 nalezy do ostatniej klasy.
    classes = np.digitize(iop, CLASS_EDGES[1:-1], right=False) + 1
    return classes.astype(int)


def main():
    start = time.monotonic()
    weights = read_weights(WEIGHTS_FILE)
    weight_map = {name: value for name, value, *_ in weights}

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(2180, 4004)")
            if not cur.fetchone()[0]:
                raise RuntimeError("Obliczenie IOP jest juz uruchomione w innej sesji")
            metrics = source_columns(cur)

        without_weight = [m for m in metrics if m not in weight_map]
        if without_weight:
            raise ValueError(f"Brak wag dla kryteriow: {without_weight} - uzupelnij {WEIGHTS_FILE.name}")
        unused = [n for n in weight_map if n not in metrics]
        if unused:
            print(f"UWAGA: wagi bez kolumny w agregacji (pominiete): {unused}", flush=True)

        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT h3_id, resolution, {} FROM grid.{} ORDER BY resolution, h3_id").format(
                sql.SQL(", ").join(sql.Identifier(m) for m in metrics), sql.Identifier(SOURCE)))
            frame = pd.DataFrame(cur.fetchall(), columns=["h3_id", "resolution", *metrics])
        if frame.empty:
            raise RuntimeError(f"grid.{SOURCE} jest pusta")
        resolutions = sorted(frame["resolution"].unique())
        print(f"Kryteria: {len(metrics)}, komorki: {len(frame)}, rozdzielczosci: {[int(r) for r in resolutions]}", flush=True)

        # 1. Normalizacja (macierz komorki x kryteria).
        norm = normalize(frame, metrics)
        # 2. Wektor wag w tej samej kolejnosci co kolumny macierzy.
        w = np.array([weight_map[m] for m in metrics])
        # 3. IOP = K . w  (wzor 2) oraz wariant z zajec IOP / n.
        iop_sum = norm.to_numpy() @ w
        iop_mean = iop_sum / len(metrics)
        # 4. Ponowna normalizacja min-max do 0..1, osobno w kazdej rozdzielczosci.
        iop = min_max(pd.Series(iop_sum), frame["resolution"].reset_index(drop=True)).to_numpy()
        if np.isnan(iop).any() or iop.min() < -1e-9 or iop.max() > 1 + 1e-9:
            raise RuntimeError("IOP_norm poza zakresem 0..1 - blad obliczen")
        iop = np.clip(iop, 0.0, 1.0)
        classes = classify(iop)

        norm_cols = [f"{m}_norm" for m in metrics]
        out = pd.DataFrame(norm.to_numpy(), columns=norm_cols)
        out.insert(0, "resolution", frame["resolution"].to_numpy())
        out.insert(0, "h3_id", frame["h3_id"].to_numpy())
        out["iop_suma"] = iop_sum
        out["iop_srednia"] = iop_mean
        out["iop_norm"] = iop
        out["iop_klasa"] = classes
        out["iop_opis"] = [CLASS_LABELS[c - 1] for c in classes]

        with conn.cursor() as cur:
            # Wagi do bazy - jedno zrodlo prawdy dla wyniku.
            cur.execute("DROP TABLE IF EXISTS grid.iop_weights")
            cur.execute("""
                CREATE TABLE grid.iop_weights (
                    kryterium text PRIMARY KEY,
                    waga double precision NOT NULL CHECK (waga BETWEEN -1 AND 1),
                    klasa_bdot text,
                    uzasadnienie text
                )
            """)
            execute_values(cur, "INSERT INTO grid.iop_weights VALUES %s",
                           [w_ for w_ in weights if w_[0] in metrics])

            cur.execute("DROP TABLE IF EXISTS pg_temp.iop_values")
            cur.execute(sql.SQL("""
                CREATE TEMP TABLE iop_values (
                    h3_id text PRIMARY KEY, resolution integer, {norms},
                    iop_suma double precision, iop_srednia double precision,
                    iop_norm double precision, iop_klasa smallint, iop_opis text
                ) ON COMMIT DROP
            """).format(norms=sql.SQL(", ").join(
                sql.SQL("{} double precision").format(sql.Identifier(c)) for c in norm_cols)))
            execute_values(cur, "INSERT INTO iop_values VALUES %s",
                           out.itertuples(index=False, name=None), page_size=2000)

            cur.execute("DROP TABLE IF EXISTS grid.h3_iop_new")
            cur.execute(sql.SQL("""
                CREATE TABLE grid.h3_iop_new AS
                SELECT a.fid, v.*, a.geom::geometry(Polygon, 2180) AS geom
                FROM iop_values v JOIN grid.{src} a USING (h3_id)
            """).format(src=sql.Identifier(SOURCE)))
            cur.execute("SELECT COUNT(*) FROM grid.h3_iop_new")
            if cur.fetchone()[0] != len(out):
                raise RuntimeError("Niezgodna liczba komorek po polaczeniu z geometria")
            cur.execute("ALTER TABLE grid.h3_iop_new ADD PRIMARY KEY (fid)")
            cur.execute("CREATE UNIQUE INDEX ON grid.h3_iop_new (h3_id)")
            cur.execute("CREATE INDEX ON grid.h3_iop_new (resolution)")
            cur.execute("CREATE INDEX ON grid.h3_iop_new USING GIST (geom)")
            for column, comment in [
                ("iop_suma", "IOP = sum(k_i * IVRF_i), k_i znormalizowane min-max (Pokonieczny 2018, wzor 2)"),
                ("iop_srednia", "IOP / n, n = liczba kryteriow (wariant z zajec), zakres -1..1"),
                ("iop_norm", "IOP znormalizowany min-max w obrebie rozdzielczosci: 0 = najgorsza, 1 = najlepsza przejezdnosc"),
                ("iop_klasa", "5 rownych przedzialow iop_norm: 1 = 0-0.2 ... 5 = 0.8-1"),
            ]:
                cur.execute(sql.SQL("COMMENT ON COLUMN grid.h3_iop_new.{} IS %s").format(sql.Identifier(column)),
                            (comment,))

            # Podmiana wyniku w jednej transakcji (widoki zaleza od tabeli).
            for res in resolutions:
                cur.execute(sql.SQL("DROP VIEW IF EXISTS grid.{}").format(sql.Identifier(f"h3_iop_res{res}")))
            cur.execute("DROP TABLE IF EXISTS grid.h3_iop")
            cur.execute("ALTER TABLE grid.h3_iop_new RENAME TO h3_iop")
            for res in resolutions:
                cur.execute(sql.SQL("CREATE VIEW grid.{} AS SELECT * FROM grid.h3_iop WHERE resolution = {}").format(
                    sql.Identifier(f"h3_iop_res{res}"), sql.Literal(int(res))))
            save_qgis_style(cur, ["h3_iop", *[f"h3_iop_res{r}" for r in resolutions]])
            cur.execute("ANALYZE grid.h3_iop")
        conn.commit()

        summary = out.groupby("resolution").agg(
            komorki=("iop_norm", "size"), suma_min=("iop_suma", "min"), suma_max=("iop_suma", "max"),
            srednia_min=("iop_srednia", "min"), srednia_max=("iop_srednia", "max"),
            norm_srednia=("iop_norm", "mean"), norm_mediana=("iop_norm", "median"))
        print("IOP per rozdzielczosc:\n" + summary.round(4).to_string(), flush=True)
        counts = out.groupby(["resolution", "iop_opis"]).size().unstack(fill_value=0)
        print("Liczba komorek w klasach:\n" + counts.reindex(columns=CLASS_LABELS, fill_value=0).to_string(), flush=True)
        print(f"GOTOWE: grid.h3_iop ({len(out)} komorek, {len(metrics)} kryteriow); "
              f"czas {time.monotonic() - start:.1f} s", flush=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_qgis_style(cur, tables):
    """Zapis stylu do public.layer_styles - QGIS laduje go automatycznie przy dodaniu warstwy.

    Kolumna type zostaje NULL: QGIS dopasowuje wtedy styl niezaleznie od wykrytego typu geometrii
    (dla widokow bywa to 'Unknown geometry')."""
    if not STYLE_FILE.is_file():
        print(f"UWAGA: brak {STYLE_FILE.name} - pomijam zapis stylu QGIS", flush=True)
        return
    qml = STYLE_FILE.read_text(encoding="utf-8")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.layer_styles (
            id serial PRIMARY KEY,
            f_table_catalog varchar, f_table_schema varchar, f_table_name varchar,
            f_geometry_column varchar, stylename text, styleqml xml, stylesld xml,
            useasdefault boolean, description text, owner varchar(63) DEFAULT CURRENT_USER,
            ui xml, update_time timestamp DEFAULT CURRENT_TIMESTAMP, type varchar
        )
    """)
    for table in tables:
        cur.execute("""
            DELETE FROM public.layer_styles
            WHERE f_table_schema = 'grid' AND f_table_name = %s AND stylename = 'IOP przejezdnosc'
        """, (table,))
        cur.execute("""
            UPDATE public.layer_styles SET useasdefault = false
            WHERE f_table_schema = 'grid' AND f_table_name = %s
        """, (table,))
        cur.execute("""
            INSERT INTO public.layer_styles
                (f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                 stylename, styleqml, useasdefault, description)
            VALUES (current_database(), 'grid', %s, 'geom', 'IOP przejezdnosc', %s::xml, true,
                    'IOP_norm: 5 rownych przedzialow 0..1, czerwony-bialy-zielony')
        """, (table, qml))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BLAD: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
