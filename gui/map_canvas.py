"""Interaktywne plotno mapy: rysowanie warstw (EPSG:2180), zoom, przesuwanie, identyfikacja, tryby.

Tryby kliku lewym przyciskiem:
  pan       - przeciaganie przesuwa mape, klik identyfikuje obiekt
  measure   - dwa kliki = pomiar odleglosci
  add_point - klik dodaje punkt do edytowanej warstwy
Przeciaganie prawym / srodkowym przyciskiem zawsze przesuwa mape.
"""
from __future__ import annotations

import math
import tkinter as tk

from pdf_export import nice_distance, nice_step


def _point_in_ring(x, y, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-300) + x1:
            inside = not inside
    return inside


def _segment_distance(px, py, a, b) -> float:
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


class MapCanvas(tk.Canvas):
    def __init__(self, master, app, **kwargs) -> None:
        super().__init__(master, highlightthickness=0, **kwargs)
        self.app = app
        self.center = (480000.0, 470000.0)   # okolice powiatu kutnowskiego w EPSG:2180
        self.scale_ = 0.004                   # piksele na metr
        self.mode = "pan"
        self.measure_points: list[tuple[float, float]] = []
        self.cursor_world: tuple[float, float] | None = None
        self._drag = None
        self._moved = False
        self._render_job = None
        self.px_per_m_screen = self.winfo_fpixels("1m") * 1000
        self.bind("<Configure>", lambda e: self.schedule_render(10))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.25))
        self.bind("<Button-5>", lambda e: self.zoom_at(e.x, e.y, 1 / 1.25))
        for button in (1, 2, 3):
            self.bind(f"<ButtonPress-{button}>", lambda e, b=button: self._press(e, b))
            self.bind(f"<B{button}-Motion>", self._drag_motion)
            self.bind(f"<ButtonRelease-{button}>", lambda e, b=button: self._release(e, b))
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self.app.on_cursor(None))

    # ------------------------------------------------------------ transformacje
    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.winfo_width(), self.winfo_height()
        return w / 2 + (x - self.center[0]) * self.scale_, h / 2 - (y - self.center[1]) * self.scale_

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        w, h = self.winfo_width(), self.winfo_height()
        return self.center[0] + (sx - w / 2) / self.scale_, self.center[1] - (sy - h / 2) / self.scale_

    def extent(self):
        x0, y1 = self.to_world(0, 0)
        x1, y0 = self.to_world(self.winfo_width(), self.winfo_height())
        return x0, y0, x1, y1

    def scale_denominator(self) -> float:
        """Mianownik skali mapy na ekranie: metry terenu na metr ekranu."""
        return self.px_per_m_screen / self.scale_

    def m_per_px(self) -> float:
        return 1 / self.scale_

    # ------------------------------------------------------------ widok
    def zoom_to_bbox(self, bbox, margin: float = 0.08, min_size: float = 200.0) -> None:
        if not bbox:
            return
        xmin, ymin, xmax, ymax = bbox
        width, height = max(xmax - xmin, min_size), max(ymax - ymin, min_size)
        w, h = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        self.center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        self.scale_ = (1 - 2 * margin) * min(w / width, h / height)
        self.render()

    def set_scale_denominator(self, denominator: float) -> None:
        self.scale_ = self.px_per_m_screen / max(denominator, 1)
        self.render()

    def zoom_at(self, sx, sy, factor) -> None:
        new_scale = max(1e-6, min(self.scale_ * factor, 50.0))
        factor = new_scale / self.scale_
        if factor == 1:
            return
        before = self.to_world(sx, sy)
        self.scale_ = new_scale
        after = self.to_world(sx, sy)
        self.center = (self.center[0] + before[0] - after[0], self.center[1] + before[1] - after[1])
        self.scale("moves", sx, sy, factor, factor)   # szybki podglad, pelne rysowanie za chwile
        self.schedule_render(140)
        self.app.on_view_changed()

    def schedule_render(self, delay: int = 60) -> None:
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(delay, self.render)

    # ------------------------------------------------------------ rysowanie
    def render(self) -> None:
        self._render_job = None
        t = self.app.theme
        self.configure(background=t["map_bg"])
        self.delete("all")
        ex0, ey0, ex1, ey1 = self.extent()
        tol = 0.6 / self.scale_   # pomijanie wierzcholkow blizszych niz ~0.6 px
        drawn = 0
        for layer in reversed(self.app.layers):   # pierwsza na liscie = na wierzchu
            if not layer.visible:
                continue
            st = layer.style
            tag = ("moves", "map", f"L{layer.id}")
            for feat in layer.features:
                b = feat.bbox
                if b[2] < ex0 or b[0] > ex1 or b[3] < ey0 or b[1] > ey1:
                    continue
                color = st.color_for(feat)
                drawn += 1
                if layer.kind == "polygon":
                    for poly in feat.geom:
                        self._draw_polygon(poly, color, st.outline, st.width, tag, tol)
                elif layer.kind == "line":
                    for part in feat.geom:
                        flat = self._flat(part, tol)
                        if len(flat) >= 4:
                            self.create_line(flat, fill=color or st.outline, width=st.width, tags=tag,
                                             capstyle="round", joinstyle="round")
                else:
                    r = st.point_size
                    for part in feat.geom:
                        for x, y in part:
                            sx, sy = self.to_screen(x, y)
                            self.create_oval(sx - r, sy - r, sx + r, sy + r, fill=color or st.fill,
                                             outline=st.outline, width=1, tags=tag)
        self._draw_selection()
        self._draw_measure()
        if not self.app.layers:
            self.create_text(24, 24, anchor="nw", fill=t["muted"], font=("Segoe UI", 11),
                             text="Brak warstw. Wczytaj SHP lub warstwę H3 z PostGIS (pasek narzędzi).")
        self._draw_scalebar()
        self._draw_north()
        self.app.on_view_changed()
        self.drawn_count = drawn

    def _flat(self, pts, tol):
        out = []
        last = None
        for x, y in pts:
            if last is not None and abs(x - last[0]) < tol and abs(y - last[1]) < tol:
                continue
            last = (x, y)
            out.extend(self.to_screen(x, y))
        if last is not None and pts and last != tuple(pts[-1]):
            out.extend(self.to_screen(*pts[-1]))
        return out

    def _draw_polygon(self, poly, fill, outline, width, tag, tol):
        rings = [self._flat(r, tol) for r in poly]
        rings = [r for r in rings if len(r) >= 6]
        if not rings:
            return
        if len(rings) == 1:
            self.create_polygon(rings[0], fill=fill, outline=outline, width=width, tags=tag)
            return
        # otwory: wspolny kontur "dziurki od klucza" + regula parzystosci, obrysy osobno
        outer = rings[0]
        flat = outer + outer[:2]
        for hole in rings[1:]:
            flat += hole + hole[:2] + outer[:2]
        if fill:
            self.create_polygon(flat, fill=fill, outline="", width=0, tags=tag)
        for ring in rings:
            self.create_line(ring + ring[:2], fill=outline, width=width, tags=tag)

    def _draw_selection(self) -> None:
        layer, feats = self.app.selection
        if not layer or not feats or not layer.visible:
            return
        color = self.app.theme["highlight"]
        for feat in feats:
            if layer.kind == "polygon":
                for poly in feat.geom:
                    for ring in poly:
                        flat = [c for p in ring for c in self.to_screen(*p)]
                        if len(flat) >= 4:
                            self.create_line(flat + flat[:2], fill=color, width=3.2, tags=("moves", "sel"))
            elif layer.kind == "line":
                for part in feat.geom:
                    flat = [c for p in part for c in self.to_screen(*p)]
                    if len(flat) >= 4:
                        self.create_line(flat, fill=color, width=layer.style.width + 4, tags=("moves", "sel"))
                        self.tag_lower("sel", f"L{layer.id}")
            else:
                r = layer.style.point_size + 4
                for part in feat.geom:
                    for x, y in part:
                        sx, sy = self.to_screen(x, y)
                        self.create_oval(sx - r, sy - r, sx + r, sy + r, outline=color, width=3,
                                         tags=("moves", "sel"))

    def _draw_measure(self) -> None:
        if not self.measure_points:
            return
        color = self.app.theme["measure"]
        pts = [self.to_screen(*p) for p in self.measure_points]
        if len(pts) == 1 and self.cursor_world and self.mode == "measure":
            pts.append(self.to_screen(*self.cursor_world))
        if len(pts) >= 2:
            self.create_line([c for p in pts for c in p], fill=color, width=2.5, dash=(8, 4),
                             tags=("moves", "measure"))
        for sx, sy in [self.to_screen(*p) for p in self.measure_points]:
            self.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill=color, outline="white", width=1.5,
                             tags=("moves", "measure"))
        if len(self.measure_points) == 2:
            from crs import distance_2180
            d = distance_2180(*self.measure_points)["geodezyjna"]
            (x1, y1), (x2, y2) = pts[0], pts[1]
            self.create_text((x1 + x2) / 2, (y1 + y2) / 2 - 12, text=nice_distance(d), fill=color,
                             font=("Segoe UI", 10, "bold"), tags=("moves", "measure"))

    def _draw_scalebar(self) -> None:
        t = self.app.theme
        h = self.winfo_height()
        target_m = 120 / self.scale_
        length_m = nice_step(target_m)
        length_px = length_m * self.scale_
        x, y = 16, h - 26
        self.create_rectangle(x - 8, y - 22, x + length_px + 70, y + 12, fill=t["map_box"], outline=t["border"],
                              tags=("fixed", "scalebar"))
        half = length_px / 2
        self.create_rectangle(x, y, x + half, y + 6, fill=t["map_fg"], outline=t["map_fg"], tags="fixed")
        self.create_rectangle(x + half, y, x + length_px, y + 6, fill=t["map_box"], outline=t["map_fg"],
                              tags="fixed")
        for pos, value in ((0, 0), (half, length_m / 2), (length_px, length_m)):
            label = "0" if value == 0 else nice_distance(value).replace(".000 km", " km").replace(".00 m", " m")
            self.create_text(x + pos, y - 4, text=label, anchor="s", fill=t["map_fg"], font=("Segoe UI", 8),
                             tags="fixed")
        self.create_text(x + length_px + 10, y + 3, anchor="w", fill=t["map_fg"], font=("Segoe UI", 8, "bold"),
                         text=f"1:{self.scale_denominator():,.0f}".replace(",", " "), tags="fixed")

    def _draw_north(self) -> None:
        t = self.app.theme
        w = self.winfo_width()
        x, y = w - 30, 22
        self.create_polygon(x, y - 10, x + 8, y + 12, x, y + 7, x - 8, y + 12, fill=t["map_fg"],
                            outline=t["map_fg"], tags="fixed")
        self.create_text(x, y + 24, text="N", fill=t["map_fg"], font=("Segoe UI", 9, "bold"), tags="fixed")

    # ------------------------------------------------------------ identyfikacja
    def identify(self, x: float, y: float):
        """Najwyzszy widoczny obiekt pod punktem (wspolrzedne mapy)."""
        tol = 6 / self.scale_
        for layer in self.app.layers:
            if not layer.visible:
                continue
            for feat in reversed(layer.features):
                b = feat.bbox
                if x < b[0] - tol or x > b[2] + tol or y < b[1] - tol or y > b[3] + tol:
                    continue
                if layer.kind == "polygon":
                    for poly in feat.geom:
                        if _point_in_ring(x, y, poly[0]) and not any(_point_in_ring(x, y, h) for h in poly[1:]):
                            return layer, feat
                elif layer.kind == "line":
                    for part in feat.geom:
                        if any(_segment_distance(x, y, a, b2) <= tol for a, b2 in zip(part, part[1:])):
                            return layer, feat
                else:
                    ptol = (layer.style.point_size + 3) / self.scale_
                    if any(math.hypot(px - x, py - y) <= ptol for part in feat.geom for px, py in part):
                        return layer, feat
        return None, None

    # ------------------------------------------------------------ zdarzenia
    def _wheel(self, event) -> None:
        self.zoom_at(event.x, event.y, 1.25 if event.delta > 0 else 1 / 1.25)

    def _press(self, event, button) -> None:
        self.focus_set()
        self._drag = (event.x, event.y, button)
        self._moved = False
        if button != 1 or self.mode == "pan":
            self.configure(cursor="fleur")

    def _drag_motion(self, event) -> None:
        if not self._drag:
            return
        x0, y0, _button = self._drag
        dx, dy = event.x - x0, event.y - y0
        if not self._moved and abs(dx) + abs(dy) < 4:
            return
        self._moved = True
        self.center = (self.center[0] - dx / self.scale_, self.center[1] + dy / self.scale_)
        self._drag = (event.x, event.y, _button)
        self.move("moves", dx, dy)
        self._motion(event)

    def _release(self, event, button) -> None:
        self.configure(cursor=self._mode_cursor())
        moved, self._drag = self._moved, None
        if moved:
            self.render()
            return
        if button != 1:
            return
        world = self.to_world(event.x, event.y)
        if self.mode == "measure":
            self.app.on_measure_click(world)
        elif self.mode == "add_point":
            self.app.on_add_point(world)
        else:
            self.app.on_identify(*self.identify(*world))

    def _motion(self, event) -> None:
        self.cursor_world = self.to_world(event.x, event.y)
        self.app.on_cursor(self.cursor_world)
        if self.mode == "measure" and len(self.measure_points) == 1:
            self.delete("measure")
            self._draw_measure()

    def _mode_cursor(self) -> str:
        return {"measure": "crosshair", "add_point": "tcross"}.get(self.mode, "arrow")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.configure(cursor=self._mode_cursor())
        if mode != "measure":
            self.measure_points = []
        self.render()
