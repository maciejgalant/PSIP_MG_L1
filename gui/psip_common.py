"""Wspolne elementy GUI PSIP: Docker Compose, zapytania do PostGIS, klasy IOP.

Tylko biblioteka standardowa Pythona (Tkinter) - na hoscie nie trzeba nic instalowac.
Baza i skrypty dzialaja w kontenerach z docker-compose-local.yml.
"""
from __future__ import annotations

import csv
import queue
import re
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose-local.yml"
WEIGHTS_FILE = ROOT / "app" / "iop_weights.csv"
WEIGHT_FIELDS = ["kryterium", "waga", "klasa_bdot", "uzasadnienie"]

COMPOSE_CMD = ["docker", "compose", "-f", str(COMPOSE)]
PSQL_CMD = COMPOSE_CMD + ["exec", "-T", "postgis", "psql", "-X", "-A", "-t",
                          "-P", "pager=off", "-U", "psip", "-d", "psip"]

# Te same przedzialy i kolory co w app/compute_iop.py i app/iop_style.qml.
CLASS_EDGES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
CLASS_LABELS = ["bardzo trudna", "trudna", "przeciętna", "dobra", "bardzo dobra"]
CLASS_COLORS = ["#d7191c", "#eb8c8d", "#ffffff", "#8ccaa0", "#1a9641"]

IDENT = re.compile(r"[a-z][a-z0-9_]*")
H3_ID = re.compile(r"[0-9a-f]{15}")


def compose_python(script: str) -> list[str]:
    """Polecenie uruchamiajace skrypt z app/ w kontenerze python."""
    return COMPOSE_CMD + ["exec", "-T", "python", "python", f"/app/{script}"]


def run_sql(*queries: str, timeout: int = 180) -> list[str]:
    """Wykonuje zapytania psql (kazde jako osobne -c) i zwraca niepuste linie wyniku."""
    command = list(PSQL_CMD)
    for query in queries:
        command += ["-c", query]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Blad psql")
    return [line for line in result.stdout.splitlines() if line.strip()]


def iop_class(value: float) -> int:
    """Numer klasy 1..5 dla iop_norm (jak np.digitize w compute_iop.py)."""
    for index, edge in enumerate(CLASS_EDGES[1:-1], start=1):
        if value < edge:
            return index
    return len(CLASS_LABELS)


def read_weights(path: Path = WEIGHTS_FILE) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if not IDENT.fullmatch(row.get("kryterium", "")):
            raise ValueError(f"Nieprawidlowe kryterium w {path.name}: {row.get('kryterium')!r}")
        value = float(row["waga"])
        if not -1.0 <= value <= 1.0:
            raise ValueError(f"{row['kryterium']}: waga {value} poza zakresem -1..1")
    return rows


def write_weights(rows: list[dict[str, str]], path: Path = WEIGHTS_FILE) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WEIGHT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in WEIGHT_FIELDS})
    tmp.replace(path)


class CommandRunner:
    """Uruchamia polecenie w watku; linie i wynik trafiaja do kolejki okna.

    Komunikaty: ("log", linia), ("done", (sukces, tytul, linie)).
    """

    def __init__(self, messages: "queue.Queue[tuple[str, object]]") -> None:
        self.messages = messages
        self.busy = False

    def start(self, title: str, command: list[str]) -> bool:
        if self.busy:
            return False
        self.busy = True
        self.messages.put(("log", "> " + " ".join(command)))

        def worker() -> None:
            lines: list[str] = []
            try:
                process = subprocess.Popen(
                    command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, stdin=subprocess.DEVNULL)
                assert process.stdout is not None
                for line in process.stdout:
                    lines.append(line.rstrip())
                    self.messages.put(("log", line.rstrip()))
                code = process.wait()
                message = f"{title}: zakończono" if code == 0 else f"{title}: kod błędu {code}"
                self.messages.put(("done", (code == 0, message, lines)))
            except Exception as error:  # noqa: BLE001 - pokazujemy kazdy blad w GUI
                self.messages.put(("done", (False, f"{title}: {error}", lines)))
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()
        return True
