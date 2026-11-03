"""Interaktywna mapa H3 z PostGIS w Tkinter: IOP i znormalizowane kryteria (LAB 4).

Uruchom z katalogu repozytorium: py gui/h3_map.py
Wymaga: uruchomionego Docker Compose (docker-compose-local.yml) oraz grid.h3_iop
(przycisk "Oblicz IOP" w panelu albo python /app/compute_iop.py w kontenerze).
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from psip_common import (CLASS_COLORS, CLASS_EDGES, CLASS_LABELS, H3_ID, IDENT,
                         iop_class, run_sql)

IOP_LAYER = "IOP – klasy przejezdności"


def read_layers():
    """Kolumny *_norm z grid.h3_iop i wagi z grid.iop_weights."""
    lines = run_sql(
        "SELECT 'COL|' || column_name FROM information_schema.columns "
        "WHERE table_schema='grid' AND table_name='h3_iop' AND column_name LIKE '%\\_norm' "
        "AND column_name <> 'iop_norm' ORDER BY 1",
        "SELECT 'W|' || kryterium || '|' || waga || '|' || COALESCE(klasa_bdot, '') FROM grid.iop_weights")
    columns, weights = [], {}
    for line in lines:
        parts = line.split("|")
        if parts[0] == "COL" and IDENT.fullmatch(parts[1]):
            columns.append(parts[1])
        elif parts[0] == "W" and len(parts) >= 4:
            weights[parts[1]] = (float(parts[2]), parts[3])
    if not columns:
        raise RuntimeError("Brak grid.h3_iop - uruchom najpierw obliczenie IOP (compute_iop.py)")
    return columns, weights


def read_hexagons(resolution, column):
    if resolution not in (7, 8, 9):
        raise ValueError("Nieprawidlowa rozdzielczosc")
    if not IDENT.fullmatch(column):
        raise ValueError("Nieprawidlowa nazwa kolumny")
    lines = run_sql(f"""
SELECT json_build_object('id', h3_id, 'v', {column}, 'iop', iop_norm,
                         'geom', ST_AsGeoJSON(geom, 1)::json)::text
FROM grid.h3_iop WHERE resolution = {resolution} ORDER BY h3_id""")
    cells = [json.loads(line) for line in lines]
    try:
        county = [json.loads(line) for line in run_sql(
            "SELECT ST_AsGeoJSON(ST_Boundary(ST_Transform(geom, 2180)), 1) FROM public.powiat")]
    except RuntimeError:
        county = []  # brak granicy powiatu nie blokuje mapy
    return cells, county


def read_cell(h3_id):
    if not H3_ID.fullmatch(h3_id):
        raise ValueError("Nieprawidlowy identyfikator H3")
    lines = run_sql(f"SELECT (to_jsonb(t) - 'geom')::text FROM grid.h3_iop t WHERE h3_id = '{h3_id}'")
    if not lines:
        raise RuntimeError(f"Brak komorki {h3_id}")
    return json.loads(lines[0])


def blend(value):
    """Skala 0..1 dla pojedynczego kryterium: bialy -> granatowy."""
    value = max(0.0, min(1.0, value))
    start, end = (248, 250, 252), (30, 64, 175)
    return "#%02x%02x%02x" % tuple(int(a + (b - a) * value) for a, b in zip(start, end))


class H3Map(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("PSIP LAB 4 | Mapa H3 – przejezdność terenu")
        self.geometry("1360x800")
        self.minsize(900, 560)
        self.res = tk.IntVar(value=8)
        self.layer = tk.StringVar(value=IOP_LAYER)
        self.info = tk.StringVar(value="Wczytywanie listy warstw z PostGIS...")
        self.data, self.county, self.columns, self.weights = [], [], [], {}
        self.loaded_layer, self.loaded_res = IOP_LAYER, 8
        self.items = {}
        self.selected = None
        self.center, self.scale = (0.0, 0.0), 1.0
        self.drag, self.moved = None, False
        self.requests: queue.Queue = queue.Queue()
        self.loading = False
        self._build()
        self._background("layers", read_layers)
        self.after(100, self.poll)

    # ---------- interfejs ----------
    def _build(self):
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="H3 / IOP", font=("Segoe UI", 16, "bold")).pack(side="left", padx=(0, 18))
        ttk.Label(top, text="Rozdzielczość").pack(side="left")
        for res in (7, 8, 9):
            ttk.Radiobutton(top, text=str(res), variable=self.res, value=res).pack(side="left", padx=4)
        ttk.Label(top, text="Warstwa").pack(side="left", padx=(16, 4))
        self.layer_box = ttk.Combobox(top, textvariable=self.layer, values=[IOP_LAYER], width=42, state="readonly")
        self.layer_box.pack(side="left")
        self.load_btn = ttk.Button(top, text="Wczytaj", command=self.load)
        self.load_btn.pack(side="left", padx=10)
        ttk.Button(top, text="Dopasuj widok", command=self.fit).pack(side="left")
        ttk.Label(self, textvariable=self.info, padding=(12, 2)).pack(fill="x")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas = tk.Canvas(body, background="#f1f5f9", highlightthickness=0)
        body.add(self.canvas, weight=4)
        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(side, weight=1)
        ttk.Label(side, text="Wybrana komórka", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.cell_info = tk.StringVar(value="Kliknij heksagon na mapie.")
        ttk.Label(side, textvariable=self.cell_info, wraplength=330, justify="left").pack(anchor="w", pady=(4, 8))
        ttk.Label(side, text="Największy wpływ na IOP (k · w)").pack(anchor="w")
        columns = ("kryterium", "k", "w", "kw")
        self.tree = ttk.Treeview(side, columns=columns, show="headings", height=14)
        for col, title, width in zip(columns, ("Kryterium", "k", "w", "k·w"), (230, 55, 50, 60)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w" if col == "kryterium" else "e")
        self.tree.tag_configure("plus", foreground="#15803d")
        self.tree.tag_configure("minus", foreground="#b91c1c")
        self.tree.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda e: self.render())
        self.canvas.bind("<MouseWheel>", self.zoom)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_step(e.x, e.y, 1.25))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_step(e.x, e.y, 1 / 1.25))
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.pan)
        self.canvas.bind("<ButtonRelease-1>", self.release)

    # ---------- watki i dane ----------
    def _background(self, kind, func, *args):
        def worker():
            try:
                self.requests.put((kind, args, func(*args)))
            except Exception as exc:  # noqa: BLE001
                self.requests.put(("error", args, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def load(self):
        if self.loading:
            return
        resolution, layer = self.res.get(), self.layer.get()
        column = "iop_norm" if layer == IOP_LAYER else layer.split(" ")[0]
        self.loading = True
        self.load_btn.state(["disabled"])
        self.info.set(f"Pobieranie H3 R{resolution} ({column}) z PostGIS...")
        self._background("data", lambda: (layer, resolution, read_hexagons(resolution, column)))

    def poll(self):
        try:
            while True:
                kind, _args, payload = self.requests.get_nowait()
                if kind == "error":
                    self.loading = False
                    self.load_btn.state(["!disabled"])
                    self.info.set("Błąd pobierania danych z PostGIS")
                    messagebox.showerror("PostGIS", payload, parent=self)
                elif kind == "layers":
                    self.columns, self.weights = payload
                    values = [IOP_LAYER] + [self._layer_name(c) for c in self.columns]
                    self.layer_box.configure(values=values)
                    self.info.set(f"{len(self.columns)} kryteriów. Wybierz rozdzielczość i kliknij Wczytaj.")
                    self.load()
                elif kind == "data":
                    self.loading = False
                    self.load_btn.state(["!disabled"])
                    self.loaded_layer, self.loaded_res, (self.data, self.county) = payload
                    self.selected = None
                    self.cell_info.set("Kliknij heksagon na mapie.")
                    self.tree.delete(*self.tree.get_children())
                    self.fit()
                    self.info.set(f"H3 R{self.loaded_res}: {len(self.data)} komórek | {self.loaded_layer}")
                elif kind == "cell":
                    self._show_cell(payload)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def _layer_name(self, column):
        criterion = column[:-5]
        weight, name = self.weights.get(criterion, (0.0, ""))
        return f"{column}  ({name}, w={weight:+.2f})"

    # ---------- rysowanie ----------
    def polygons(self):
        for obj in self.data:
            geom = obj["geom"]
            rings = [geom["coordinates"][0]] if geom["type"] == "Polygon" else \
                [poly[0] for poly in geom["coordinates"]] if geom["type"] == "MultiPolygon" else []
            for ring in rings:
                yield obj, ring

    def fit(self):
        coords = [p for _, ring in self.polygons() for p in ring]
        if not coords:
            self.render()
            return
        xs, ys = [p[0] for p in coords], [p[1] for p in coords]
        self.center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        self.scale = 0.92 * min(w / max(max(xs) - min(xs), 1), h / max(max(ys) - min(ys), 1))
        self.render()

    def xy(self, x, y):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return w / 2 + (x - self.center[0]) * self.scale, h / 2 - (y - self.center[1]) * self.scale

    def world(self, x, y):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return self.center[0] + (x - w / 2) / self.scale, self.center[1] - (y - h / 2) / self.scale

    def color(self, obj):
        if self.loaded_layer == IOP_LAYER:
            return CLASS_COLORS[iop_class(float(obj["iop"])) - 1]
        return blend(float(obj["v"] or 0))

    def render(self):
        self.canvas.delete("all")
        self.items = {}
        if not self.data:
            self.canvas.create_text(30, 30, text="Wczytaj H3 z PostGIS", anchor="nw", fill="#475569")
            return
        thin = 0.3 if self.loaded_res == 9 else 0.6
        for obj, ring in self.polygons():
            flat = [c for x, y in ring for c in self.xy(x, y)]
            if len(flat) < 6:
                continue
            item = self.canvas.create_polygon(flat, fill=self.color(obj), outline="#64748b",
                                              width=thin, tags=("cell",))
            self.items[item] = obj
        for line in self.county:
            parts = [line["coordinates"]] if line["type"] == "LineString" else line["coordinates"]
            for part in parts:
                flat = [c for x, y in part for c in self.xy(x, y)]
                if len(flat) >= 4:
                    self.canvas.create_line(flat, fill="#0f172a", width=2, tags=("county",))
        if self.selected:
            self._highlight(self.selected)
        self.draw_legend()

    def draw_legend(self):
        self.canvas.delete("legend")
        x, y = 14, 14
        if self.loaded_layer == IOP_LAYER:
            counts = [0] * 5
            for obj in self.data:
                counts[iop_class(float(obj["iop"])) - 1] += 1
            rows = [(CLASS_COLORS[i], f"{CLASS_EDGES[i]:.1f}–{CLASS_EDGES[i + 1]:.1f}  {CLASS_LABELS[i]}  ({counts[i]})")
                    for i in range(5)]
            title = f"IOP_norm | H3 R{self.loaded_res}"
        else:
            rows = [(blend(v), f"{v:.2f}") for v in (0.0, 0.25, 0.5, 0.75, 1.0)]
            title = f"{self.loaded_layer.split(' ')[0]} | H3 R{self.loaded_res}"
        height = 30 + 20 * len(rows)
        self.canvas.create_rectangle(x, y, x + 250, y + height, fill="white", outline="#cbd5e1", tags="legend")
        self.canvas.create_text(x + 10, y + 8, text=title, anchor="nw", font=("Segoe UI", 9, "bold"), tags="legend")
        for i, (fill, label) in enumerate(rows):
            top = y + 28 + 20 * i
            self.canvas.create_rectangle(x + 10, top, x + 28, top + 14, fill=fill, outline="#64748b", tags="legend")
            self.canvas.create_text(x + 36, top + 7, text=label, anchor="w", font=("Segoe UI", 9), tags="legend")

    # ---------- interakcja ----------
    def zoom(self, event):
        self.zoom_step(event.x, event.y, 1.25 if event.delta > 0 else 1 / 1.25)

    def zoom_step(self, x, y, factor):
        if not self.data:
            return
        new_scale = max(1e-5, min(self.scale * factor, 1e4))
        factor = new_scale / self.scale
        before = self.world(x, y)
        self.scale = new_scale
        after = self.world(x, y)
        self.center = (self.center[0] + before[0] - after[0], self.center[1] + before[1] - after[1])
        # Przeskalowanie istniejacych wielokatow zamiast ponownego rysowania (szybkie dla R9).
        self.canvas.scale("cell", x, y, factor, factor)
        self.canvas.scale("selected", x, y, factor, factor)
        self.canvas.scale("county", x, y, factor, factor)

    def press(self, event):
        self.drag, self.moved = (event.x, event.y), False

    def pan(self, event):
        if self.drag is None:
            return
        dx, dy = event.x - self.drag[0], event.y - self.drag[1]
        if abs(dx) + abs(dy) > 2:
            self.moved = True
        self.center = (self.center[0] - dx / self.scale, self.center[1] + dy / self.scale)
        self.drag = (event.x, event.y)
        self.canvas.move("cell", dx, dy)
        self.canvas.move("selected", dx, dy)
        self.canvas.move("county", dx, dy)

    def release(self, event):
        was_drag, self.drag = self.moved, None
        if was_drag:
            return
        hit = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        cells = [i for i in hit if i in self.items]
        if cells:
            self.select(self.items[cells[-1]])

    def _highlight(self, obj):
        self.canvas.delete("selected")
        for item, cell in self.items.items():
            if cell is obj:
                coords = self.canvas.coords(item)
                self.canvas.create_polygon(coords, fill="", outline="#0f172a", width=3, tags=("selected",))
        self.canvas.tag_raise("legend")

    def select(self, obj):
        self.selected = obj
        self._highlight(obj)
        value = float(obj["iop"])
        self.info.set(f"H3 {obj['id']} | IOP_norm {value:.3f} | klasa {iop_class(value)}: "
                      f"{CLASS_LABELS[iop_class(value) - 1]}")
        self.cell_info.set(f"{obj['id']}\nPobieranie szczegółów...")
        self._background("cell", read_cell, obj["id"])

    def _show_cell(self, row):
        value = float(row["iop_norm"])
        self.cell_info.set(
            f"H3 {row['h3_id']} (R{row['resolution']})\n"
            f"IOP_norm = {value:.3f} → klasa {row['iop_klasa']}: {row['iop_opis']}\n"
            f"IOP = Σ k·w = {float(row['iop_suma']):.3f}\n"
            f"IOP / n = {float(row['iop_srednia']):.4f}")
        contributions = []
        for key, k in row.items():
            if key.endswith("_norm") and key != "iop_norm" and k:
                weight, name = self.weights.get(key[:-5], (0.0, ""))
                if weight:
                    contributions.append((k * weight, key[:-5], name, k, weight))
        contributions.sort(key=lambda c: abs(c[0]), reverse=True)
        self.tree.delete(*self.tree.get_children())
        for kw, criterion, name, k, weight in contributions[:15]:
            self.tree.insert("", "end", values=(f"{criterion} {name}"[:48], f"{k:.3f}", f"{weight:+.2f}", f"{kw:+.3f}"),
                             tags=("plus" if kw > 0 else "minus",))


def main():
    root = tk.Tk()
    root.withdraw()
    window = H3Map(root)
    window.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
