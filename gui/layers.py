"""Model warstw mapy: obiekty, style (pojedynczy / stopniowany), wczytywanie SHP i H3 z PostGIS.

Wszystkie geometrie trzymane sa w ukladzie mapy EPSG:2180 (metry).
  punkt:   parts = [[(x, y)], ...]
  linia:   parts = [[(x, y), ...], ...]
  poligon: polygons = [[ring_zewn, otwor, ...], ...]
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from crs import CRS, CRS_2180, WKT_2180, crs_from_prj, guess_crs, transformer
from shapefile_io import (MULTIPOINT, POINT, POLYGON, POLYLINE, DbfField, read_shapefile,
                          write_shapefile)

_ids = itertools.count(1)
PALETTE = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777"]
IOP_RAMP = ("#d7191c", "#ffffff", "#1a9641")  # jak klasy IOP w compute_iop.py / iop_style.qml


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def lerp_color(a: str, b: str, t: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(x + (y - x) * t for x, y in zip(ra, rb))


def ramp_color(colors: list[str], t: float) -> str:
    t = max(0.0, min(1.0, t))
    if len(colors) == 1:
        return colors[0]
    pos = t * (len(colors) - 1)
    i = min(int(pos), len(colors) - 2)
    return lerp_color(colors[i], colors[i + 1], pos - i)


def fmt_number(value: float) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    if abs(value) >= 10:
        return f"{value:.1f}"
    if abs(value) >= 0.01:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{value:.2e}"


# ---------------------------------------------------------------- styl
@dataclass
class Style:
    mode: str = "single"                  # single | graduated
    fill: str = "#93c5fd"
    outline: str = "#1e3a8a"
    width: float = 1.0
    point_size: float = 5.0
    field: str = ""
    vmin: float = 0.0
    vmax: float = 1.0
    classes: int = 5
    colors: list[str] = dc_field(default_factory=lambda: list(IOP_RAMP))
    no_fill: bool = False
    labels: list[str] = dc_field(default_factory=list)   # wlasne opisy klas (opcjonalnie)

    def class_index(self, value) -> int | None:
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if self.vmax <= self.vmin:
            return 0
        t = (value - self.vmin) / (self.vmax - self.vmin)
        return max(0, min(self.classes - 1, int(t * self.classes) if t < 1 else self.classes - 1))

    def class_color(self, index: int) -> str:
        return ramp_color(self.colors, index / (self.classes - 1) if self.classes > 1 else 0.5)

    def color_for(self, feature: "Feature") -> str:
        if self.mode != "graduated" or not self.field:
            return "" if self.no_fill else self.fill
        index = self.class_index(feature.attrs.get(self.field))
        return "#9ca3af" if index is None else self.class_color(index)

    def legend(self, kind: str, layer_name: str, counts: list[int] | None = None):
        """[(rodzaj, wypelnienie, obrys, opis)]"""
        if self.mode != "graduated" or not self.field:
            return [(kind, "" if self.no_fill else self.fill, self.outline, layer_name)]
        entries = []
        step = (self.vmax - self.vmin) / self.classes
        for i in range(self.classes):
            low, high = self.vmin + i * step, self.vmin + (i + 1) * step
            label = f"{fmt_number(low)} – {fmt_number(high)}"
            if i < len(self.labels) and self.labels[i]:
                label += f"  {self.labels[i]}"
            if counts is not None:
                label += f"  ({counts[i]})"
            entries.append((kind, self.class_color(i), self.outline, label))
        return entries

    def copy(self) -> "Style":
        return Style(**{**self.__dict__, "colors": list(self.colors), "labels": list(self.labels)})


# ---------------------------------------------------------------- obiekty i warstwy
@dataclass
class Feature:
    fid: int
    geom: list          # patrz docstring modulu
    attrs: dict
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)

    def compute_bbox(self, kind: str) -> None:
        if kind == "polygon":
            pts = [p for poly in self.geom for ring in poly for p in ring]
        else:
            pts = [p for part in self.geom for p in part]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            self.bbox = (min(xs), min(ys), max(xs), max(ys))


class Layer:
    def __init__(self, name: str, kind: str, features: list[Feature] | None = None,
                 fields: list[str] | None = None, source: str = "", style: Style | None = None) -> None:
        self.id = next(_ids)
        self.name, self.kind, self.source = name, kind, source
        self.features: list[Feature] = features or []
        self.fields: list[str] = fields or []
        self.visible = True
        self.editable = False
        self.origin = "shp"          # shp | h3 | edit | db
        self.meta: dict = {}
        color = PALETTE[(self.id - 1) % len(PALETTE)]
        self.style = style or Style(fill=color if kind != "line" else color,
                                    outline="#1f2937" if kind == "polygon" else color,
                                    width=1.0 if kind == "polygon" else 2.0)
        if kind == "polygon" and style is None:
            self.style.fill = ramp_color(["#ffffff", color], 0.45)
            self.style.outline = color
        for feat in self.features:
            feat.compute_bbox(kind)

    @property
    def bbox(self):
        boxes = [f.bbox for f in self.features if f.geom]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def numeric_fields(self) -> list[str]:
        out = []
        for name in self.fields:
            for feat in self.features[:200]:
                value = feat.attrs.get(name)
                if value is None or value == "":
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    break
                out.append(name)
                break
        return out

    def value_range(self, field_name: str) -> tuple[float, float] | None:
        values = [float(f.attrs[field_name]) for f in self.features
                  if isinstance(f.attrs.get(field_name), (int, float)) and not isinstance(f.attrs.get(field_name), bool)]
        return (min(values), max(values)) if values else None

    def legend(self):
        counts = None
        st = self.style
        if st.mode == "graduated" and st.field:
            counts = [0] * st.classes
            for feat in self.features:
                index = st.class_index(feat.attrs.get(st.field))
                if index is not None:
                    counts[index] += 1
        title = self.name if st.mode != "graduated" else f"{st.field}"
        return st.legend(self.kind, title, counts)

    def label_for(self, feat: Feature) -> str:
        for key in ("nazwa", "name", "NAZWA", "JPT_NAZWA_", "h3_id", "id"):
            if feat.attrs.get(key) not in (None, ""):
                return f"{feat.attrs[key]}"
        return f"#{feat.fid}"

    def kind_label(self) -> str:
        return {"polygon": "poligonowa", "line": "liniowa", "point": "punktowa"}[self.kind]


# ---------------------------------------------------------------- SHP
def _ring_area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2


def _point_in_ring(pt, ring) -> bool:
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-300) + x1:
            inside = not inside
    return inside


def group_rings(rings) -> list[list]:
    """Pierscienie shapefile -> poligony [zewnetrzny, otwory...] (zewn. = zgodnie z ruchem wskazowek)."""
    outers, holes = [], []
    for ring in rings:
        if len(ring) < 3:
            continue
        (outers if _ring_area(ring) <= 0 else holes).append(ring)
    if not outers:  # odwrotna orientacja w pliku - traktujemy wszystko jak zewnetrzne
        outers, holes = holes, []
    polygons = [[ring] for ring in outers]
    for hole in holes:
        owner = next((p for p in polygons if _point_in_ring(hole[0], p[0])), None)
        if owner:
            owner.append(hole)
        else:  # otwor bez pasujacego obrysu - rysujemy jako osobny poligon
            polygons.append([hole])
    return polygons


def load_shapefile(path: str | Path, crs_override: CRS | None = None) -> Layer:
    data = read_shapefile(path)
    src = crs_override or crs_from_prj(data.prj) or guess_crs(data.bbox)
    to_map = transformer(src)
    kind = {POINT: "point", MULTIPOINT: "point", POLYLINE: "line", POLYGON: "polygon"}[data.shape_type]
    features = []
    for fid, (geom, rec) in enumerate(zip(data.geometries, data.records), start=1):
        if not geom:
            continue
        parts = [[to_map(x, y) for x, y in part] for part in geom]
        g = group_rings(parts) if kind == "polygon" else parts
        features.append(Feature(fid, g, dict(rec)))
    layer = Layer(Path(data.path).stem, kind, features, [f.name for f in data.fields], str(data.path))
    layer.meta = {"crs": src.label, "encoding": data.encoding, "dbf_fields": data.fields}
    return layer


def inspect_shapefile(path: str | Path) -> dict:
    data = read_shapefile(path)
    src = crs_from_prj(data.prj)
    return {"typ": {POINT: "punktowa", MULTIPOINT: "punktowa", POLYLINE: "liniowa", POLYGON: "poligonowa"}
            [data.shape_type], "obiekty": len(data.geometries), "pola": [f.name for f in data.fields],
            "crs": src.label if src else f"brak .prj – zgadnięto: {guess_crs(data.bbox).label}",
            "kodowanie": data.encoding}


def new_point_layer(name: str) -> Layer:
    layer = Layer(name, "point", [], ["id", "nazwa", "x_2180", "y_2180", "lon", "lat"], "")
    layer.editable = True
    layer.origin = "edit"
    layer.style.point_size = 6
    layer.style.fill = "#f97316"
    layer.style.outline = "#7c2d12"
    return layer


def save_point_layer(layer: Layer, path: str | Path) -> list[Path]:
    fields = [DbfField("id", "N", 10), DbfField("nazwa", "C", 120), DbfField("x_2180", "N", 14, 2),
              DbfField("y_2180", "N", 14, 2), DbfField("lon", "N", 14, 7), DbfField("lat", "N", 14, 7)]
    known = {f.name for f in fields}
    for extra in layer.fields:  # dodatkowe pola z warstwy, jesli sa
        if extra not in known and len(extra) <= 10:
            fields.append(DbfField(extra, "C", 120))
    geoms = [f.geom[0][0] for f in layer.features]
    records = [f.attrs for f in layer.features]
    return write_shapefile(path, POINT, geoms, fields, records, WKT_2180)


# ---------------------------------------------------------------- H3 z PostGIS
def load_h3_layer(resolution: int) -> Layer:
    from psip_common import run_sql  # import tutaj: modul wymaga Dockera tylko przy uzyciu
    if resolution not in (7, 8, 9):
        raise ValueError("Rozdzielczość H3 musi być 7, 8 lub 9")
    lines = run_sql(
        "SELECT json_build_object('a', to_jsonb(t) - 'geom', "
        "'g', ST_AsGeoJSON(ST_Transform(geom, 2180), 1)::json)::text "
        f"FROM grid.h3_iop t WHERE resolution = {resolution} ORDER BY h3_id", timeout=600)
    if not lines:
        raise RuntimeError(f"Brak komórek H3 R{resolution} w grid.h3_iop – uruchom „Oblicz IOP” w panelu")
    features, fields = [], []
    for fid, line in enumerate(lines, start=1):
        obj = json.loads(line)
        attrs, geom = obj["a"], obj["g"]
        if not fields:
            fields = list(attrs)
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        g = [[[tuple(p) for p in ring] for ring in poly] for poly in polys]
        features.append(Feature(fid, g, attrs))
    style = Style(mode="graduated", field="iop_norm", vmin=0.0, vmax=1.0, classes=5, colors=list(IOP_RAMP),
                  outline="#64748b", width=0.4 if resolution == 9 else 0.7,
                  labels=["bardzo trudna", "trudna", "przeciętna", "dobra", "bardzo dobra"])
    layer = Layer(f"H3 R{resolution} – IOP", "polygon", features, fields, "PostGIS grid.h3_iop", style)
    layer.origin = "h3"
    layer.meta = {"resolution": resolution, "crs": CRS_2180.label}
    return layer


def load_county_layer() -> Layer:
    from psip_common import run_sql
    lines = run_sql("SELECT json_build_object('a', to_jsonb(t) - 'geom', "
                    "'g', ST_AsGeoJSON(ST_Transform(geom, 2180), 1)::json)::text FROM public.powiat t")
    features = []
    for fid, line in enumerate(lines, start=1):
        obj = json.loads(line)
        geom = obj["g"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        features.append(Feature(fid, [[[tuple(p) for p in r] for r in poly] for poly in polys], obj["a"]))
    style = Style(fill="#ffffff", outline="#0f172a", width=2.2, no_fill=True)
    layer = Layer("Granica powiatu (PostGIS)", "polygon", features,
                  list(features[0].attrs) if features else [], "PostGIS public.powiat", style)
    layer.origin = "db"
    return layer
