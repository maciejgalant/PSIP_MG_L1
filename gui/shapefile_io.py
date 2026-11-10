"""Odczyt i zapis ESRI Shapefile (.shp/.shx/.dbf/.prj/.cpg) w czystym Pythonie.

Obslugiwane typy: Point, MultiPoint, PolyLine, Polygon (takze warianty Z i M - bierzemy X, Y).
Geometria zwracana jako lista czesci (part = lista punktow (x, y)).
"""
from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field
from pathlib import Path

POINT, POLYLINE, POLYGON, MULTIPOINT = 1, 3, 5, 8
BASE_TYPE = {0: 0, 1: 1, 3: 3, 5: 5, 8: 8, 11: 1, 13: 3, 15: 5, 18: 8, 21: 1, 23: 3, 25: 5, 28: 8}
TYPE_NAMES = {POINT: "punktowa", MULTIPOINT: "punktowa (multi)", POLYLINE: "liniowa", POLYGON: "poligonowa"}

CPG_ALIASES = {"UTF-8": "utf-8", "UTF8": "utf-8", "65001": "utf-8", "1250": "cp1250", "ANSI 1250": "cp1250",
               "WINDOWS-1250": "cp1250", "1252": "cp1252", "ANSI 1252": "cp1252", "ISO-8859-2": "iso8859_2",
               "88592": "iso8859_2", "UTF-16": "utf-16"}
LDID = {0x57: "cp1250", 0xC8: "cp1250", 0x03: "cp1252", 0x01: "cp437", 0x64: "cp852"}


@dataclass
class DbfField:
    name: str
    type: str = "C"      # C - tekst, N - liczba, F - zmiennoprzecinkowa, L - logiczna, D - data
    size: int = 80
    decimals: int = 0


@dataclass
class ShapeData:
    shape_type: int
    geometries: list            # [ [part, part...], ... ]  (None = pusta geometria)
    fields: list[DbfField]
    records: list[dict]
    bbox: tuple[float, float, float, float]
    prj: str | None = None
    encoding: str = "utf-8"
    path: Path | None = None
    extra: dict = field(default_factory=dict)


def _sibling(path: Path, ext: str) -> Path | None:
    for candidate in (path.with_suffix(ext), path.with_suffix(ext.upper())):
        if candidate.is_file():
            return candidate
    return None


def _read_shp(data: bytes):
    if len(data) < 100 or struct.unpack(">i", data[:4])[0] != 9994:
        raise ValueError("To nie jest plik .shp (zly naglowek)")
    shape_type = BASE_TYPE.get(struct.unpack("<i", data[32:36])[0])
    if shape_type is None:
        raise ValueError("Nieobslugiwany typ geometrii shapefile")
    bbox = struct.unpack("<4d", data[36:68])
    geometries = []
    pos = 100
    while pos + 8 <= len(data):
        _num, length = struct.unpack(">2i", data[pos:pos + 8])
        content = data[pos + 8:pos + 8 + 2 * length]
        pos += 8 + 2 * length
        if len(content) < 4:
            break
        rtype = BASE_TYPE.get(struct.unpack("<i", content[:4])[0], 0)
        if rtype == 0:
            geometries.append(None)
        elif rtype == POINT:
            x, y = struct.unpack("<2d", content[4:20])
            geometries.append([[(x, y)]])
        elif rtype == MULTIPOINT:
            n = struct.unpack("<i", content[36:40])[0]
            pts = struct.unpack(f"<{2 * n}d", content[40:40 + 16 * n])
            geometries.append([[(pts[i], pts[i + 1])] for i in range(0, 2 * n, 2)])
        else:
            nparts, npoints = struct.unpack("<2i", content[36:44])
            parts = list(struct.unpack(f"<{nparts}i", content[44:44 + 4 * nparts])) + [npoints]
            start = 44 + 4 * nparts
            flat = struct.unpack(f"<{2 * npoints}d", content[start:start + 16 * npoints])
            pts = [(flat[i], flat[i + 1]) for i in range(0, 2 * npoints, 2)]
            geometries.append([pts[parts[i]:parts[i + 1]] for i in range(nparts)])
    return shape_type, geometries, bbox


def _encoding(path: Path, ldid: int) -> str:
    cpg = _sibling(path, ".cpg")
    if cpg:
        key = cpg.read_text(encoding="ascii", errors="ignore").strip().upper()
        if key in CPG_ALIASES:
            return CPG_ALIASES[key]
        try:
            "a".encode(key.lower())
            return key.lower()
        except LookupError:
            pass
    return LDID.get(ldid, "")


def _read_dbf(path: Path):
    data = path.read_bytes()
    nrec, header_len, rec_len = struct.unpack("<IHH", data[4:12])
    ldid = data[29]
    fields = []
    pos = 32
    while pos < header_len - 1 and data[pos] != 0x0D:
        raw = data[pos:pos + 32]
        name = raw[:11].split(b"\x00")[0].decode("ascii", errors="replace").strip()
        fields.append(DbfField(name, chr(raw[11]), raw[16], raw[17]))
        pos += 32
    encoding = _encoding(path, ldid)
    if not encoding:  # sprobuj UTF-8, w razie bledu Windows-1250 (typowe dla danych z Polski)
        sample = data[header_len:header_len + rec_len * min(nrec, 500)]
        try:
            sample.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "cp1250"
    records = []
    for i in range(nrec):
        start = header_len + i * rec_len
        raw = data[start:start + rec_len]
        if not raw or raw[:1] == b"*":
            records.append(None)  # rekord usuniety - zachowujemy indeks zgodny z .shp
            continue
        offset, row = 1, {}
        for fld in fields:
            chunk = raw[offset:offset + fld.size]
            offset += fld.size
            text = chunk.decode(encoding, errors="replace").strip().strip("\x00")
            row[fld.name] = _convert(fld, text)
        records.append(row)
    return fields, records, encoding


def _convert(fld: DbfField, text: str):
    if fld.type in "NFO":
        if not text or set(text) <= {"*", "."}:
            return None
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            return None
        return int(value) if fld.decimals == 0 and value.is_integer() and abs(value) < 2 ** 53 else value
    if fld.type == "L":
        return {"T": True, "Y": True, "F": False, "N": False}.get(text[:1].upper())
    if fld.type == "D" and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def read_shapefile(path: str | Path) -> ShapeData:
    path = Path(path)
    if path.suffix.lower() != ".shp":
        path = path.with_suffix(".shp")
    if not path.is_file():
        raise FileNotFoundError(f"Brak pliku {path}")
    shape_type, geometries, bbox = _read_shp(path.read_bytes())
    dbf = _sibling(path, ".dbf")
    if dbf:
        fields, records, encoding = _read_dbf(dbf)
    else:
        fields, records, encoding = [], [], "utf-8"
    if len(records) < len(geometries):
        records += [{} for _ in range(len(geometries) - len(records))]
    prj_file = _sibling(path, ".prj")
    prj = prj_file.read_text(encoding="utf-8", errors="ignore") if prj_file else None
    # usuwamy rekordy oznaczone jako skasowane w .dbf
    pairs = [(g, r) for g, r in zip(geometries, records) if r is not None]
    return ShapeData(shape_type, [g for g, _ in pairs], fields, [r for _, r in pairs], bbox, prj,
                     encoding, path)


# ---------------------------------------------------------------- zapis
def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


def _format_value(fld: DbfField, value) -> bytes:
    if value is None or value == "":
        text = ""
    elif fld.type in "NF":
        try:
            number = float(value)
            text = f"{number:.{fld.decimals}f}" if fld.decimals else str(int(round(number)))
        except (TypeError, ValueError):
            text = ""
        return text.rjust(fld.size)[:fld.size].encode("ascii")
    elif fld.type == "L":
        text = "T" if value else "F"
    elif fld.type == "D":
        text = str(value).replace("-", "")[:8]
    else:
        text = str(value)
    raw = text.encode("utf-8")
    while len(raw) > fld.size:  # nie ucinaj znaku UTF-8 w polowie
        text = text[:-1]
        raw = text.encode("utf-8")
    return raw.ljust(fld.size, b" ")


def write_shapefile(path: str | Path, shape_type: int, geometries: list, fields: list[DbfField],
                    records: list[dict], prj: str | None = None) -> list[Path]:
    """Zapisuje warstwe. geometries: dla POINT [(x, y), ...]; dla linii/poligonow [[part...], ...]."""
    path = Path(path).with_suffix(".shp")
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = []
    all_points = []
    for geom in geometries:
        if shape_type == POINT:
            x, y = geom
            all_points.append((x, y))
            contents.append(struct.pack("<i2d", POINT, x, y))
        else:
            parts = [list(p) for p in geom]
            if shape_type == POLYGON:
                parts = [p if p[0] == p[-1] else p + [p[0]] for p in parts]
            pts = [pt for part in parts for pt in part]
            all_points.extend(pts)
            offsets, total = [], 0
            for part in parts:
                offsets.append(total)
                total += len(part)
            body = struct.pack("<i4d2i", shape_type, *_bbox(pts), len(parts), len(pts))
            body += struct.pack(f"<{len(parts)}i", *offsets)
            body += struct.pack(f"<{2 * len(pts)}d", *[c for pt in pts for c in pt])
            contents.append(body)
    bbox = _bbox(all_points)

    def header(file_len_words: int) -> bytes:
        return (struct.pack(">7i", 9994, 0, 0, 0, 0, 0, file_len_words)
                + struct.pack("<2i", 1000, shape_type) + struct.pack("<4d", *bbox) + struct.pack("<4d", 0, 0, 0, 0))

    shp_records, shx_records, offset = [], [], 50
    for i, content in enumerate(contents, start=1):
        words = len(content) // 2
        shp_records.append(struct.pack(">2i", i, words) + content)
        shx_records.append(struct.pack(">2i", offset, words))
        offset += 4 + words
    shp_bytes = b"".join(shp_records)
    path.write_bytes(header(50 + len(shp_bytes) // 2) + shp_bytes)
    path.with_suffix(".shx").write_bytes(header(50 + 4 * len(contents)) + b"".join(shx_records))

    # .dbf (dBASE III, UTF-8 opisane w .cpg)
    today = dt.date.today()
    rec_len = 1 + sum(f.size for f in fields)
    header_len = 32 + 32 * len(fields) + 1
    dbf = bytearray(struct.pack("<4BIHH20x", 3, today.year - 1900, today.month, today.day,
                                len(records), header_len, rec_len))
    for fld in fields:
        name = fld.name.encode("ascii", errors="replace")[:10].ljust(11, b"\x00")
        dbf += name + fld.type.encode("ascii") + b"\x00" * 4 + bytes([fld.size, fld.decimals]) + b"\x00" * 14
    dbf += b"\x0D"
    for record in records:
        dbf += b" " + b"".join(_format_value(fld, record.get(fld.name)) for fld in fields)
    dbf += b"\x1A"
    path.with_suffix(".dbf").write_bytes(bytes(dbf))
    path.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
    written = [path, path.with_suffix(".shx"), path.with_suffix(".dbf"), path.with_suffix(".cpg")]
    if prj:
        path.with_suffix(".prj").write_text(prj, encoding="ascii")
        written.append(path.with_suffix(".prj"))
    return written
