"""Eksport mapy z tk.Canvas do wektorowego PDF - czysty Python (zlib + struct).

Elementy plotna (wielokaty, linie, prostokaty, owale, teksty) sa przepisywane do PDF
jako grafika wektorowa. Czcionka TrueType z systemu (Arial / Segoe UI / DejaVu Sans)
jest osadzana, wiec polskie znaki wyswietlaja sie poprawnie w kazdej przegladarce PDF.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import struct
import tkinter.font as tkfont
import zlib
from pathlib import Path

PAGE_SIZES = {"A4": (595.28, 841.89), "A3": (841.89, 1190.55)}
FONT_CANDIDATES = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "tahoma.ttf", "verdana.ttf")
] + [Path(p) for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                       "/usr/share/fonts/TTF/DejaVuSans.ttf",
                       "/System/Library/Fonts/Supplemental/Arial.ttf",
                       "/Library/Fonts/Arial.ttf")]
GLYPH_NAMES = {
    "ą": "aogonek", "ć": "cacute", "ę": "eogonek", "ł": "lslash", "ń": "nacute", "ó": "oacute",
    "ś": "sacute", "ź": "zacute", "ż": "zdotaccent", "Ą": "Aogonek", "Ć": "Cacute", "Ę": "Eogonek",
    "Ł": "Lslash", "Ń": "Nacute", "Ó": "Oacute", "Ś": "Sacute", "Ź": "Zacute", "Ż": "Zdotaccent",
    "–": "endash", "—": "emdash", "°": "degree", "·": "periodcentered", "²": "twosuperior",
    "×": "multiply", "±": "plusminus", "µ": "mu", "„": "quotedblbase", "”": "quotedblright",
    "’": "quoteright", "…": "ellipsis", "→": "arrowright", "≥": "greaterequal", "≤": "lessequal",
    "Σ": "Sigma", "φ": "phi", "λ": "lambda", "−": "minus",
}


# ------------------------------------------------------------------ czcionka TTF
class TrueTypeFont:
    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()
        self.name = "".join(ch for ch in path.stem.title() if ch.isalnum()) or "Font"
        tables = {}
        num = struct.unpack(">H", self.data[4:6])[0]
        for i in range(num):
            tag, _chk, off, length = struct.unpack(">4sIII", self.data[12 + 16 * i:28 + 16 * i])
            tables[tag.decode("latin-1")] = (off, length)
        head = tables["head"][0]
        self.units = struct.unpack(">H", self.data[head + 18:head + 20])[0]
        self.bbox = struct.unpack(">4h", self.data[head + 36:head + 44])
        hhea = tables["hhea"][0]
        self.ascent, self.descent = struct.unpack(">2h", self.data[hhea + 4:hhea + 8])
        n_metrics = struct.unpack(">H", self.data[hhea + 34:hhea + 36])[0]
        hmtx = tables["hmtx"][0]
        self.advances = [struct.unpack(">H", self.data[hmtx + 4 * i:hmtx + 4 * i + 2])[0] for i in range(n_metrics)]
        self.cmap = self._cmap(tables["cmap"][0])

    def _cmap(self, base: int) -> dict[int, int]:
        count = struct.unpack(">H", self.data[base + 2:base + 4])[0]
        for i in range(count):
            pid, eid, off = struct.unpack(">HHI", self.data[base + 4 + 8 * i:base + 12 + 8 * i])
            sub = base + off
            if pid == 3 and eid in (1, 10) and struct.unpack(">H", self.data[sub:sub + 2])[0] == 4:
                return self._format4(sub)
        raise ValueError("Czcionka bez tablicy cmap formatu 4")

    def _format4(self, sub: int) -> dict[int, int]:
        seg = struct.unpack(">H", self.data[sub + 6:sub + 8])[0] // 2
        ends = struct.unpack(f">{seg}H", self.data[sub + 14:sub + 14 + 2 * seg])
        p = sub + 16 + 2 * seg
        starts = struct.unpack(f">{seg}H", self.data[p:p + 2 * seg])
        deltas = struct.unpack(f">{seg}h", self.data[p + 2 * seg:p + 4 * seg])
        range_pos = p + 4 * seg
        offsets = struct.unpack(f">{seg}H", self.data[range_pos:range_pos + 2 * seg])
        mapping = {}
        for i in range(seg):
            for code in range(starts[i], min(ends[i], 0x2FFF) + 1):
                if offsets[i] == 0:
                    glyph = (code + deltas[i]) & 0xFFFF
                else:
                    addr = range_pos + 2 * i + offsets[i] + 2 * (code - starts[i])
                    glyph = struct.unpack(">H", self.data[addr:addr + 2])[0]
                    if glyph:
                        glyph = (glyph + deltas[i]) & 0xFFFF
                if glyph:
                    mapping[code] = glyph
        return mapping

    def width(self, char: str) -> float:
        glyph = self.cmap.get(ord(char), 0)
        adv = self.advances[min(glyph, len(self.advances) - 1)]
        return adv * 1000 / self.units


def find_font() -> TrueTypeFont | None:
    for path in FONT_CANDIDATES:
        if path.is_file():
            try:
                return TrueTypeFont(path)
            except Exception:  # noqa: BLE001 - probujemy kolejna czcionke
                continue
    return None


# ------------------------------------------------------------------ dokument PDF
class PdfPage:
    """Jedna strona PDF; wspolrzedne w punktach, poczatek w LEWYM GORNYM rogu (jak w Tk)."""

    def __init__(self, width: float, height: float) -> None:
        self.width, self.height = width, height
        self.ops: list[str] = []
        self.font = find_font()
        self.chars: dict[str, int] = {}   # znak spoza ASCII -> kod 128..255

    # --- pomocnicze
    def _y(self, y: float) -> float:
        return self.height - y

    @staticmethod
    def _num(value: float) -> str:
        text = f"{value:.2f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def _pt(self, x: float, y: float) -> str:
        return f"{self._num(x)} {self._num(self._y(y))}"

    def _rgb(self, rgb, stroke: bool) -> str:
        r, g, b = (c / 255 for c in rgb)
        return f"{r:.3f} {g:.3f} {b:.3f} {'RG' if stroke else 'rg'}"

    # --- grafika
    def save(self):
        self.ops.append("q")

    def restore(self):
        self.ops.append("Q")

    def clip_rect(self, x, y, w, h):
        self.ops.append(f"{self._pt(x, y + h)} {self._num(w)} {self._num(h)} re W n")

    def path(self, points, fill=None, stroke=None, width=1.0, closed=True, dash=None, evenodd=True):
        if len(points) < 2 or (fill is None and stroke is None):
            return
        out = ["q"]
        if fill is not None:
            out.append(self._rgb(fill, False))
        if stroke is not None:
            out.append(self._rgb(stroke, True))
            out.append(f"{self._num(max(width, 0.1))} w 1 j 1 J")
            if dash:
                out.append("[" + " ".join(self._num(d) for d in dash) + "] 0 d")
        out.append(f"{self._pt(*points[0])} m")
        out.extend(f"{self._pt(x, y)} l" for x, y in points[1:])
        star = "*" if evenodd else ""
        if closed:
            out.append("h")
            op = ("B" + star) if fill is not None and stroke is not None else ("f" + star) if fill is not None else "S"
        else:
            op = "S"
        out.append(op)
        out.append("Q")
        self.ops.append(" ".join(out))

    def rect(self, x, y, w, h, fill=None, stroke=None, width=1.0):
        self.path([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill, stroke, width)

    def ellipse(self, x0, y0, x1, y1, fill=None, stroke=None, width=1.0):
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
        if fill is None and stroke is None:
            return
        k = 0.5523
        cy = self._y(cy)
        n = self._num
        out = ["q"]
        if fill is not None:
            out.append(self._rgb(fill, False))
        if stroke is not None:
            out += [self._rgb(stroke, True), f"{n(max(width, 0.1))} w"]
        out.append(f"{n(cx + rx)} {n(cy)} m")
        out.append(f"{n(cx + rx)} {n(cy + k * ry)} {n(cx + k * rx)} {n(cy + ry)} {n(cx)} {n(cy + ry)} c")
        out.append(f"{n(cx - k * rx)} {n(cy + ry)} {n(cx - rx)} {n(cy + k * ry)} {n(cx - rx)} {n(cy)} c")
        out.append(f"{n(cx - rx)} {n(cy - k * ry)} {n(cx - k * rx)} {n(cy - ry)} {n(cx)} {n(cy - ry)} c")
        out.append(f"{n(cx + k * rx)} {n(cy - ry)} {n(cx + rx)} {n(cy - k * ry)} {n(cx + rx)} {n(cy)} c h")
        out.append("B" if fill is not None and stroke is not None else "f" if fill is not None else "S")
        out.append("Q")
        self.ops.append(" ".join(out))

    # --- tekst
    def text_width(self, text: str, size: float) -> float:
        if self.font:
            return sum(self.font.width(ch) for ch in text) * size / 1000
        return 0.52 * size * len(text)

    def _encode(self, text: str) -> str:
        out = []
        for ch in text:
            code = ord(ch)
            if 32 <= code < 127:
                byte = code
            else:
                if ch not in self.chars:
                    if len(self.chars) >= 128 or (self.font and code not in self.font.cmap):
                        byte = ord("?")
                        out.append(chr(byte))
                        continue
                    self.chars[ch] = 128 + len(self.chars)
                byte = self.chars[ch]
            c = chr(byte)
            out.append("\\" + c if c in "()\\" else c if byte < 128 else f"\\{byte:03o}")
        return "".join(out)

    def text(self, x, y, text, size=10.0, color=(0, 0, 0), anchor="nw"):
        """anchor jak w Tk: n, ne, e, se, s, sw, w, nw, center."""
        if not text:
            return
        for i, line in enumerate(str(text).split("\n")):
            width = self.text_width(line, size)
            ly = y + i * size * 1.2
            dx = 0.0 if "w" in anchor else -width if "e" in anchor else -width / 2
            if "n" in anchor:
                base = ly + size * 0.8
            elif "s" in anchor:
                base = ly - size * 0.2
            else:
                base = ly + size * 0.32
            self.ops.append(f"BT {self._rgb(color, False)} /F1 {self._num(size)} Tf "
                            f"{self._pt(x + dx, base)} Td ({self._encode(line)}) Tj ET")

    # --- zapis
    def write(self, path: Path, title: str = "Mapa") -> None:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        def stream(data: bytes, extra: str = "") -> bytes:
            packed = zlib.compress(data, 6)
            return (f"<< /Length {len(packed)} /Filter /FlateDecode {extra}>>\nstream\n".encode()
                    + packed + b"\nendstream")

        content = "\n".join(self.ops).encode("latin-1")
        diffs = " ".join(f"{code} /{GLYPH_NAMES.get(ch, f'uni{ord(ch):04X}')}"
                         for ch, code in sorted(self.chars.items(), key=lambda kv: kv[1]))
        encoding = f"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [{diffs}] >>" if diffs \
            else "/WinAnsiEncoding"
        if self.font:
            f = self.font
            fontfile = add(stream(f.data, f"/Length1 {len(f.data)} "))
            scale = 1000 / f.units
            bbox = " ".join(str(int(v * scale)) for v in f.bbox)
            descriptor = add((f"<< /Type /FontDescriptor /FontName /{f.name} /Flags 32 /FontBBox [{bbox}] "
                              f"/ItalicAngle 0 /Ascent {int(f.ascent * scale)} /Descent {int(f.descent * scale)} "
                              f"/CapHeight {int(f.ascent * scale * 0.72)} /StemV 80 /FontFile2 {fontfile} 0 R >>")
                             .encode())
            reverse = {code: ch for ch, code in self.chars.items()}
            widths = []
            for code in range(32, 256):
                ch = chr(code) if code < 128 else reverse.get(code, " ")
                widths.append(str(int(round(f.width(ch)))))
            font_obj = add((f"<< /Type /Font /Subtype /TrueType /BaseFont /{f.name} /FirstChar 32 /LastChar 255 "
                            f"/Widths [{' '.join(widths)}] /FontDescriptor {descriptor} 0 R "
                            f"/Encoding {encoding} >>").encode())
        else:
            font_obj = add(f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding {encoding} >>".encode())
        content_obj = add(stream(content))
        pages_id = len(objects) + 2
        page_obj = add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {self._num(self.width)} "
                        f"{self._num(self.height)}] /Resources << /Font << /F1 {font_obj} 0 R >> >> "
                        f"/Contents {content_obj} 0 R >>").encode())
        add(f"<< /Type /Pages /Kids [{page_obj} 0 R] /Count 1 >>".encode())
        safe_title = title.encode("utf-16-be")
        info = add(b"<< /Title <FEFF" + safe_title.hex().upper().encode() + b"> /Producer (PSIP GIS Tkinter) "
                   + f"/CreationDate (D:{dt.datetime.now():%Y%m%d%H%M%S}) >>".encode())
        catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        Path(path).write_bytes(bytes(out))


# ------------------------------------------------------------------ eksport plotna
def _color(canvas, value):
    if not value:
        return None
    try:
        r, g, b = canvas.winfo_rgb(value)
    except Exception:  # noqa: BLE001
        return None
    return r // 256, g // 256, b // 256


def _font_px(canvas, spec) -> float:
    try:
        font = tkfont.Font(root=canvas, font=spec)
        size = font.actual("size")
    except Exception:  # noqa: BLE001
        return 12.0
    return float(-size) if size < 0 else size * canvas.winfo_fpixels("1p")


def draw_canvas(page: PdfPage, canvas, x: float, y: float, w: float, h: float) -> float:
    """Rysuje zawartosc plotna w prostokacie strony. Zwraca skale (pt na piksel)."""
    cw, ch = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
    s = min(w / cw, h / ch)
    ox, oy = x + (w - cw * s) / 2, y + (h - ch * s) / 2
    page.save()
    page.clip_rect(x, y, w, h)
    page.rect(x, y, w, h, fill=_color(canvas, canvas.cget("background")))

    def tr(coords):
        return [(ox + coords[i] * s, oy + coords[i + 1] * s) for i in range(0, len(coords) - 1, 2)]

    for item in canvas.find_all():
        if canvas.itemcget(item, "state") == "hidden" or "noexport" in canvas.gettags(item):
            continue
        kind = canvas.type(item)
        coords = canvas.coords(item)
        if kind == "polygon":
            fill = _color(canvas, canvas.itemcget(item, "fill"))
            outline = _color(canvas, canvas.itemcget(item, "outline"))
            width = float(canvas.itemcget(item, "width") or 1) * s
            page.path(tr(coords), fill, outline if width > 0 else None, width)
        elif kind == "line":
            dash = canvas.itemcget(item, "dash")
            pattern = [float(d) * s for d in dash.split()] if dash and dash.replace(" ", "").isdigit() else None
            page.path(tr(coords), stroke=_color(canvas, canvas.itemcget(item, "fill")),
                      width=float(canvas.itemcget(item, "width") or 1) * s, closed=False, dash=pattern)
        elif kind == "rectangle":
            (x0, y0), (x1, y1) = tr(coords)
            page.rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0),
                      _color(canvas, canvas.itemcget(item, "fill")),
                      _color(canvas, canvas.itemcget(item, "outline")),
                      float(canvas.itemcget(item, "width") or 1) * s)
        elif kind == "oval":
            (x0, y0), (x1, y1) = tr(coords)
            page.ellipse(x0, y0, x1, y1, _color(canvas, canvas.itemcget(item, "fill")),
                         _color(canvas, canvas.itemcget(item, "outline")),
                         float(canvas.itemcget(item, "width") or 1) * s)
        elif kind == "text":
            (tx, ty), = tr(coords)
            page.text(tx, ty, canvas.itemcget(item, "text"), _font_px(canvas, canvas.itemcget(item, "font")) * s,
                      _color(canvas, canvas.itemcget(item, "fill")) or (0, 0, 0),
                      canvas.itemcget(item, "anchor") or "center")
    page.restore()
    page.rect(x, y, w, h, stroke=(60, 60, 60), width=0.8)
    return s


def export_map(path, canvas, *, title: str, legend: list, footer: list[str], paper: str = "A4",
               landscape: bool = True, include_legend: bool = True, ground_m_per_px: float | None = None,
               subtitle: str = "") -> str:
    """legend: [(tytul_warstwy, [(rodzaj, wypelnienie, obrys, opis), ...]), ...]. Zwraca opis skali."""
    pw, ph = PAGE_SIZES.get(paper, PAGE_SIZES["A4"])
    if landscape:
        pw, ph = ph, pw
    page = PdfPage(pw, ph)
    m = 28.0
    page.text(m, m, title or "Mapa", 18, (15, 23, 42), "nw")
    if subtitle:
        page.text(m, m + 24, subtitle, 9.5, (71, 85, 105), "nw")
    top = m + (42 if subtitle else 30)
    legend_w = 190.0 if include_legend and legend else 0.0
    map_w = pw - 2 * m - (legend_w + 12 if legend_w else 0)
    map_h = ph - top - m - 30
    s = draw_canvas(page, canvas, m, top, map_w, map_h)
    scale_text = ""
    if ground_m_per_px:
        paper_m_per_px = s / 72 * 0.0254
        scale_text = f"Skala wydruku 1 : {_nice_int(ground_m_per_px / paper_m_per_px)}"
    if legend_w:
        lx, ly = m + map_w + 12, top
        page.rect(lx, ly, legend_w, map_h, fill=(255, 255, 255), stroke=(203, 213, 225), width=0.6)
        page.text(lx + 10, ly + 8, "Legenda", 12, (15, 23, 42))
        cy = ly + 30
        for layer_title, entries in legend:
            if cy > ly + map_h - 24:
                page.text(lx + 10, cy, "…", 9, (71, 85, 105))
                break
            page.text(lx + 10, cy, _short(page, layer_title, 9.5, legend_w - 20), 9.5, (15, 23, 42))
            cy += 15
            for kind, fill, outline, label in entries:
                if cy > ly + map_h - 14:
                    break
                fill, outline = _hex_rgb(fill), _hex_rgb(outline)
                fx, fy = lx + 14, cy
                if kind == "point":
                    page.ellipse(fx + 2, fy + 1, fx + 12, fy + 11, fill, outline, 0.6)
                elif kind == "line":
                    page.path([(fx, fy + 6), (fx + 14, fy + 6)], stroke=fill or outline, width=2, closed=False)
                else:
                    page.rect(fx, fy, 14, 11, fill, outline or (100, 116, 139), 0.5)
                page.text(fx + 20, fy + 5.5, _short(page, label, 8.5, legend_w - 44), 8.5, (51, 65, 85), "w")
                cy += 14
            cy += 8
    lines = ([scale_text] if scale_text else []) + list(footer)
    page.text(m, ph - m - 18, "   |   ".join(lines), 8.5, (71, 85, 105), "nw")
    page.write(Path(path), title or "Mapa")
    return scale_text


def _hex_rgb(color):
    """'#rrggbb' -> (r, g, b); pusty kolor -> None (brak wypelnienia)."""
    if not color:
        return None
    if isinstance(color, tuple):
        return color
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def _nice_int(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def _short(page: PdfPage, text: str, size: float, max_w: float) -> str:
    text = str(text)
    if page.text_width(text, size) <= max_w:
        return text
    while text and page.text_width(text + "…", size) > max_w:
        text = text[:-1]
    return text + "…"


def nice_distance(meters: float) -> str:
    return f"{meters / 1000:.3f} km" if meters >= 1000 else f"{meters:.2f} m"


def nice_step(value: float) -> float:
    """Zaokraglenie do 1/2/5 x 10^n (np. dlugosc podzialki)."""
    if value <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(value))
    for mult in (1, 2, 5, 10):
        if mult * exp >= value:
            return mult * exp
    return 10 * exp
