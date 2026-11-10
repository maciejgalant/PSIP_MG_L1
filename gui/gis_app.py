"""PSIP GIS – desktopowa aplikacja mapowa w Tkinter (tylko biblioteka standardowa).

Uruchom z katalogu repozytorium:
    py gui/gis_app.py

Funkcje: logowanie, interaktywna mapa (EPSG:2180), hierarchia warstw z legenda,
wczytywanie SHP, warstwy H3/IOP z PostGIS, analiza IOP z suwakami wag, edycja stylu,
wyszukiwanie po atrybutach, pomiar odleglosci, warstwa punktowa z zapisem do SHP,
zoom do obiektu, tryb jasny/ciemny, eksport do PDF, wspolrzedne kursora (4326/3857), skala.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dialogs  # noqa: E402
from auth import LoginWindow  # noqa: E402
from crs import DISPLAY_CRS, PUWG1992, format_coords  # noqa: E402
from layers import Feature, Layer  # noqa: E402
from map_canvas import MapCanvas  # noqa: E402
from ui_common import THEMES, apply_theme  # noqa: E402

SCALES = ["1:2 000", "1:5 000", "1:10 000", "1:25 000", "1:50 000", "1:100 000", "1:250 000", "1:500 000"]


class GisWindow(tk.Toplevel):
    def __init__(self, root: "GisApp", user: str) -> None:
        super().__init__(root.tk_root)
        self.root_app = root
        self.user = user
        self.theme_name = root.theme_name
        self.theme = THEMES[self.theme_name]
        self.layers: list[Layer] = []
        self.active: Layer | None = None
        self.selection: tuple[Layer | None, list[Feature]] = (None, [])
        self.edit_layer: Layer | None = None
        self.ask_point_name = True
        self.title(f"PSIP GIS | {user}")
        self.geometry("1440x880")
        self.minsize(1080, 640)
        self.protocol("WM_DELETE_WINDOW", self.root_app.quit)
        self.coord_crs = tk.StringVar(value="EPSG:4326")
        self.coord_text = tk.StringVar(value="—")
        self.scale_text = tk.StringVar(value="")
        self.mode_text = tk.StringVar(value="Tryb: przeglądanie")
        self.message = tk.StringVar(value="Gotowe")
        self._build()
        self.bind("<Escape>", lambda e: self.end_mode())
        self.bind("<Control-z>", lambda e: self.undo_point())
        self.after(50, self.map.render)

    # ================================================================ interfejs
    def _build(self) -> None:
        self.configure(bg=self.theme["bg"])
        toolbar = ttk.Frame(self, padding=(8, 6, 8, 4))
        toolbar.pack(fill="x")
        self.tool_buttons: dict[str, ttk.Button] = {}
        tools = [
            ("shp", "Wczytaj SHP", lambda: dialogs.LoadShpDialog(self).show()),
            ("h3", "H3 z PostGIS", lambda: dialogs.H3Dialog(self).show()),
            ("|", None, None),
            ("weights", "Analiza i wagi", lambda: dialogs.AnalysisDialog(self).show()),
            ("style", "Styl warstwy", lambda: dialogs.StyleDialog(self).show()),
            ("search", "Szukaj", lambda: dialogs.SearchDialog(self).show()),
            ("zoom", "Zoom do obiektu", lambda: dialogs.ZoomDialog(self).show()),
            ("|", None, None),
            ("measure", "Pomiar odległości", lambda: dialogs.MeasureDialog(self).show()),
            ("points", "Warstwa punktowa", lambda: dialogs.PointLayerDialog(self).show()),
            ("save", "Zapisz SHP", lambda: dialogs.SaveShpDialog(self).show()),
            ("|", None, None),
            ("theme", "Jasny / ciemny", lambda: dialogs.ThemeDialog(self).show()),
            ("pdf", "Eksport PDF", lambda: dialogs.PdfDialog(self).show()),
        ]
        for key, text, command in tools:
            if key == "|":
                ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=6, pady=2)
                continue
            button = ttk.Button(toolbar, text=text, command=command, style="Tool.TButton")
            button.pack(side="left", padx=2)
            self.tool_buttons[key] = button
        ttk.Button(toolbar, text="Wyloguj", command=lambda: dialogs.LogoutDialog(self).show(),
                   style="Tool.TButton").pack(side="right", padx=2)
        ttk.Label(toolbar, text=f"Zalogowano: {self.user}", style="Muted.TLabel").pack(side="right", padx=10)

        status = ttk.Frame(self, style="Panel.TFrame")
        status.pack(fill="x", side="bottom")
        ttk.Label(status, textvariable=self.mode_text, style="Status.TLabel", width=34).pack(side="left")
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y")
        ttk.Label(status, text="Kursor", style="Status.TLabel").pack(side="left")
        box = ttk.Combobox(status, textvariable=self.coord_crs, values=list(DISPLAY_CRS), width=11,
                           state="readonly")
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self.on_cursor(self.map.cursor_world))
        ttk.Label(status, textvariable=self.coord_text, style="Status.TLabel", width=44).pack(side="left")
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y")
        ttk.Label(status, text="Skala", style="Status.TLabel").pack(side="left")
        self.scale_box = ttk.Combobox(status, textvariable=self.scale_text, values=SCALES, width=14)
        self.scale_box.pack(side="left", padx=(0, 6))
        self.scale_box.bind("<<ComboboxSelected>>", self._set_scale)
        self.scale_box.bind("<Return>", self._set_scale)
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y")
        ttk.Label(status, textvariable=self.message, style="Status.TLabel").pack(side="left", fill="x", expand=True)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        # --- panel warstw (hierarchia + legenda)
        left = ttk.Frame(body, padding=(0, 0, 6, 0))
        body.add(left, weight=0)
        head = ttk.Frame(left)
        head.pack(fill="x")
        ttk.Label(head, text="Warstwy", style="H.TLabel").pack(side="left")
        ttk.Label(head, text="(góra listy = na wierzchu)", style="Muted.TLabel").pack(side="left", padx=6)
        holder = ttk.Frame(left)
        holder.pack(fill="both", expand=True, pady=(4, 0))
        self.layer_canvas = tk.Canvas(holder, width=300, highlightthickness=1)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.layer_canvas.yview)
        self.layer_frame = ttk.Frame(self.layer_canvas)
        self.layer_frame.bind("<Configure>", lambda e: self.layer_canvas.configure(
            scrollregion=self.layer_canvas.bbox("all")))
        self._layer_window = self.layer_canvas.create_window((0, 0), window=self.layer_frame, anchor="nw")
        self.layer_canvas.bind("<Configure>", lambda e: self.layer_canvas.itemconfigure(
            self._layer_window, width=e.width))
        self.layer_canvas.configure(yscrollcommand=scroll.set)
        self.layer_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.layer_canvas.bind("<Enter>", lambda e: self.layer_canvas.bind_all("<MouseWheel>", self._layer_wheel))
        self.layer_canvas.bind("<Leave>", lambda e: self.layer_canvas.unbind_all("<MouseWheel>"))

        # --- mapa
        center = ttk.Frame(body)
        body.add(center, weight=1)
        self.map = MapCanvas(center, self)
        self.map.pack(fill="both", expand=True)

        # --- informacje o obiekcie
        right = ttk.Frame(body, padding=(6, 0, 0, 0))
        body.add(right, weight=0)
        ttk.Label(right, text="Informacje o obiekcie", style="H.TLabel").pack(anchor="w")
        self.info_title = ttk.Label(right, text="Kliknij obiekt na mapie.", style="Muted.TLabel", wraplength=280)
        self.info_title.pack(anchor="w", pady=(2, 4))
        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True)
        self.info_tree = ttk.Treeview(tree_frame, columns=("pole", "wartosc"), show="headings", height=20)
        self.info_tree.heading("pole", text="Atrybut")
        self.info_tree.heading("wartosc", text="Wartość")
        self.info_tree.column("pole", width=120, anchor="w")
        self.info_tree.column("wartosc", width=160, anchor="w")
        info_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.info_tree.yview)
        self.info_tree.configure(yscrollcommand=info_scroll.set)
        self.info_tree.pack(side="left", fill="both", expand=True)
        info_scroll.pack(side="right", fill="y")
        self.refresh_layers()

    def _layer_wheel(self, event) -> None:
        self.layer_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ================================================================ warstwy
    def add_layer(self, layer: Layer, zoom: bool = True) -> None:
        self.layers.insert(0, layer)
        self.active = layer
        self.refresh_layers()
        if zoom and layer.bbox:
            self.map.zoom_to_bbox(layer.bbox)
        else:
            self.map.render()
        self.message.set(f"Dodano warstwę „{layer.name}” ({len(layer.features)} obiektów)")

    def remove_layer(self, layer: Layer) -> None:
        if not dialogs.confirm(self, "Usuń warstwę", f"Usunąć warstwę „{layer.name}” z mapy?"):
            return
        if layer.editable and layer.features and not layer.meta.get("saved"):
            if not dialogs.confirm(self, "Niezapisane punkty",
                                   f"Warstwa „{layer.name}” ma niezapisane punkty. Usunąć mimo to?"):
                return
        self.layers.remove(layer)
        if self.edit_layer is layer:
            self.end_mode()
        if self.selection[0] is layer:
            self.set_selection(None, [])
        if self.active is layer:
            self.active = self.layers[0] if self.layers else None
        self.refresh_layers()
        self.map.render()

    def move_layer(self, layer: Layer, delta: int) -> None:
        index = self.layers.index(layer)
        new = max(0, min(len(self.layers) - 1, index + delta))
        if new != index:
            self.layers.insert(new, self.layers.pop(index))
            self.refresh_layers()
            self.map.render()

    def set_active(self, layer: Layer) -> None:
        self.active = layer
        self.refresh_layers()

    def toggle_visible(self, layer: Layer, var: tk.BooleanVar) -> None:
        layer.visible = var.get()
        self.map.render()

    def refresh_layers(self) -> None:
        t = self.theme
        self.layer_canvas.configure(background=t["bg"], highlightbackground=t["border"])
        for child in self.layer_frame.winfo_children():
            child.destroy()
        if not self.layers:
            ttk.Label(self.layer_frame, text="Brak warstw.\nUżyj „Wczytaj SHP” albo „H3 z PostGIS”.",
                      style="Muted.TLabel", justify="left").pack(anchor="w", padx=6, pady=8)
        for index, layer in enumerate(self.layers):
            active = layer is self.active
            card = tk.Frame(self.layer_frame, bg=t["panel"], highlightthickness=2 if active else 1,
                            highlightbackground=t["accent"] if active else t["border"])
            card.pack(fill="x", padx=2, pady=3)
            row = tk.Frame(card, bg=t["panel"])
            row.pack(fill="x", padx=4, pady=(4, 0))
            var = tk.BooleanVar(value=layer.visible)
            ttk.Checkbutton(row, variable=var, style="Panel.TCheckbutton",
                            command=lambda l=layer, v=var: self.toggle_visible(l, v)).pack(side="left")
            name = tk.Label(row, text=layer.name + ("  ✎" if layer is self.edit_layer else ""), bg=t["panel"],
                            fg=t["fg"], anchor="w", cursor="hand2",
                            font=("Segoe UI", 9, "bold" if active else "normal"))
            name.pack(side="left", fill="x", expand=True)
            name.bind("<Button-1>", lambda e, l=layer: self.set_active(l))
            for text, cmd, enabled in (("▲", lambda l=layer: self.move_layer(l, -1), index > 0),
                                       ("▼", lambda l=layer: self.move_layer(l, 1), index < len(self.layers) - 1),
                                       ("✕", lambda l=layer: self.remove_layer(l), True)):
                b = ttk.Button(row, text=text, width=2, style="Small.TButton", command=cmd)
                b.pack(side="left", padx=1)
                if not enabled:
                    b.state(["disabled"])
            meta = f"{layer.kind_label()} • {len(layer.features)} ob."
            if layer.meta.get("crs"):
                meta += f" • {layer.meta['crs'].split(' ')[0]}"
            info = tk.Label(card, text=meta, bg=t["panel"], fg=t["muted"], anchor="w", font=("Segoe UI", 8))
            info.pack(fill="x", padx=8)
            info.bind("<Button-1>", lambda e, l=layer: self.set_active(l))
            actions = tk.Frame(card, bg=t["panel"])
            actions.pack(fill="x", padx=6, pady=(2, 0))
            ttk.Button(actions, text="Styl", style="Small.TButton",
                       command=lambda l=layer: dialogs.StyleDialog(self, l).show()).pack(side="left", padx=1)
            if any(f.endswith("_norm") and f != "iop_norm" for f in layer.fields):
                ttk.Button(actions, text="Wagi", style="Small.TButton",
                           command=lambda l=layer: dialogs.AnalysisDialog(self, l).show()).pack(side="left", padx=1)
            ttk.Button(actions, text="Zoom", style="Small.TButton",
                       command=lambda l=layer: self.map.zoom_to_bbox(l.bbox)).pack(side="left", padx=1)
            self._draw_legend(card, layer)

    def _draw_legend(self, parent, layer: Layer) -> None:
        t = self.theme
        entries = layer.legend()
        row_h = 18
        title = layer.style.field if layer.style.mode == "graduated" else ""
        height = len(entries) * row_h + (18 if title else 0) + 8
        legend = tk.Canvas(parent, height=height, bg=t["panel"], highlightthickness=0)
        legend.pack(fill="x", padx=8, pady=(2, 6))
        y = 4
        if title:
            legend.create_text(2, y, text=f"Legenda: {title}", anchor="nw", fill=t["muted"], font=("Segoe UI", 8))
            y += 16
        for kind, fill, outline, label in entries:
            if kind == "point":
                legend.create_oval(6, y + 2, 18, y + 14, fill=fill or t["panel"], outline=outline)
            elif kind == "line":
                legend.create_line(2, y + 8, 22, y + 8, fill=fill or outline, width=max(2, layer.style.width))
            else:
                legend.create_rectangle(2, y + 2, 22, y + 14, fill=fill or t["panel"], outline=outline,
                                        width=2 if not fill else 1)
            legend.create_text(30, y + 8, text=label, anchor="w", fill=t["fg"], font=("Segoe UI", 8))
            y += row_h

    # ================================================================ zdarzenia mapy
    def on_cursor(self, world) -> None:
        if not world:
            self.coord_text.set("—")
            return
        self.coord_text.set(format_coords(self.coord_crs.get(), *world))

    def on_view_changed(self) -> None:
        self.scale_text.set(f"1:{self.map.scale_denominator():,.0f}".replace(",", " "))

    def _set_scale(self, _event=None) -> None:
        text = self.scale_text.get().replace(" ", "").replace("1:", "").replace(",", "")
        try:
            value = float(text)
        except ValueError:
            self.on_view_changed()
            return
        self.map.set_scale_denominator(value)

    def on_identify(self, layer, feat) -> None:
        if not layer:
            self.set_selection(None, [])
            return
        self.active = layer
        self.set_selection(layer, [feat])
        self.refresh_layers()

    def set_selection(self, layer, feats, zoom: bool = False) -> None:
        self.selection = (layer, list(feats))
        self.info_tree.delete(*self.info_tree.get_children())
        if layer and len(feats) == 1:
            feat = feats[0]
            self.info_title.configure(text=f"{layer.name} • obiekt #{feat.fid}\n{layer.label_for(feat)}")
            for key, value in feat.attrs.items():
                text = f"{value:.6g}" if isinstance(value, float) else "" if value is None else str(value)
                self.info_tree.insert("", "end", values=(key, text))
            cx, cy = (feat.bbox[0] + feat.bbox[2]) / 2, (feat.bbox[1] + feat.bbox[3]) / 2
            self.info_tree.insert("", "end", values=("— środek X/Y 2180", f"{cx:.2f} / {cy:.2f}"))
            lon, lat = PUWG1992.inverse(cx, cy)
            self.info_tree.insert("", "end", values=("— środek λ/φ 4326", f"{lon:.6f} / {lat:.6f}"))
        elif layer and feats:
            self.info_title.configure(text=f"{layer.name} • zaznaczono {len(feats)} obiektów")
        else:
            self.info_title.configure(text="Kliknij obiekt na mapie.")
        if zoom and feats:
            boxes = [f.bbox for f in feats]
            self.map.zoom_to_bbox((min(b[0] for b in boxes), min(b[1] for b in boxes),
                                   max(b[2] for b in boxes), max(b[3] for b in boxes)), margin=0.2)
        else:
            self.map.render()

    # ---------------------------------------------------------------- tryby
    def start_measure(self) -> None:
        self.end_mode(render=False)
        self.map.measure_points = []
        self.map.set_mode("measure")
        self.mode_text.set("Tryb: pomiar – wskaż 1. punkt (Esc kończy)")
        self.message.set("Kliknij na mapie dwa punkty.")

    def on_measure_click(self, world) -> None:
        pts = self.map.measure_points
        if len(pts) >= 2:
            pts.clear()
        pts.append(world)
        if len(pts) == 1:
            self.mode_text.set("Tryb: pomiar – wskaż 2. punkt (Esc kończy)")
            self.map.render()
            return
        self.map.mode = "pan"
        self.map.configure(cursor="arrow")
        self.mode_text.set("Tryb: przeglądanie")
        self.map.render()
        dialogs.MeasureResultDialog(self, pts[0], pts[1]).show()

    def start_adding(self, layer: Layer) -> None:
        self.end_mode(render=False)
        self.edit_layer = layer
        self.map.set_mode("add_point")
        self.mode_text.set(f"Tryb: dodawanie punktów → {layer.name}")
        self.message.set("Klik = nowy punkt, Ctrl+Z = cofnij, Esc = koniec dodawania.")
        self.refresh_layers()

    def on_add_point(self, world) -> None:
        layer = self.edit_layer
        if not layer:
            return
        number = max([f.attrs.get("id", 0) or 0 for f in layer.features] + [0]) + 1
        name = f"Punkt {number}"
        if self.ask_point_name:
            name = dialogs.PointNameDialog(self, number, world).show()
            if name is None:
                return
        lon, lat = PUWG1992.inverse(*world)
        feat = Feature(number, [[world]], {"id": number, "nazwa": name, "x_2180": round(world[0], 2),
                                           "y_2180": round(world[1], 2), "lon": round(lon, 7),
                                           "lat": round(lat, 7)})
        feat.compute_bbox("point")
        layer.features.append(feat)
        layer.meta["saved"] = False
        self.message.set(f"Dodano „{name}” do warstwy {layer.name} ({len(layer.features)} pkt)")
        self.refresh_layers()
        self.map.render()

    def undo_point(self) -> None:
        layer = self.edit_layer
        if layer and layer.features:
            removed = layer.features.pop()
            if self.selection[0] is layer and removed in self.selection[1]:
                self.set_selection(None, [])
            self.message.set(f"Cofnięto punkt „{removed.attrs.get('nazwa')}”")
            self.refresh_layers()
            self.map.render()

    def end_mode(self, render: bool = True) -> None:
        was_editing = self.edit_layer is not None
        self.edit_layer = None
        self.mode_text.set("Tryb: przeglądanie")
        if self.map.mode != "pan" or render:
            self.map.set_mode("pan")
        if was_editing:
            self.refresh_layers()

    # ---------------------------------------------------------------- motyw
    def set_theme(self, name: str) -> None:
        self.theme_name = self.root_app.theme_name = name
        self.theme = apply_theme(self.root_app.tk_root, name)
        self.configure(bg=self.theme["bg"])
        self.refresh_layers()
        self.map.render()


class GisApp:
    def __init__(self) -> None:
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.tk_root.title("PSIP GIS")
        self.theme_name = "jasny"
        self.theme = apply_theme(self.tk_root, self.theme_name)
        self.window: GisWindow | None = None
        self.show_login()

    def show_login(self) -> None:
        LoginWindow(self.tk_root, self.open_main, self.quit, THEMES[self.theme_name])

    def open_main(self, user: str) -> None:
        self.window = GisWindow(self, user)

    def logout(self) -> None:
        if self.window:
            self.window.destroy()
            self.window = None
        self.show_login()

    def quit(self) -> None:
        self.tk_root.destroy()

    def run(self) -> None:
        self.tk_root.mainloop()


if __name__ == "__main__":
    GisApp().run()
