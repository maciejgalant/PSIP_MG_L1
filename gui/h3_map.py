"""Interaktywna mapa H3 z PostGIS w Tkinter (bez dodatkowych pakietow Python).

Uruchom z katalogu repozytorium: py gui/h3_map.py
Wymaga: uruchomionego Docker Compose oraz grid.h3_aggregation w PostGIS.
"""
import json
import math
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = str(ROOT / "docker-compose-local.yml")
DB_CMD = ["docker", "compose", "-f", COMPOSE, "exec", "-T", "postgis",
          "psql", "-X", "-A", "-t", "-P", "pager=off", "-U", "psip", "-d", "psip"]
# Pre-defined and safe identifier. Further metrics can be added after checking metadata.
METRIC = "adja_a_m2"


def read_hexagons(resolution):
    if resolution not in (7, 8, 9):
        raise ValueError("Nieprawidlowa rozdzielczosc")
    query = f"""
SELECT json_build_object(
  'id', h3_id, 'metric', {METRIC},
  'geom', ST_AsGeoJSON(geom, 5)::json
)::text
FROM grid.h3_aggregation
WHERE resolution = {resolution}
ORDER BY h3_id
"""
    result = subprocess.run(DB_CMD + ["-c", query], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


class H3Map(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PSIP LAB 3 | Mapa H3")
        self.geometry("1150x740")
        self.res = tk.IntVar(value=8)
        self.info = tk.StringVar(value="Wybierz rozdzielczosc i kliknij Wczytaj")
        self.data = []
        self.box = None
        self.center = (0, 0)
        self.scale = 1.0
        self.drag = None
        self.requests = queue.Queue()
        self.loading = False
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="H3 / BDOT10k", font=("Segoe UI", 16, "bold")).pack(side="left", padx=(0, 20))
        ttk.Label(top, text="Rozdzielczosc").pack(side="left")
        for res in (7, 8, 9):
            ttk.Radiobutton(top, text=str(res), variable=self.res, value=res).pack(side="left", padx=5)
        self.load_btn = ttk.Button(top, text="Wczytaj", command=self.load)
        self.load_btn.pack(side="left", padx=12)
        ttk.Button(top, text="Dopasuj widok", command=self.fit).pack(side="left")
        ttk.Label(self, textvariable=self.info, padding=(12, 4)).pack(fill="x")
        self.canvas = tk.Canvas(self, background="#f8fafc", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", lambda e: self.render())
        self.canvas.bind("<MouseWheel>", self.zoom)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_step(e.x, e.y, 1.2))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_step(e.x, e.y, 1 / 1.2))
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.pan)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.after(100, self.poll)

    def load(self):
        if self.loading:
            return
        resolution = self.res.get()
        self.loading = True
        self.load_btn.state(["disabled"])
        self.info.set(f"Pobieranie H3 R{resolution} z PostGIS...")
        def worker():
            try:
                self.requests.put(("data", resolution, read_hexagons(resolution)))
            except Exception as exc:
                self.requests.put(("error", resolution, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, res, payload = self.requests.get_nowait()
                self.loading = False
                self.load_btn.state(["!disabled"])
                if kind == "error":
                    self.info.set("Nie udalo sie pobrac danych")
                    messagebox.showerror("PostGIS", payload)
                else:
                    self.data = payload
                    self.fit()
                    self.info.set(f"H3 R{res}: {len(self.data)} komorek | Kolor: {METRIC} (m²)")
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def boundaries(self):
        for obj in self.data:
            geom = obj["geom"]
            if geom["type"] == "Polygon":
                polygons = [geom["coordinates"]]
            elif geom["type"] == "MultiPolygon":
                polygons = geom["coordinates"]
            else:
                continue
            for polygon in polygons:
                for ring in polygon[:1]:
                    yield obj, ring

    def fit(self):
        coords = [p for _, ring in self.boundaries() for p in ring]
        if not coords:
            return
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        self.center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        self.scale = 0.9 * min(w / max(xmax - xmin, 1), h / max(ymax - ymin, 1))
        self.render()

    def xy(self, x, y):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (w / 2 + (x - self.center[0]) * self.scale,
                h / 2 - (y - self.center[1]) * self.scale)

    def world(self, x, y):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (self.center[0] + (x - w / 2) / self.scale,
                self.center[1] - (y - h / 2) / self.scale)

    def render(self):
        self.canvas.delete("all")
        if not self.data:
            self.canvas.create_text(30, 30, text="Wczytaj H3 z PostGIS", anchor="nw", fill="#475569")
            return
        maximum = max((float(obj["metric"] or 0) for obj in self.data), default=0)
        for obj, ring in self.boundaries():
            flat = []
            for x, y in ring:
                flat.extend(self.xy(x, y))
            if len(flat) < 6:
                continue
            metric = float(obj["metric"] or 0)
            ratio = math.sqrt(metric / maximum) if maximum > 0 else 0
            color = "#e2e8f0" if metric == 0 else "#%02x%02x%02x" % (
                int(214 - 160 * ratio), int(233 - 75 * ratio), int(224 - 110 * ratio))
            item = self.canvas.create_polygon(flat, fill=color, outline="#64748b", width=0.5)
            self.canvas.addtag_withtag("cell", item)
            self.canvas.tag_bind(item, "<ButtonRelease-1>",
                                 lambda e, o=obj: self.select(o))
        self.canvas.create_rectangle(12, 12, 220, 55, fill="white", outline="#cbd5e1")
        self.canvas.create_text(20, 20, anchor="nw",
                                text=f"H3 R{self.res.get()} | {len(self.data)} komorek",
                                fill="#0f172a")

    def select(self, obj):
        self.info.set(f"H3: {obj['id']} | {METRIC}: {obj['metric']} m²")

    def zoom(self, event):
        self.zoom_step(event.x, event.y, 1.2 if event.delta > 0 else 1 / 1.2)

    def zoom_step(self, x, y, factor):
        if not self.data:
            return
        before = self.world(x, y)
        self.scale = max(0.00001, min(self.scale * factor, 10000))
        after = self.world(x, y)
        self.center = (self.center[0] + before[0] - after[0],
                       self.center[1] + before[1] - after[1])
        self.render()

    def press(self, event):
        self.drag = (event.x, event.y)
        self.moved = False

    def pan(self, event):
        if self.drag is None:
            return
        dx, dy = event.x - self.drag[0], event.y - self.drag[1]
        if abs(dx) + abs(dy) > 2:
            self.moved = True
        self.center = (self.center[0] - dx / self.scale,
                       self.center[1] + dy / self.scale)
        self.drag = (event.x, event.y)
        self.render()

    def release(self, event):
        self.drag = None


if __name__ == "__main__":
    H3Map().mainloop()
