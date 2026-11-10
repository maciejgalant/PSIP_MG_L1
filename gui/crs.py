"""Uklady wspolrzednych bez zewnetrznych bibliotek: EPSG:2180, 4326, 3857 (+ dowolny TM z .prj).

Uklad roboczy mapy to EPSG:2180 (PUWG 1992, metry) - w nim sa siatki H3 w PostGIS.
Odwzorowanie Gaussa-Krugera liczone szeregami Krugera (dokladnosc ponizej 1 mm w Polsce).
Odleglosc geodezyjna: wzory Vincenty'ego na elipsoidzie GRS80.
"""
from __future__ import annotations

import math
import re

A_GRS80 = 6378137.0
F_GRS80 = 1 / 298.257222101
R_MERC = 6378137.0

MAP_CRS = "EPSG:2180"
DISPLAY_CRS = ("EPSG:4326", "EPSG:3857", "EPSG:2180")

WKT_2180 = (
    'PROJCS["ETRS_1989_Poland_CS92",GEOGCS["GCS_ETRS_1989",DATUM["D_ETRS_1989",'
    'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",-5300000.0],'
    'PARAMETER["Central_Meridian",19.0],PARAMETER["Scale_Factor",0.9993],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')


class TransverseMercator:
    """Odwzorowanie poprzeczne Merkatora (szeregi Krugera 3. rzedu)."""

    def __init__(self, lon0: float, k0: float, fe: float, fn: float,
                 a: float = A_GRS80, f: float = F_GRS80) -> None:
        self.lon0, self.k0, self.fe, self.fn = math.radians(lon0), k0, fe, fn
        n = f / (2 - f)
        self.n = n
        self.A = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
        self.alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16,
                      13 * n ** 2 / 48 - 3 * n ** 3 / 5,
                      61 * n ** 3 / 240)
        self.beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96,
                     n ** 2 / 48 + n ** 3 / 15,
                     17 * n ** 3 / 480)
        self.delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3,
                      7 * n ** 2 / 3 - 8 * n ** 3 / 5,
                      56 * n ** 3 / 15)
        self.c = 2 * math.sqrt(n) / (1 + n)

    def forward(self, lon: float, lat: float) -> tuple[float, float]:
        phi, dl = math.radians(lat), math.radians(lon) - self.lon0
        sin_phi = math.sin(phi)
        t = math.sinh(math.atanh(sin_phi) - self.c * math.atanh(self.c * sin_phi))
        xi = math.atan2(t, math.cos(dl))
        eta = math.atanh(math.sin(dl) / math.sqrt(1 + t * t))
        e, n = eta, xi
        for j, a in enumerate(self.alpha, start=1):
            e += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
            n += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        return self.fe + self.k0 * self.A * e, self.fn + self.k0 * self.A * n

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        xi = (y - self.fn) / (self.k0 * self.A)
        eta = (x - self.fe) / (self.k0 * self.A)
        xi_p, eta_p = xi, eta
        for j, b in enumerate(self.beta, start=1):
            xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
            eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
        phi = chi
        for j, d in enumerate(self.delta, start=1):
            phi += d * math.sin(2 * j * chi)
        lon = self.lon0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
        return math.degrees(lon), math.degrees(phi)


PUWG1992 = TransverseMercator(19.0, 0.9993, 500000.0, -5300000.0)


def merc_forward(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    return R_MERC * math.radians(lon), R_MERC * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def merc_inverse(x: float, y: float) -> tuple[float, float]:
    return math.degrees(x / R_MERC), math.degrees(2 * math.atan(math.exp(y / R_MERC)) - math.pi / 2)


class CRS:
    """Uklad zrodlowy warstwy: zamiana na/z EPSG:4326 (lon, lat w stopniach)."""

    def __init__(self, code: str, to_geo, from_geo, label: str | None = None) -> None:
        self.code, self._to_geo, self._from_geo = code, to_geo, from_geo
        self.label = label or code

    def to_geo(self, x: float, y: float) -> tuple[float, float]:
        return self._to_geo(x, y)

    def from_geo(self, lon: float, lat: float) -> tuple[float, float]:
        return self._from_geo(lon, lat)

    def __repr__(self) -> str:
        return f"CRS({self.label})"


CRS_2180 = CRS("EPSG:2180", PUWG1992.inverse, PUWG1992.forward, "EPSG:2180 (PUWG 1992)")
CRS_4326 = CRS("EPSG:4326", lambda x, y: (x, y), lambda lon, lat: (lon, lat), "EPSG:4326 (WGS 84 / ETRS89)")
CRS_3857 = CRS("EPSG:3857", merc_inverse, merc_forward, "EPSG:3857 (Web Mercator)")
KNOWN = {c.code: c for c in (CRS_2180, CRS_4326, CRS_3857)}


def _param(wkt: str, name: str) -> float | None:
    match = re.search(r'PARAMETER\["%s",\s*([-+0-9.eE]+)\]' % name, wkt, re.IGNORECASE)
    return float(match.group(1)) if match else None


def crs_from_prj(wkt: str | None) -> CRS | None:
    """Rozpoznaje uklad z pliku .prj (WKT). None = nie rozpoznano."""
    if not wkt:
        return None
    text = wkt.strip()
    upper = text.upper()
    if not upper.startswith("PROJCS"):
        return CRS_4326 if upper.startswith(("GEOGCS", "GEOGCRS")) else None
    if "MERCATOR_AUXILIARY_SPHERE" in upper or "PSEUDO-MERCATOR" in upper or "PSEUDO_MERCATOR" in upper \
            or "3857" in upper or "900913" in upper:
        return CRS_3857
    if "TRANSVERSE_MERCATOR" in upper or "GAUSS" in upper:
        lon0 = _param(text, "Central_Meridian")
        k0 = _param(text, "Scale_Factor")
        fe = _param(text, "False_Easting")
        fn = _param(text, "False_Northing")
        if None in (lon0, k0, fe, fn):
            return None
        if abs(lon0 - 19) < 1e-9 and abs(k0 - 0.9993) < 1e-9 and fe == 500000 and fn == -5300000:
            return CRS_2180
        tm = TransverseMercator(lon0, k0, fe, fn)
        name = re.match(r'PROJCS\["([^"]+)"', text)
        return CRS("TM", tm.inverse, tm.forward, name.group(1) if name else "Transverse Mercator")
    return None


def guess_crs(bbox: tuple[float, float, float, float]) -> CRS:
    """Gdy brak .prj: zgaduje po zakresie wspolrzednych."""
    xmin, ymin, xmax, ymax = bbox
    if -180 <= xmin <= 180 and -90 <= ymin <= 90 and -180 <= xmax <= 180 and -90 <= ymax <= 90:
        return CRS_4326
    if 1_500_000 < xmin < 2_800_000 and 6_000_000 < ymin < 7_500_000:
        return CRS_3857
    return CRS_2180


def transformer(src: CRS, dst: CRS = CRS_2180):
    """Funkcja (x, y) -> (x, y) z ukladu src do dst."""
    if src.code == dst.code and src.code != "TM":
        return lambda x, y: (x, y)
    return lambda x, y: dst.from_geo(*src.to_geo(x, y))


def map_to(code: str, x: float, y: float) -> tuple[float, float]:
    """Wspolrzedne mapy (2180) w wybranym ukladzie wyswietlania."""
    if code == "EPSG:2180":
        return x, y
    lon, lat = PUWG1992.inverse(x, y)
    return (lon, lat) if code == "EPSG:4326" else merc_forward(lon, lat)


def format_coords(code: str, x: float, y: float) -> str:
    a, b = map_to(code, x, y)
    if code == "EPSG:4326":
        ns, ew = ("N" if b >= 0 else "S"), ("E" if a >= 0 else "W")
        return f"φ {abs(b):.6f}° {ns}   λ {abs(a):.6f}° {ew}"
    return f"X {a:,.2f} m   Y {b:,.2f} m".replace(",", " ")


def geodesic_distance(lon1: float, lat1: float, lon2: float, lat2: float,
                      a: float = A_GRS80, f: float = F_GRS80) -> float:
    """Odleglosc na elipsoidzie (Vincenty, zadanie odwrotne) w metrach."""
    if (lon1, lat1) == (lon2, lat2):
        return 0.0
    b = (1 - f) * a
    L = math.radians(lon2 - lon1)
    U1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    U2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    sinU1, cosU1, sinU2, cosU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(200):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cosU2 * sin_lam, cosU1 * sinU2 - sinU1 * cosU2 * cos_lam)
        if sin_sigma == 0:
            return 0.0
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sigma
        cos2_alpha = 1 - sin_alpha ** 2
        cos2_sm = cos_sigma - 2 * sinU1 * sinU2 / cos2_alpha if cos2_alpha else 0.0
        C = f / 16 * cos2_alpha * (4 + f * (4 - 3 * cos2_alpha))
        prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (cos2_sm + C * cos_sigma * (-1 + 2 * cos2_sm ** 2)))
        if abs(lam - prev) < 1e-12:
            break
    u2 = cos2_alpha * (a * a - b * b) / (b * b)
    A_ = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B_ = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    d_sigma = B_ * sin_sigma * (cos2_sm + B_ / 4 * (
        cos_sigma * (-1 + 2 * cos2_sm ** 2) - B_ / 6 * cos2_sm * (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos2_sm ** 2)))
    return b * A_ * (sigma - d_sigma)


def distance_2180(p1: tuple[float, float], p2: tuple[float, float]) -> dict[str, float]:
    """Odleglosc miedzy dwoma punktami mapy: geodezyjna i plaska w ukladzie 2180."""
    lon1, lat1 = PUWG1992.inverse(*p1)
    lon2, lat2 = PUWG1992.inverse(*p2)
    return {"geodezyjna": geodesic_distance(lon1, lat1, lon2, lat2),
            "plaska_2180": math.hypot(p2[0] - p1[0], p2[1] - p1[1])}
