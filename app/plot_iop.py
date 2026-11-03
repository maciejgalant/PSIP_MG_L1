"""Mapy IOP (PNG) dla kazdej rozdzielczosci H3 z grid.h3_iop.

Uruchom po compute_iop.py: python /app/plot_iop.py
Wynik: IOP_OUTPUT_DIR (domyslnie /app/output)/iop_res{7,8,9}.png
"""
import os
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402

from compute_iop import CLASS_EDGES, CLASS_LABELS  # noqa: E402

OUTPUT_DIR = Path(os.getenv("IOP_OUTPUT_DIR", "/app/output"))
# Te same kolory co w iop_style.qml: 0 czerwony -> 0.5 bialy -> 1 zielony.
COLORS = ["#d7191c", "#eb8c8d", "#ffffff", "#8ccaa0", "#1a9641"]


def engine():
    url = URL.create(
        "postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER", "psip"),
        password=os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST", "postgis"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "psip"),
    )
    return create_engine(url)


def main():
    eng = engine()
    cells = gpd.read_postgis(
        "SELECT h3_id, resolution, iop_norm, iop_klasa, geom FROM grid.h3_iop", eng, geom_col="geom")
    county = gpd.read_postgis("SELECT geom FROM public.powiat", eng, geom_col="geom")
    if cells.empty:
        raise RuntimeError("grid.h3_iop jest pusta - uruchom compute_iop.py")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    legend = [
        Patch(facecolor=COLORS[i], edgecolor="#6e6e6e",
              label=f"{CLASS_EDGES[i]:.1f} … {CLASS_EDGES[i + 1]:.1f}  {CLASS_LABELS[i]}")
        for i in range(len(COLORS))
    ]
    for res, part in cells.groupby("resolution"):
        fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
        part.plot(ax=ax, color=[COLORS[k - 1] for k in part["iop_klasa"]],
                  edgecolor="#6e6e6e", linewidth=0.15 if res >= 9 else 0.3)
        county.boundary.plot(ax=ax, color="black", linewidth=1.0)
        counts = part["iop_klasa"].value_counts().reindex(range(1, 6), fill_value=0)
        labels = [f"{p.get_label()}  ({counts[i + 1]})" for i, p in enumerate(legend)]
        ax.legend(handles=legend, labels=labels, title="IOP_norm (liczba komórek)", loc="lower left", fontsize=8)
        ax.set_title(f"Indeks przejezdności terenu (IOP) – H3 res {res}\n"
                     f"IOP_norm: średnia {part.iop_norm.mean():.3f}, mediana {part.iop_norm.median():.3f} "
                     f"(min-max, Pokonieczny 2018)", fontsize=11)
        ax.set_axis_off()
        out = OUTPUT_DIR / f"iop_res{res}.png"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"Zapisano {out}", flush=True)


if __name__ == "__main__":
    main()
