"""Okna modalne aplikacji PSIP GIS - kazdy przycisk paska narzedzi otwiera jedno z nich."""
from __future__ import annotations

import datetime as dt
import os
import queue
import string
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from crs import KNOWN, distance_2180, format_coords
from layers import (IOP_RAMP, Layer, Style, fmt_number, inspect_shapefile, load_county_layer,
                    load_h3_layer, load_shapefile, new_point_layer, save_point_layer)
from pdf_export import export_map, nice_distance
from psip_common import ROOT, WEIGHTS_FILE, CommandRunner, compose_python, read_weights, write_weights
from ui_common import Modal, confirm_modal, info_modal

RAMPS = {
    "IOP: czerwony – biały – zielony": list(IOP_RAMP),
    "Niebieski (jasny → ciemny)": ["#eff6ff", "#1e3a8a"],
    "Zielony (jasny → ciemny)": ["#f0fdf4", "#14532d"],
    "Żółty – pomarańczowy – czerwony": ["#ffffb2", "#fd8d3c", "#bd0026"],
    "Viridis": ["#440154", "#21918c", "#fde725"],
    "Szary": ["#f8fafc", "#1e293b"],
    "Brązowy – biały – turkusowy": ["#a6611a", "#f5f5f5", "#018571"],
}
GROUPS = {"wszystkie": "", "SK – sieć komunikacyjna": "sk", "PT – pokrycie terenu": "pt",
          "SW – sieć wodna": "sw", "BU – budynki i budowle": "bu", "KU – kompleksy użytkowania": "ku",
          "OI – obiekty inne": "oi", "SU – uzbrojenie terenu": "su", "AD / TC – administracja": ("ad", "tc")}


def confirm(app, title, text) -> bool:
    return confirm_modal(app, app.theme, title, text)


def error(app, title, text) -> None:
    info_modal(app, app.theme, title, text)


def _layer_names(layers):
    return [f"{i + 1}. {layer.name}" for i, layer in enumerate(layers)]


class LayerPicker(ttk.Combobox):
    """Combobox wyboru warstwy; .layer zwraca wybrany obiekt Layer."""

    def __init__(self, master, layers: list[Layer], selected: Layer | None, on_change=None, width=40):
        self.layers = layers
        self.var = tk.StringVar()
        super().__init__(master, textvariable=self.var, values=_layer_names(layers), state="readonly", width=width)
        if layers:
            index = layers.index(selected) if selected in layers else 0
            self.current(index)
        if on_change:
            self.bind("<<ComboboxSelected>>", lambda e: on_change(self.layer))

    @property
    def layer(self) -> Layer | None:
        index = self.current()
        return self.layers[index] if 0 <= index < len(self.layers) else None


# ====================================================================== wczytanie SHP
class LoadShpDialog(Modal):
    last_dir: Path | None = None

    def __init__(self, app) -> None:
        super().__init__(app, "Wczytaj warstwę SHP", app.theme, 760, 560, resizable=True)
        self.app = app
        start = LoadShpDialog.last_dir or (ROOT / "data")
        self.folder = tk.StringVar(value=str(start))
        self.path = tk.StringVar()
        self.crs = tk.StringVar(value="Automatycznie (.prj)")
        self.details = tk.StringVar(value="Wybierz plik .shp z listy albo przez „Przeglądaj…”.")
        self.messages: queue.Queue = queue.Queue()
        b = self.body
        nav = ttk.Frame(b)
        nav.pack(fill="x")
        ttk.Button(nav, text="↑ Wyżej", command=self.up).pack(side="left")
        ttk.Entry(nav, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(nav, text="Otwórz folder", command=lambda: self.open_folder(Path(self.folder.get()))).pack(
            side="left")
        quick = ttk.Frame(b)
        quick.pack(fill="x", pady=(6, 6))
        ttk.Label(quick, text="Skróty:", style="Muted.TLabel").pack(side="left")
        for label, path in (("Dane projektu", ROOT / "data"), ("Repozytorium", ROOT), ("Dom", Path.home()),
                            ("Pulpit", Path.home() / "Desktop"), ("Pobrane", Path.home() / "Downloads")):
            if path.is_dir():
                ttk.Button(quick, text=label, style="Small.TButton",
                           command=lambda p=path: self.open_folder(p)).pack(side="left", padx=2)
        for drive in self._drives():
            ttk.Button(quick, text=drive, style="Small.TButton",
                       command=lambda d=drive: self.open_folder(Path(d))).pack(side="left", padx=2)
        frame = ttk.Frame(b)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("typ", "rozmiar"), show="tree headings", height=12)
        self.tree.heading("#0", text="Nazwa")
        self.tree.heading("typ", text="Typ")
        self.tree.heading("rozmiar", text="Rozmiar")
        self.tree.column("#0", width=420)
        self.tree.column("typ", width=110)
        self.tree.column("rozmiar", width=90, anchor="e")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_double)
        file_row = ttk.Frame(b)
        file_row.pack(fill="x", pady=(8, 2))
        ttk.Label(file_row, text="Plik").pack(side="left")
        ttk.Entry(file_row, textvariable=self.path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(file_row, text="Przeglądaj…", command=self.browse).pack(side="left")
        crs_row = ttk.Frame(b)
        crs_row.pack(fill="x", pady=4)
        ttk.Label(crs_row, text="Układ źródłowy").pack(side="left")
        ttk.Combobox(crs_row, textvariable=self.crs, state="readonly", width=28,
                     values=["Automatycznie (.prj)"] + list(KNOWN)).pack(side="left", padx=6)
        ttk.Label(crs_row, text="→ mapa w EPSG:2180", style="Muted.TLabel").pack(side="left")
        ttk.Label(b, textvariable=self.details, style="Muted.TLabel", wraplength=700, justify="left").pack(
            anchor="w", pady=(4, 0))
        self.add_button("Anuluj", self.close)
        self.btn_load = self.add_button("Wczytaj warstwę", self.load, accent=True)
        self.open_folder(start)

    @staticmethod
    def _drives():
        if os.name != "nt":
            return []
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]

    def open_folder(self, folder: Path) -> None:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            self.details.set(f"Nie można otworzyć folderu: {exc}")
            return
        self.folder.set(str(folder))
        LoadShpDialog.last_dir = folder
        self.tree.delete(*self.tree.get_children())
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    self.tree.insert("", "end", iid=str(entry), text="▸ " + entry.name, values=("folder", ""))
                elif entry.suffix.lower() == ".shp":
                    size = entry.stat().st_size
                    self.tree.insert("", "end", iid=str(entry), text=entry.name,
                                     values=("warstwa SHP", f"{size / 1024:,.0f} kB".replace(",", " ")))
            except OSError:
                continue

    def up(self) -> None:
        current = Path(self.folder.get())
        if current.parent != current:
            self.open_folder(current.parent)

    def on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        path = Path(sel[0])
        if path.suffix.lower() == ".shp":
            self.path.set(str(path))
            try:
                info = inspect_shapefile(path)
                fields = ", ".join(info["pola"][:12]) + (" …" if len(info["pola"]) > 12 else "")
                self.details.set(f"Warstwa {info['typ']}, obiektów: {info['obiekty']}, układ: {info['crs']}, "
                                 f"kodowanie DBF: {info['kodowanie']}\nPola: {fields or '—'}")
            except Exception as exc:  # noqa: BLE001
                self.details.set(f"Nie można odczytać pliku: {exc}")

    def on_double(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel and Path(sel[0]).is_dir():
            self.open_folder(Path(sel[0]))
        elif sel:
            self.load()

    def browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Wybierz plik SHP", initialdir=self.folder.get(),
                                          filetypes=[("ESRI Shapefile", "*.shp"), ("Wszystkie pliki", "*.*")])
        if path:
            self.open_folder(Path(path).parent)
            self.path.set(path)
            if self.tree.exists(str(Path(path))):
                self.tree.selection_set(str(Path(path)))
                self.tree.see(str(Path(path)))

    def load(self) -> None:
        path = self.path.get().strip()
        if not path:
            self.details.set("Najpierw wybierz plik .shp.")
            return
        override = KNOWN.get(self.crs.get())
        self.btn_load.state(["disabled"])
        self.details.set("Wczytywanie…")
        self.configure(cursor="watch")

        def worker():
            try:
                self.messages.put(("ok", load_shapefile(path, override)))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload = self.messages.get_nowait()
        except queue.Empty:
            self.after(80, self._poll)
            return
        self.configure(cursor="")
        self.btn_load.state(["!disabled"])
        if kind == "error":
            self.details.set(f"Błąd: {payload}")
            return
        self.close()
        self.app.add_layer(payload)


# ====================================================================== H3 z PostGIS
class H3Dialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Warstwa H3 / IOP z PostGIS", app.theme, 460, 290)
        self.app = app
        self.res = tk.IntVar(value=8)
        self.county = tk.BooleanVar(value=not any(l.origin == "db" for l in app.layers))
        self.status = tk.StringVar(value="Wymaga uruchomionych kontenerów (docker-compose-local.yml) i grid.h3_iop.")
        self.messages: queue.Queue = queue.Queue()
        b = self.body
        ttk.Label(b, text="Rozdzielczość siatki H3", style="H.TLabel").pack(anchor="w")
        row = ttk.Frame(b)
        row.pack(anchor="w", pady=6)
        for res, label in ((7, "R7 (~5,2 km²)"), (8, "R8 (~0,74 km²)"), (9, "R9 (~0,1 km²)")):
            ttk.Radiobutton(row, text=label, variable=self.res, value=res).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(b, text="Dodaj też granicę powiatu (public.powiat)", variable=self.county).pack(
            anchor="w", pady=4)
        self.progress = ttk.Progressbar(b, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 4))
        ttk.Label(b, textvariable=self.status, style="Muted.TLabel", wraplength=420).pack(anchor="w")
        self.add_button("Anuluj", self.close)
        self.btn = self.add_button("Wczytaj z PostGIS", self.load, accent=True)

    def load(self) -> None:
        res, county = self.res.get(), self.county.get()
        self.btn.state(["disabled"])
        self.progress.start(12)
        self.status.set(f"Pobieranie H3 R{res} z grid.h3_iop…")

        def worker():
            try:
                layers = [load_h3_layer(res)]
                if county:
                    try:
                        layers.append(load_county_layer())
                    except Exception:  # noqa: BLE001 - granica jest opcjonalna
                        pass
                self.messages.put(("ok", layers))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload = self.messages.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(100, self._poll)
            return
        self.progress.stop()
        self.btn.state(["!disabled"])
        if kind == "error":
            self.status.set(f"Błąd: {payload}")
            return
        self.close()
        h3, *rest = payload
        for layer in rest:
            self.app.add_layer(layer, zoom=False)
        self.app.add_layer(h3)


# ====================================================================== analiza IOP – suwaki wag
class AnalysisDialog(Modal):
    """Interaktywna analiza IOP: suwak wagi IVRF przy kazdym kryterium, przeliczenie na zywo."""

    def __init__(self, app, layer: Layer | None = None) -> None:
        super().__init__(app, "Analiza IOP – wagi kryteriów (IVRF)", app.theme, 720, 700, resizable=True)
        self.dock_right = True
        self.app = app
        self.candidates = [l for l in app.layers if any(f.endswith("_norm") and f != "iop_norm" for f in l.fields)]
        self.layer = layer if layer in self.candidates else (app.active if app.active in self.candidates else
                                                             (self.candidates[0] if self.candidates else None))
        self.values: dict[str, tk.DoubleVar] = {}
        self.labels: dict[str, tk.Label] = {}
        self.rows: dict[str, tk.Frame] = {}
        self.weights_meta: dict[str, dict] = {}
        self.group = tk.StringVar(value="wszystkie")
        self.search = tk.StringVar()
        self.only_nonzero = tk.BooleanVar(value=False)
        self.stats = tk.StringVar()
        self.messages: queue.Queue = queue.Queue()
        self.runner = CommandRunner(self.messages)
        self._job = None
        if not self.layer:
            ttk.Label(self.body, text="Brak warstwy z kryteriami *_norm.\nWczytaj warstwę H3 z PostGIS "
                                      "(przycisk „H3 z PostGIS”).", justify="left").pack(anchor="w")
            self.add_button("Zamknij", self.close, accent=True)
            return
        self._build()
        self.reload_weights()

    def _build(self) -> None:
        t = self.theme
        b = self.body
        top = ttk.Frame(b)
        top.pack(fill="x")
        ttk.Label(top, text="Warstwa").pack(side="left")
        self.picker = LayerPicker(top, self.candidates, self.layer, self._layer_changed, width=34)
        self.picker.pack(side="left", padx=6)
        ttk.Checkbutton(top, text="tylko wagi ≠ 0", variable=self.only_nonzero,
                        command=self._filter).pack(side="left", padx=8)
        flt = ttk.Frame(b)
        flt.pack(fill="x", pady=6)
        ttk.Label(flt, text="Grupa").pack(side="left")
        box = ttk.Combobox(flt, textvariable=self.group, values=list(GROUPS), state="readonly", width=28)
        box.pack(side="left", padx=6)
        box.bind("<<ComboboxSelected>>", lambda e: self._filter())
        ttk.Label(flt, text="Szukaj").pack(side="left")
        ttk.Entry(flt, textvariable=self.search, width=18).pack(side="left", padx=6)
        self.search.trace_add("write", lambda *a: self._filter())
        ttk.Label(b, text="−1 utrudnia przejazd • 0 brak wpływu • +1 ułatwia. Mapa przelicza się na bieżąco: "
                          "IOP = Σ k·w, potem normalizacja min–max do 0..1 (jak compute_iop.py).",
                  style="Muted.TLabel", wraplength=680, justify="left").pack(anchor="w")
        holder = ttk.Frame(b)
        holder.pack(fill="both", expand=True, pady=6)
        self.canvas = tk.Canvas(holder, highlightthickness=1, bg=t["panel"], highlightbackground=t["border"])
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=t["panel"])
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        ttk.Label(b, textvariable=self.stats, wraplength=680, justify="left").pack(anchor="w")
        self.log = tk.Text(b, height=5, font=("Consolas", 8), bg=t["field"], fg=t["fg"], relief="flat",
                           state="disabled")
        self.log.pack(fill="x", pady=(4, 0))
        self.add_button("Zamknij", self.close)
        self.btn_db = self.add_button("Zapisz i przelicz w PostGIS", self.save_and_compute)
        self.add_button("Zapisz wagi do CSV", self.save_csv, accent=True)
        self.add_button("Przywróć z pliku", self.reload_weights, side="left")
        self.add_button("IOP z bazy", self.show_db_iop, side="left")

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _layer_changed(self, layer) -> None:
        self.layer = layer
        self.reload_weights()

    def reload_weights(self) -> None:
        t = self.theme
        try:
            rows = read_weights()
        except Exception as exc:  # noqa: BLE001
            self.stats.set(f"Nie można wczytać {WEIGHTS_FILE.name}: {exc}")
            rows = []
        fields = set(self.layer.fields)
        by_name = {r["kryterium"]: r for r in rows}
        criteria = sorted({f[:-5] for f in fields if f.endswith("_norm") and f != "iop_norm"})
        for child in self.inner.winfo_children():
            child.destroy()
        self.values, self.labels, self.rows, self.weights_meta = {}, {}, {}, {}
        for name in criteria:
            meta = by_name.get(name, {"kryterium": name, "waga": "0", "klasa_bdot": "", "uzasadnienie": ""})
            self.weights_meta[name] = meta
            row = tk.Frame(self.inner, bg=t["panel"])
            var = tk.DoubleVar(value=float(meta["waga"]))
            self.values[name] = var
            tk.Label(row, text=name, width=14, anchor="w", bg=t["panel"], fg=t["fg"],
                     font=("Consolas", 9)).pack(side="left", padx=(6, 2))
            tk.Label(row, text=(meta.get("klasa_bdot") or "")[:30], width=30, anchor="w", bg=t["panel"],
                     fg=t["muted"], font=("Segoe UI", 8)).pack(side="left")
            ttk.Scale(row, from_=-1.0, to=1.0, variable=var, length=210,
                      command=lambda v, n=name: self._changed(n)).pack(side="left", padx=4)
            value = tk.Label(row, width=6, anchor="e", bg=t["panel"], font=("Consolas", 10, "bold"))
            value.pack(side="left")
            ttk.Button(row, text="0", width=2, style="Small.TButton",
                       command=lambda n=name: self._set(n, 0.0)).pack(side="left", padx=4)
            self.labels[name], self.rows[name] = value, row
            self._paint(name)
        self._filter()
        self.recompute()

    def _set(self, name, value) -> None:
        self.values[name].set(value)
        self._changed(name)

    def _paint(self, name) -> None:
        v = self.values[name].get()
        color = "#16a34a" if v > 0 else "#dc2626" if v < 0 else self.theme["muted"]
        self.labels[name].configure(text=f"{v:+.2f}", fg=color)

    def _changed(self, name) -> None:
        var = self.values[name]
        rounded = round(var.get() * 20) / 20
        if abs(rounded - var.get()) > 1e-9:
            var.set(rounded)
        self._paint(name)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(180, self.recompute)

    def _filter(self) -> None:
        prefix = GROUPS.get(self.group.get(), "")
        text = self.search.get().strip().lower()
        for name, row in self.rows.items():
            meta = self.weights_meta[name]
            visible = (not prefix or name.startswith(prefix)) and (
                not text or text in name or text in (meta.get("klasa_bdot") or "").lower()) and (
                not self.only_nonzero.get() or abs(self.values[name].get()) > 1e-9)
            if visible:
                row.pack(fill="x", pady=1)
            else:
                row.pack_forget()

    def recompute(self) -> None:
        """IOP na zywo dla wybranej warstwy - te same wzory co app/compute_iop.py."""
        self._job = None
        layer = self.layer
        weights = {n: v.get() for n, v in self.values.items() if abs(v.get()) > 1e-9}
        if not weights:
            self.stats.set("Wszystkie wagi są zerowe – IOP nie ma sensu.")
            return
        sums = []
        for feat in layer.features:
            total = 0.0
            for name, w in weights.items():
                k = feat.attrs.get(f"{name}_norm")
                if k:
                    total += float(k) * w
            feat.attrs["iop_analiza_suma"] = total
            sums.append(total)
        lo, hi = min(sums), max(sums)
        span = (hi - lo) or 1.0
        for feat in layer.features:
            feat.attrs["iop_analiza"] = round((feat.attrs["iop_analiza_suma"] - lo) / span, 6)
        for f in ("iop_analiza", "iop_analiza_suma"):
            if f not in layer.fields:
                layer.fields.append(f)
        st = layer.style
        if st.field != "iop_analiza":
            layer.style = Style(mode="graduated", field="iop_analiza", vmin=0, vmax=1, classes=5,
                                colors=list(IOP_RAMP), outline=st.outline, width=st.width,
                                labels=["bardzo trudna", "trudna", "przeciętna", "dobra", "bardzo dobra"])
        counts = [0] * layer.style.classes   # uzytkownik mogl zmienic liczbe klas w „Styl warstwy”
        for feat in layer.features:
            counts[layer.style.class_index(feat.attrs["iop_analiza"]) or 0] += 1
        self.stats.set(f"Aktywnych kryteriów: {len(weights)} z {len(self.values)} • Σk·w od {lo:.3f} do {hi:.3f} • "
                       f"klasy 1–{len(counts)}: {' / '.join(map(str, counts))}  (pole „iop_analiza”)")
        self.app.refresh_layers()
        self.app.map.render()

    def show_db_iop(self) -> None:
        if "iop_norm" in self.layer.fields:
            st = self.layer.style
            self.layer.style = Style(mode="graduated", field="iop_norm", vmin=0, vmax=1, classes=5,
                                     colors=list(IOP_RAMP), outline=st.outline, width=st.width,
                                     labels=["bardzo trudna", "trudna", "przeciętna", "dobra", "bardzo dobra"])
            self.app.refresh_layers()
            self.app.map.render()
            self.stats.set("Wyświetlono iop_norm z bazy (wagi z ostatniego compute_iop.py).")

    def _collect_rows(self):
        try:
            rows = read_weights()
        except Exception:  # noqa: BLE001
            rows = []
        known = {r["kryterium"] for r in rows}
        for row in rows:
            if row["kryterium"] in self.values:
                value = self.values[row["kryterium"]].get()
                if abs(value - float(row["waga"])) > 1e-9:
                    row["waga"] = f"{value:.2f}".replace("-0.00", "0.00")
        for name, var in self.values.items():
            if name not in known:
                rows.append({"kryterium": name, "waga": f"{var.get():.2f}", "klasa_bdot": "", "uzasadnienie": ""})
        return rows

    def save_csv(self) -> bool:
        rows = self._collect_rows()
        if not any(float(r["waga"]) for r in rows):
            self.stats.set("Wszystkie wagi są zerowe – nie zapisano.")
            return False
        try:
            write_weights(rows)
        except Exception as exc:  # noqa: BLE001
            self.stats.set(f"Zapis nie powiódł się: {exc}")
            return False
        self.stats.set(f"Zapisano {WEIGHTS_FILE.relative_to(ROOT)} ({len(rows)} kryteriów).")
        return True

    def save_and_compute(self) -> None:
        if self.runner.busy or not self.save_csv():
            return
        self.btn_db.state(["disabled"])
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.runner.start("Obliczenie IOP", compose_python("compute_iop.py"))
        self.after(100, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", str(value) + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "done":
                    ok, message, _lines = value
                    self.btn_db.state(["!disabled"])
                    self.stats.set(message + (" – wczytaj ponownie warstwę H3, by zobaczyć iop_norm z bazy."
                                              if ok else ""))
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ====================================================================== styl warstwy
class StyleDialog(Modal):
    def __init__(self, app, layer: Layer | None = None) -> None:
        super().__init__(app, "Edycja stylu warstwy", app.theme, 620, 600)
        self.app = app
        if not app.layers:
            ttk.Label(self.body, text="Brak warstw na mapie.").pack(anchor="w")
            self.add_button("Zamknij", self.close, accent=True)
            return
        self.layer = layer or app.active or app.layers[0]
        self.vars = {}
        b = self.body
        top = ttk.Frame(b)
        top.pack(fill="x")
        ttk.Label(top, text="Warstwa").pack(side="left")
        self.picker = LayerPicker(top, app.layers, self.layer, self._load, width=44)
        self.picker.pack(side="left", padx=6)
        self.mode = tk.StringVar()
        modes = ttk.Frame(b)
        modes.pack(fill="x", pady=(10, 4))
        ttk.Radiobutton(modes, text="Jeden symbol", variable=self.mode, value="single",
                        command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(modes, text="Stopniowany (schemat barw wg wartości)", variable=self.mode,
                        value="graduated", command=self._mode_changed).pack(side="left", padx=12)

        self.single = ttk.LabelFrame(b, text="Symbol", padding=10)
        self.grad = ttk.LabelFrame(b, text="Schemat barw – zakres wartości zjawiska", padding=10)
        common = ttk.LabelFrame(b, text="Obrys", padding=10)
        self.fill_btn = self._color_row(self.single, 0, "Wypełnienie", "fill")
        self.no_fill = tk.BooleanVar()
        ttk.Checkbutton(self.single, text="bez wypełnienia", variable=self.no_fill, command=self._preview).grid(
            row=0, column=2, sticky="w", padx=8)
        ttk.Label(self.single, text="Rozmiar punktu").grid(row=1, column=0, sticky="w", pady=4)
        self.point_size = tk.DoubleVar()
        ttk.Spinbox(self.single, from_=1, to=30, increment=1, textvariable=self.point_size, width=6).grid(
            row=1, column=1, sticky="w")

        g = self.grad
        ttk.Label(g, text="Pole (atrybut)").grid(row=0, column=0, sticky="w")
        self.field = tk.StringVar()
        self.field_box = ttk.Combobox(g, textvariable=self.field, state="readonly", width=28)
        self.field_box.grid(row=0, column=1, columnspan=3, sticky="w", pady=3)
        self.field_box.bind("<<ComboboxSelected>>", lambda e: self._data_range())
        ttk.Label(g, text="Wartość min").grid(row=1, column=0, sticky="w")
        self.vmin = tk.StringVar()
        ttk.Entry(g, textvariable=self.vmin, width=12).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(g, text="max").grid(row=1, column=2, sticky="e", padx=4)
        self.vmax = tk.StringVar()
        ttk.Entry(g, textvariable=self.vmax, width=12).grid(row=1, column=3, sticky="w")
        ttk.Button(g, text="Z danych", command=self._data_range, style="Small.TButton").grid(row=1, column=4, padx=6)
        self.range_info = ttk.Label(g, style="Muted.TLabel")
        self.range_info.grid(row=2, column=0, columnspan=5, sticky="w")
        ttk.Label(g, text="Liczba klas").grid(row=3, column=0, sticky="w")
        self.classes = tk.IntVar()
        ttk.Spinbox(g, from_=2, to=10, textvariable=self.classes, width=5, command=self._preview).grid(
            row=3, column=1, sticky="w", pady=3)
        ttk.Label(g, text="Gotowy schemat").grid(row=4, column=0, sticky="w")
        self.ramp_name = tk.StringVar(value="(własny)")
        ramp_box = ttk.Combobox(g, textvariable=self.ramp_name, values=["(własny)"] + list(RAMPS), state="readonly",
                                width=32)
        ramp_box.grid(row=4, column=1, columnspan=4, sticky="w", pady=3)
        ramp_box.bind("<<ComboboxSelected>>", self._ramp_selected)
        ttk.Label(g, text="Kolory").grid(row=5, column=0, sticky="w")
        self.color_frame = ttk.Frame(g)
        self.color_frame.grid(row=5, column=1, columnspan=4, sticky="w", pady=3)
        self.ramp_colors: list[str] = []
        self.preview = tk.Canvas(g, height=28, width=440, highlightthickness=0, bg=self.theme["bg"])
        self.preview.grid(row=6, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.outline_btn = self._color_row(common, 0, "Kolor obrysu", "outline")
        ttk.Label(common, text="Grubość").grid(row=0, column=2, sticky="w", padx=(16, 4))
        self.width = tk.DoubleVar()
        ttk.Spinbox(common, from_=0, to=10, increment=0.2, textvariable=self.width, width=6).grid(row=0, column=3)
        self.single.pack(fill="x", pady=4)
        self.grad.pack(fill="x", pady=4)
        common.pack(fill="x", pady=4)
        for var in (self.vmin, self.vmax):
            var.trace_add("write", lambda *a: self._preview())
        self.classes.trace_add("write", lambda *a: self._preview())
        self.add_button("Anuluj", self.close)
        self.add_button("OK", self.ok, accent=True)
        self.add_button("Zastosuj", self.apply)
        self._load(self.layer)

    def _color_row(self, parent, row, label, key):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        btn = tk.Button(parent, width=6, relief="solid", bd=1, command=lambda: self._pick(key))
        btn.grid(row=row, column=1, sticky="w", padx=6)
        return btn

    def _pick(self, key) -> None:
        current = self.vars.get(key, "#888888")
        color = colorchooser.askcolor(current, parent=self, title="Wybierz kolor")[1]
        if color:
            self.vars[key] = color
            (self.fill_btn if key == "fill" else self.outline_btn).configure(bg=color, activebackground=color)
            self._preview()

    def _load(self, layer) -> None:
        self.layer = layer
        st = layer.style
        self.mode.set(st.mode)
        self.vars = {"fill": st.fill, "outline": st.outline}
        self.fill_btn.configure(bg=st.fill, activebackground=st.fill)
        self.outline_btn.configure(bg=st.outline, activebackground=st.outline)
        self.no_fill.set(st.no_fill)
        self.point_size.set(st.point_size)
        self.width.set(st.width)
        numeric = layer.numeric_fields()
        self.field_box.configure(values=numeric)
        self.field.set(st.field if st.field in numeric else (numeric[0] if numeric else ""))
        self.vmin.set(fmt_number(st.vmin) if st.field == self.field.get() else "")
        self.vmax.set(fmt_number(st.vmax) if st.field == self.field.get() else "")
        self.classes.set(st.classes)
        self.ramp_colors = list(st.colors)
        self.ramp_name.set(next((k for k, v in RAMPS.items() if v == st.colors), "(własny)"))
        self._color_buttons()
        if not self.vmin.get():
            self._data_range()
        self._mode_changed()

    def _color_buttons(self) -> None:
        for child in self.color_frame.winfo_children():
            child.destroy()
        for i, color in enumerate(self.ramp_colors):
            tk.Button(self.color_frame, bg=color, activebackground=color, width=4, relief="solid", bd=1,
                      command=lambda i=i: self._pick_ramp(i)).pack(side="left", padx=2)
        ttk.Button(self.color_frame, text="+", width=2, style="Small.TButton",
                   command=self._add_color).pack(side="left", padx=(8, 1))
        ttk.Button(self.color_frame, text="−", width=2, style="Small.TButton",
                   command=self._remove_color).pack(side="left", padx=1)
        ttk.Button(self.color_frame, text="⇄ odwróć", style="Small.TButton",
                   command=self._reverse).pack(side="left", padx=6)
        self._preview()

    def _pick_ramp(self, index) -> None:
        color = colorchooser.askcolor(self.ramp_colors[index], parent=self, title="Kolor schematu")[1]
        if color:
            self.ramp_colors[index] = color
            self.ramp_name.set("(własny)")
            self._color_buttons()

    def _add_color(self) -> None:
        if len(self.ramp_colors) < 6:
            self.ramp_colors.append(self.ramp_colors[-1] if self.ramp_colors else "#ffffff")
            self._color_buttons()

    def _remove_color(self) -> None:
        if len(self.ramp_colors) > 2:
            self.ramp_colors.pop()
            self._color_buttons()

    def _reverse(self) -> None:
        self.ramp_colors.reverse()
        self._color_buttons()

    def _ramp_selected(self, _e=None) -> None:
        if self.ramp_name.get() in RAMPS:
            self.ramp_colors = list(RAMPS[self.ramp_name.get()])
            self._color_buttons()

    def _data_range(self) -> None:
        rng = self.layer.value_range(self.field.get()) if self.field.get() else None
        if rng:
            self.vmin.set(fmt_number(rng[0]).replace(" ", ""))
            self.vmax.set(fmt_number(rng[1]).replace(" ", ""))
            self.range_info.configure(text=f"Zakres w danych: {fmt_number(rng[0])} – {fmt_number(rng[1])}")
        else:
            self.range_info.configure(text="Brak pól liczbowych w tej warstwie." if not self.field.get() else "")
        self._preview()

    def _mode_changed(self) -> None:
        graduated = self.mode.get() == "graduated"
        for child in self.grad.winfo_children():
            try:
                child.configure(state="normal" if graduated else "disabled")
            except tk.TclError:
                pass
        self.field_box.configure(state="readonly" if graduated else "disabled")
        self._preview()

    def _number(self, var, default):
        try:
            return float(var.get().replace(" ", "").replace(",", "."))
        except ValueError:
            return default

    def _build_style(self) -> Style | None:
        st = self.layer.style.copy()
        st.mode = self.mode.get()
        st.fill = self.vars.get("fill", st.fill)
        st.outline = self.vars.get("outline", st.outline)
        st.no_fill = self.no_fill.get()
        try:
            st.point_size = float(self.point_size.get())
            st.width = float(self.width.get())
            classes = int(self.classes.get())
        except (tk.TclError, ValueError):
            return None
        if st.mode == "graduated":
            if not self.field.get():
                st.mode = "single"
            else:
                if st.field != self.field.get() or classes != st.classes:
                    st.labels = [] if classes != 5 or self.field.get() not in ("iop_norm", "iop_analiza") else \
                        st.labels
                st.field = self.field.get()
                st.vmin = self._number(self.vmin, 0.0)
                st.vmax = self._number(self.vmax, 1.0)
                st.classes = max(2, min(10, classes))
                st.colors = list(self.ramp_colors) or list(IOP_RAMP)
        return st

    def _preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        self.preview.delete("all")
        st = self._build_style()
        if not st or st.mode != "graduated":
            self.preview.create_text(4, 14, anchor="w", text="Podgląd schematu – tryb stopniowany",
                                     fill=self.theme["muted"])
            return
        width = 430 / st.classes
        for i in range(st.classes):
            self.preview.create_rectangle(4 + i * width, 2, 4 + (i + 1) * width, 18, fill=st.class_color(i),
                                          outline=self.theme["border"])
        self.preview.create_text(4, 24, anchor="w", text=fmt_number(st.vmin), fill=self.theme["fg"],
                                 font=("Segoe UI", 7))
        self.preview.create_text(434, 24, anchor="e", text=fmt_number(st.vmax), fill=self.theme["fg"],
                                 font=("Segoe UI", 7))

    def apply(self) -> bool:
        st = self._build_style()
        if not st:
            return False
        if st.mode == "graduated" and st.vmax <= st.vmin:
            self.range_info.configure(text="Wartość max musi być większa od min.")
            return False
        self.layer.style = st
        self.app.refresh_layers()
        self.app.map.render()
        return True

    def ok(self) -> None:
        if self.apply():
            self.close()


# ====================================================================== wyszukiwanie
class SearchDialog(Modal):
    OPS = ["zawiera", "=", "≠", ">", "≥", "<", "≤", "zaczyna się od"]
    LIMIT = 3000

    def __init__(self, app) -> None:
        super().__init__(app, "Wyszukiwanie w atrybutach warstwy", app.theme, 900, 600, resizable=True)
        self.app = app
        self.results = []
        if not app.layers:
            ttk.Label(self.body, text="Brak warstw na mapie.").pack(anchor="w")
            self.add_button("Zamknij", self.close, accent=True)
            return
        b = self.body
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Label(row, text="Warstwa").pack(side="left")
        self.picker = LayerPicker(row, app.layers, app.active, self._layer_changed, width=36)
        self.picker.pack(side="left", padx=6)
        q = ttk.Frame(b)
        q.pack(fill="x", pady=8)
        ttk.Label(q, text="Pole").pack(side="left")
        self.field = tk.StringVar()
        self.field_box = ttk.Combobox(q, textvariable=self.field, state="readonly", width=24)
        self.field_box.pack(side="left", padx=6)
        self.op = tk.StringVar(value="zawiera")
        ttk.Combobox(q, textvariable=self.op, values=self.OPS, state="readonly", width=13).pack(side="left")
        self.value = tk.StringVar()
        entry = ttk.Entry(q, textvariable=self.value, width=26)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.search())
        ttk.Button(q, text="Szukaj", style="Accent.TButton", command=self.search).pack(side="left")
        self.count = tk.StringVar(value="Wpisz wartość i kliknij Szukaj (puste pole = wszystkie obiekty).")
        ttk.Label(b, textvariable=self.count, style="Muted.TLabel").pack(anchor="w")
        frame = ttk.Frame(b)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        self.tree = ttk.Treeview(frame, show="headings", selectmode="extended")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.zoom_selected())
        self.add_button("Zamknij", self.close)
        self.add_button("Przybliż do zaznaczonych", self.zoom_selected, accent=True)
        self.add_button("Zaznacz wszystkie wyniki na mapie", self.select_all)
        entry.focus_set()
        self._layer_changed(self.picker.layer)

    def _layer_changed(self, layer) -> None:
        self.layer = layer
        self.field_box.configure(values=["(wszystkie pola)"] + layer.fields)
        self.field.set("(wszystkie pola)")
        columns = ["fid"] + layer.fields[:60]
        self.tree.configure(columns=columns)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=70 if col == "fid" else 120, anchor="w", stretch=False)
        self.tree.delete(*self.tree.get_children())
        self.results = []
        self.count.set(f"{len(layer.features)} obiektów, {len(layer.fields)} pól.")

    @staticmethod
    def _match(value, op, needle, number) -> bool:
        if value is None:
            return False
        if op in (">", "≥", "<", "≤") or (op in ("=", "≠") and number is not None
                                         and isinstance(value, (int, float))):
            try:
                v = float(value)
            except (TypeError, ValueError):
                return False
            if number is None:
                return False
            return {">": v > number, "≥": v >= number, "<": v < number, "≤": v <= number,
                    "=": abs(v - number) < 1e-9, "≠": abs(v - number) >= 1e-9}[op]
        text = str(value).lower()
        if op == "zawiera":
            return needle in text
        if op == "zaczyna się od":
            return text.startswith(needle)
        if op == "=":
            return text == needle
        return text != needle

    def search(self) -> None:
        layer = self.layer
        needle = self.value.get().strip().lower()
        op = self.op.get()
        try:
            number = float(needle.replace(",", ".")) if needle else None
        except ValueError:
            number = None
        fields = layer.fields if self.field.get() == "(wszystkie pola)" else [self.field.get()]
        if not needle:
            self.results = list(layer.features)
        else:
            self.results = [f for f in layer.features
                            if any(self._match(f.attrs.get(k), op, needle, number) for k in fields)]
        self.tree.delete(*self.tree.get_children())
        cols = self.tree["columns"]
        for feat in self.results[:self.LIMIT]:
            values = [feat.fid] + [self._fmt(feat.attrs.get(c)) for c in cols[1:]]
            self.tree.insert("", "end", iid=str(feat.fid), values=values)
        extra = f" (pokazano {self.LIMIT})" if len(self.results) > self.LIMIT else ""
        self.count.set(f"Znaleziono {len(self.results)} z {len(layer.features)} obiektów{extra}. "
                       "Dwuklik = przybliż do obiektu.")

    @staticmethod
    def _fmt(value):
        if value is None:
            return ""
        return f"{value:.6g}" if isinstance(value, float) else str(value)

    def _selected(self):
        ids = {int(i) for i in self.tree.selection()}
        return [f for f in self.layer.features if f.fid in ids]

    def zoom_selected(self) -> None:
        feats = self._selected()
        if feats:
            self.app.set_selection(self.layer, feats, zoom=True)

    def select_all(self) -> None:
        if self.results:
            self.app.set_selection(self.layer, self.results, zoom=True)


# ====================================================================== zoom do obiektu
class ZoomDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Zoom do obiektu", app.theme, 560, 520, resizable=True)
        self.app = app
        if not app.layers:
            ttk.Label(self.body, text="Brak warstw na mapie.").pack(anchor="w")
            self.add_button("Zamknij", self.close, accent=True)
            return
        b = self.body
        sel_layer, sel_feats = app.selection
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Label(row, text="Warstwa").pack(side="left")
        self.picker = LayerPicker(row, app.layers, sel_layer or app.active, self._fill, width=40)
        self.picker.pack(side="left", padx=6)
        self.filter = tk.StringVar()
        frow = ttk.Frame(b)
        frow.pack(fill="x", pady=6)
        ttk.Label(frow, text="Filtr").pack(side="left")
        ttk.Entry(frow, textvariable=self.filter, width=30).pack(side="left", padx=6)
        self.filter.trace_add("write", lambda *a: self._fill(self.picker.layer))
        self.tree = ttk.Treeview(b, columns=("fid", "opis"), show="headings", height=14)
        self.tree.heading("fid", text="ID")
        self.tree.heading("opis", text="Obiekt")
        self.tree.column("fid", width=60, anchor="e")
        self.tree.column("opis", width=420)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.zoom_feature())
        self.add_button("Zamknij", self.close)
        self.add_button("Przybliż do obiektu", self.zoom_feature, accent=True)
        self.add_button("Cała warstwa", self.zoom_layer)
        self.add_button("Wszystkie warstwy", self.zoom_all, side="left")
        self._fill(self.picker.layer)
        if sel_feats and sel_layer is self.picker.layer and self.tree.exists(str(sel_feats[0].fid)):
            self.tree.selection_set(str(sel_feats[0].fid))
            self.tree.see(str(sel_feats[0].fid))

    def _fill(self, layer) -> None:
        self.tree.delete(*self.tree.get_children())
        text = self.filter.get().strip().lower()
        shown = 0
        for feat in layer.features:
            label = layer.label_for(feat)
            if text and text not in label.lower() and text != str(feat.fid):
                continue
            self.tree.insert("", "end", iid=str(feat.fid), values=(feat.fid, label))
            shown += 1
            if shown >= 5000:
                break

    def zoom_feature(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        layer = self.picker.layer
        ids = {int(i) for i in sel}
        feats = [f for f in layer.features if f.fid in ids]
        self.close()
        self.app.set_selection(layer, feats, zoom=True)

    def zoom_layer(self) -> None:
        layer = self.picker.layer
        self.close()
        self.app.map.zoom_to_bbox(layer.bbox)

    def zoom_all(self) -> None:
        boxes = [l.bbox for l in self.app.layers if l.bbox and l.visible]
        self.close()
        if boxes:
            self.app.map.zoom_to_bbox((min(b[0] for b in boxes), min(b[1] for b in boxes),
                                       max(b[2] for b in boxes), max(b[3] for b in boxes)))


# ====================================================================== pomiar
class MeasureDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Pomiar odległości", app.theme)
        self.app = app
        ttk.Label(self.body, text="Pomiar odległości między dwoma punktami", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.body, justify="left", wraplength=420, text=(
            "Po kliknięciu „Rozpocznij” wskaż na mapie pierwszy, a potem drugi punkt.\n"
            "Wynik w metrach: odległość geodezyjna na elipsoidzie GRS80 (Vincenty) oraz odległość "
            "płaska w układzie EPSG:2180.\nEsc przerywa pomiar.")).pack(anchor="w", pady=(6, 0))
        self.add_button("Anuluj", self.close)
        self.add_button("Rozpocznij", self.start, accent=True)
        self.bind("<Return>", lambda e: self.start())

    def start(self) -> None:
        self.close()
        self.app.start_measure()


class MeasureResultDialog(Modal):
    def __init__(self, app, p1, p2) -> None:
        super().__init__(app, "Wynik pomiaru", app.theme)
        self.app = app
        d = distance_2180(p1, p2)
        crs = app.coord_crs.get()
        b = self.body
        ttk.Label(b, text=f"{d['geodezyjna']:,.2f} m".replace(",", " "), style="Title.TLabel").pack(anchor="w")
        ttk.Label(b, text=f"= {nice_distance(d['geodezyjna'])} (geodezyjnie, elipsoida GRS80)",
                  style="Muted.TLabel").pack(anchor="w")
        grid = ttk.Frame(b)
        grid.pack(anchor="w", pady=(10, 0))
        rows = [("Odległość płaska EPSG:2180", f"{d['plaska_2180']:,.2f} m".replace(",", " ")),
                ("Różnica (zniekształcenie)", f"{d['plaska_2180'] - d['geodezyjna']:+.2f} m"),
                (f"Punkt 1 ({crs})", format_coords(crs, *p1)),
                (f"Punkt 2 ({crs})", format_coords(crs, *p2))]
        for i, (k, v) in enumerate(rows):
            ttk.Label(grid, text=k).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(grid, text=v, style="H.TLabel").grid(row=i, column=1, sticky="w", padx=12)
        self.add_button("Zamknij", self.finish, accent=True)
        self.add_button("Nowy pomiar", self.again)
        self.bind("<Return>", lambda e: self.finish())

    def again(self) -> None:
        self.close()
        self.app.start_measure()

    def finish(self) -> None:
        self.close()


# ====================================================================== warstwa punktowa
class PointLayerDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Warstwa punktowa – dodawanie punktów", app.theme, 500, 330)
        self.app = app
        editable = [l for l in app.layers if l.editable]
        self.editable = editable
        self.choice = tk.StringVar(value="existing" if editable else "new")
        self.name = tk.StringVar(value=f"punkty_{dt.datetime.now():%Y%m%d_%H%M}")
        self.ask = tk.BooleanVar(value=app.ask_point_name)
        b = self.body
        ttk.Radiobutton(b, text="Nowa warstwa punktowa", variable=self.choice, value="new").pack(anchor="w")
        row = ttk.Frame(b)
        row.pack(fill="x", padx=22, pady=(2, 8))
        ttk.Label(row, text="Nazwa").pack(side="left")
        ttk.Entry(row, textvariable=self.name, width=34).pack(side="left", padx=6)
        rb = ttk.Radiobutton(b, text="Dodawaj do istniejącej warstwy edytowalnej", variable=self.choice,
                             value="existing")
        rb.pack(anchor="w")
        self.picker = LayerPicker(b, editable, app.edit_layer or (editable[0] if editable else None), width=36)
        self.picker.pack(anchor="w", padx=22, pady=(2, 8))
        if not editable:
            rb.state(["disabled"])
            self.picker.state(["disabled"])
        ttk.Checkbutton(b, text="Pytaj o nazwę każdego punktu", variable=self.ask).pack(anchor="w", pady=4)
        ttk.Label(b, text="Klik na mapie dodaje punkt • Ctrl+Z cofa ostatni • Esc kończy dodawanie.\n"
                          "Zapis: przycisk „Zapisz SHP”.", style="Muted.TLabel", justify="left").pack(anchor="w")
        self.add_button("Anuluj", self.close)
        self.add_button("Rozpocznij dodawanie", self.start, accent=True)
        if app.edit_layer:
            self.add_button("Zakończ dodawanie", self.stop, side="left")

    def start(self) -> None:
        self.app.ask_point_name = self.ask.get()
        if self.choice.get() == "existing" and self.picker.layer:
            layer = self.picker.layer
        else:
            name = self.name.get().strip() or "punkty"
            layer = new_point_layer(name)
            self.app.add_layer(layer, zoom=False)
        self.close()
        self.app.start_adding(layer)

    def stop(self) -> None:
        self.close()
        self.app.end_mode()


class PointNameDialog(Modal):
    def __init__(self, app, number, world) -> None:
        super().__init__(app, "Nowy punkt", app.theme)
        self.name = tk.StringVar(value=f"Punkt {number}")
        b = self.body
        ttk.Label(b, text=f"Punkt nr {number}", style="H.TLabel").pack(anchor="w")
        ttk.Label(b, text=format_coords("EPSG:2180", *world) + "\n" + format_coords("EPSG:4326", *world),
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Label(row, text="Nazwa").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.name, width=34)
        entry.pack(side="left", padx=6)
        entry.select_range(0, "end")
        entry.focus_set()
        self.add_button("Anuluj", self.close)
        self.add_button("Dodaj", lambda: self.close(self.name.get().strip() or f"Punkt {number}"), accent=True)
        self.bind("<Return>", lambda e: self.close(self.name.get().strip() or f"Punkt {number}"))
        self.after(30, entry.focus_set)


class SaveShpDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Zapisz warstwę punktową jako SHP", app.theme, 620, 280)
        self.app = app
        points = sorted([l for l in app.layers if l.kind == "point"], key=lambda l: not l.editable)
        b = self.body
        if not points:
            ttk.Label(b, text="Brak warstwy punktowej.\nUtwórz ją przyciskiem „Warstwa punktowa”.",
                      justify="left").pack(anchor="w")
            self.add_button("Zamknij", self.close, accent=True)
            return
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Label(row, text="Warstwa").pack(side="left")
        self.picker = LayerPicker(row, points, app.edit_layer or points[0], self._suggest, width=40)
        self.picker.pack(side="left", padx=6)
        self.path = tk.StringVar()
        prow = ttk.Frame(b)
        prow.pack(fill="x", pady=10)
        ttk.Label(prow, text="Plik .shp").pack(side="left")
        ttk.Entry(prow, textvariable=self.path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(prow, text="Przeglądaj…", command=self.browse).pack(side="left")
        self.info = tk.StringVar(value="Zapis: .shp, .shx, .dbf (UTF-8 + .cpg), .prj w EPSG:2180.")
        ttk.Label(b, textvariable=self.info, style="Muted.TLabel", wraplength=580, justify="left").pack(anchor="w")
        self.add_button("Anuluj", self.close)
        self.add_button("Zapisz", self.save, accent=True)
        self._suggest(self.picker.layer)

    def _suggest(self, layer) -> None:
        folder = ROOT / "data" / "wyniki"
        self.path.set(str(folder / f"{layer.name}.shp"))
        self.info.set(f"{len(layer.features)} punktów • pola: id, nazwa, x_2180, y_2180, lon, lat • "
                      "zapis .shp/.shx/.dbf/.cpg/.prj (EPSG:2180).")

    def browse(self) -> None:
        current = Path(self.path.get())
        path = filedialog.asksaveasfilename(parent=self, title="Zapisz jako SHP", defaultextension=".shp",
                                            initialdir=str(current.parent if current.parent.exists() else ROOT),
                                            initialfile=current.name, filetypes=[("ESRI Shapefile", "*.shp")])
        if path:
            self.path.set(path)

    def save(self) -> None:
        layer = self.picker.layer
        path = Path(self.path.get().strip())
        if not layer.features:
            self.info.set("Warstwa nie ma punktów – nie ma czego zapisać.")
            return
        if path.with_suffix(".shp").exists() and not confirm(self.app, "Nadpisać?",
                                                              f"Plik {path.name} istnieje. Nadpisać?"):
            return
        try:
            files = save_point_layer(layer, path)
        except Exception as exc:  # noqa: BLE001
            self.info.set(f"Błąd zapisu: {exc}")
            return
        layer.meta["saved"] = True
        layer.source = str(path)
        self.close()
        self.app.message.set(f"Zapisano {path.name} ({len(layer.features)} pkt)")
        info_modal(self.app, self.app.theme, "Zapisano SHP",
                   f"Warstwa „{layer.name}” ({len(layer.features)} punktów) zapisana:\n" +
                   "\n".join(str(f) for f in files))


# ====================================================================== motyw
class ThemeDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Tryb jasny / ciemny", app.theme, 380, 230)
        self.app = app
        self.choice = tk.StringVar(value=app.theme_name)
        b = self.body
        ttk.Label(b, text="Wygląd aplikacji", style="H.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Radiobutton(b, text="Jasny", variable=self.choice, value="jasny").pack(anchor="w", pady=2)
        ttk.Radiobutton(b, text="Ciemny", variable=self.choice, value="ciemny").pack(anchor="w", pady=2)
        ttk.Label(b, text="Dotyczy okien, panelu warstw i tła mapy.", style="Muted.TLabel").pack(anchor="w", pady=6)
        self.add_button("Anuluj", self.close)
        self.add_button("Zastosuj", self.apply, accent=True)

    def apply(self) -> None:
        name = self.choice.get()
        self.close()
        if name != self.app.theme_name:
            self.app.set_theme(name)


# ====================================================================== eksport PDF
class PdfDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Eksport mapy do PDF", app.theme, 600, 360)
        self.app = app
        active = app.active.name if app.active else "mapa"
        self.title_var = tk.StringVar(value=f"Mapa – {active}")
        self.subtitle = tk.StringVar(value="Powiat kutnowski • PSIP")
        self.paper = tk.StringVar(value="A4")
        self.orient = tk.StringVar(value="pozioma")
        self.legend = tk.BooleanVar(value=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        self.path = tk.StringVar(value=str(ROOT / "app" / "output" / f"mapa_{stamp}.pdf"))
        b = self.body
        grid = ttk.Frame(b)
        grid.pack(fill="x")
        ttk.Label(grid, text="Tytuł").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.title_var, width=50).grid(row=0, column=1, columnspan=3, sticky="we")
        ttk.Label(grid, text="Podtytuł").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.subtitle, width=50).grid(row=1, column=1, columnspan=3, sticky="we")
        ttk.Label(grid, text="Format").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(grid, textvariable=self.paper, values=["A4", "A3"], state="readonly", width=6).grid(
            row=2, column=1, sticky="w")
        orient = ttk.Frame(grid)
        orient.grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Radiobutton(orient, text="pozioma", variable=self.orient, value="pozioma").pack(side="left")
        ttk.Radiobutton(orient, text="pionowa", variable=self.orient, value="pionowa").pack(side="left", padx=8)
        ttk.Checkbutton(grid, text="Dołącz legendę warstw", variable=self.legend).grid(
            row=3, column=1, columnspan=3, sticky="w", pady=3)
        ttk.Label(grid, text="Plik").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(grid, textvariable=self.path, width=50).grid(row=4, column=1, columnspan=2, sticky="we")
        ttk.Button(grid, text="Przeglądaj…", command=self.browse).grid(row=4, column=3, padx=4)
        grid.columnconfigure(1, weight=1)
        self.info = tk.StringVar(value="Mapa jest eksportowana wektorowo w aktualnym widoku, z podziałką i skalą.")
        ttk.Label(b, textvariable=self.info, style="Muted.TLabel", wraplength=560).pack(anchor="w", pady=(10, 0))
        self.add_button("Anuluj", self.close)
        self.add_button("Eksportuj", self.export, accent=True)

    def browse(self) -> None:
        current = Path(self.path.get())
        path = filedialog.asksaveasfilename(parent=self, title="Zapisz PDF", defaultextension=".pdf",
                                            initialdir=str(current.parent if current.parent.exists() else ROOT),
                                            initialfile=current.name, filetypes=[("PDF", "*.pdf")])
        if path:
            self.path.set(path)

    def export(self) -> None:
        app = self.app
        path = Path(self.path.get().strip())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.info.set(f"Nie można utworzyć folderu: {exc}")
            return
        legend = [((l.name if l.style.mode != "graduated" else f"{l.name} – {l.style.field}"), l.legend())
                  for l in app.layers if l.visible]
        footer = ["Układ EPSG:2180 (PUWG 1992)", f"Skala ekranu 1:{app.map.scale_denominator():,.0f}"
                  .replace(",", " "), f"{dt.datetime.now():%Y-%m-%d %H:%M}", f"Autor: {app.user}"]
        try:
            scale = export_map(path, app.map, title=self.title_var.get(), subtitle=self.subtitle.get(),
                               legend=legend, footer=footer, paper=self.paper.get(),
                               landscape=self.orient.get() == "pozioma", include_legend=self.legend.get(),
                               ground_m_per_px=app.map.m_per_px())
        except Exception as exc:  # noqa: BLE001
            self.info.set(f"Błąd eksportu: {exc}")
            return
        self.close()
        app.message.set(f"Zapisano PDF: {path.name}")
        if hasattr(os, "startfile"):
            if confirm(app, "Eksport PDF", f"Zapisano {path}\n{scale}\n\nOtworzyć plik?"):
                os.startfile(path)  # noqa: S606 - Windows: otworz w domyslnej przegladarce PDF
        else:
            info_modal(app, app.theme, "Eksport PDF", f"Zapisano {path}\n{scale}")


# ====================================================================== wylogowanie
class LogoutDialog(Modal):
    def __init__(self, app) -> None:
        super().__init__(app, "Wylogowanie", app.theme)
        self.app = app
        unsaved = [l.name for l in app.layers if l.editable and l.features and not l.meta.get("saved")]
        text = f"Wylogować użytkownika „{app.user}”?\nWarstwy na mapie zostaną zamknięte."
        if unsaved:
            text += "\n\nUwaga – niezapisane warstwy punktowe: " + ", ".join(unsaved)
        ttk.Label(self.body, text=text, justify="left", wraplength=420).pack(anchor="w")
        self.add_button("Anuluj", self.close)
        self.add_button("Wyloguj", self.logout, accent=True)

    def logout(self) -> None:
        self.close()
        self.app.root_app.logout()

