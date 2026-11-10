"""Panel sterowania PSIP LAB 3-4 (uruchamiaj z katalogu repozytorium na Windows).

    py gui/lab3_app.py

Wymaga Pythona z Tkinter oraz Docker Desktop / Docker Compose w PATH.
Na hoscie nie sa potrzebne dodatkowe pakiety Pythona.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from psip_common import (CLASS_COLORS, CLASS_LABELS, COMPOSE, COMPOSE_CMD, PSQL_CMD, ROOT,
                         CommandRunner, compose_python)

QUERY = """
SELECT 'H3|' || resolution || '|' || COUNT(*) FROM grid.h3_grid
GROUP BY resolution
UNION ALL
SELECT 'RESULT|' || resolution || '|' || COUNT(*) FROM grid.h3_aggregation
GROUP BY resolution
UNION ALL
SELECT 'COLUMNS|' || COUNT(*) || '|0' FROM information_schema.columns
WHERE table_schema='grid' AND table_name='h3_aggregation'
ORDER BY 1;
"""
# Osobne zapytanie: gdy grid.h3_iop jeszcze nie istnieje, psql zglosi blad tylko dla niego.
IOP_QUERY = """
SELECT 'IOP|' || resolution || '|' || string_agg(iop_klasa || ':' || n, ',' ORDER BY iop_klasa)
FROM (SELECT resolution, iop_klasa, COUNT(*) AS n FROM grid.h3_iop GROUP BY 1, 2) s
GROUP BY resolution ORDER BY 1;
"""


class Lab3App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PSIP LAB 3–4 | H3, BDOT10k, przejezdność")
        self.geometry("1000x720")
        self.minsize(820, 560)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.runner = CommandRunner(self.messages)
        self.after_success = None
        self.parse_status = False
        self.status = tk.StringVar(value="Gotowy do sprawdzenia środowiska")
        self.counts = {str(r): tk.StringVar(value="—") for r in (7, 8, 9)}
        self.results = {str(r): tk.StringVar(value="—") for r in (7, 8, 9)}
        self.iop = {str(r): [tk.StringVar(value="—") for _ in range(5)] for r in (7, 8, 9)}
        self.columns = tk.StringVar(value="—")
        self.map_window = None
        self.weights_window = None
        self._build_ui()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        theme = ttk.Style(self)
        if "clam" in theme.theme_names():
            theme.theme_use("clam")
        self.configure(bg="#f3f6fa")
        container = ttk.Frame(self, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="PSIP LAB 3–4", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(container, text="PostGIS • H3 7/8/9 • Agregacja BDOT10k • Indeks przejezdności (IOP)",
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 14))

        controls = ttk.LabelFrame(container, text="Sterowanie", padding=12)
        controls.pack(fill="x")
        row1 = ttk.Frame(controls)
        row1.pack(fill="x")
        ttk.Label(row1, text="LAB 3", width=7, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.btn_start = ttk.Button(row1, text="Uruchom kontenery", command=self.start_containers)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_check = ttk.Button(row1, text="Sprawdź bazę", command=self.check_database)
        self.btn_check.pack(side="left", padx=(0, 8))
        self.btn_aggregate = ttk.Button(row1, text="Agreguj BDOT → H3", command=self.aggregate)
        self.btn_aggregate.pack(side="left")
        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="LAB 4", width=7, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.btn_iop = ttk.Button(row2, text="Oblicz IOP", command=self.compute_iop)
        self.btn_iop.pack(side="left", padx=(0, 8))
        self.btn_png = ttk.Button(row2, text="Mapy PNG", command=self.plot_maps)
        self.btn_png.pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="Mapa H3 / IOP", command=self.open_map).pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="Wagi IVRF", command=self.open_weights).pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="Aplikacja GIS", command=self.open_gis).pack(side="left")
        ttk.Label(controls, textvariable=self.status).pack(anchor="w", pady=(10, 0))

        overview = ttk.LabelFrame(container, text="PostGIS / grid", padding=12)
        overview.pack(fill="x", pady=12)
        headers = ["Rozdzielczość", "Siatka H3", "Wynik agregacji"] + [f"IOP {i + 1}" for i in range(5)]
        for col, title in enumerate(headers):
            ttk.Label(overview, text=title, font=("Segoe UI", 10, "bold")).grid(
                row=0, column=col, sticky="w", padx=10, pady=4)
        for i, label in enumerate(CLASS_LABELS):
            swatch = tk.Label(overview, text=label, bg=CLASS_COLORS[i], relief="solid", bd=1,
                              font=("Segoe UI", 8), padx=4)
            swatch.grid(row=1, column=3 + i, sticky="we", padx=10)
        for row, res in enumerate(("7", "8", "9"), start=2):
            ttk.Label(overview, text=f"H3 {res}").grid(row=row, column=0, sticky="w", padx=10, pady=3)
            ttk.Label(overview, textvariable=self.counts[res]).grid(row=row, column=1, sticky="w", padx=10)
            ttk.Label(overview, textvariable=self.results[res]).grid(row=row, column=2, sticky="w", padx=10)
            for i in range(5):
                ttk.Label(overview, textvariable=self.iop[res][i]).grid(row=row, column=3 + i, sticky="e", padx=10)
        ttk.Label(overview, text="Kolumny tabeli wynikowej:").grid(row=5, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(overview, textvariable=self.columns).grid(row=5, column=1, sticky="w", padx=10)
        for i in range(len(headers)):
            overview.columnconfigure(i, weight=1)

        logs = ttk.LabelFrame(container, text="Dziennik operacji", padding=8)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, height=14, wrap="word", font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(container, text="Operacje wykonują się lokalnie przez Docker Compose (docker-compose-local.yml). "
                                  "Nie uruchamiaj dwóch obliczeń jednocześnie.").pack(anchor="w", pady=(10, 0))

    def _append(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, value: bool) -> None:
        state = "disabled" if value else "normal"
        for button in (self.btn_start, self.btn_check, self.btn_aggregate, self.btn_iop, self.btn_png):
            button.configure(state=state)

    def _run(self, title: str, command: list[str], parse_status: bool = False, after_success=None) -> None:
        if self.runner.busy:
            return
        if not COMPOSE.is_file():
            messagebox.showerror("Brak pliku", f"Nie znaleziono {COMPOSE}")
            return
        if not shutil.which("docker"):
            messagebox.showerror("Docker", "Polecenie docker nie jest dostępne w PATH.")
            return
        self.parse_status = parse_status
        self.after_success = after_success
        self._set_busy(True)
        self.status.set(title)
        self.runner.start(title, command)

    def start_containers(self) -> None:
        self._run("Uruchamianie kontenerów", COMPOSE_CMD + ["up", "-d", "--no-build"])

    def check_database(self) -> None:
        self._run("Kontrola bazy", PSQL_CMD + ["-c", QUERY, "-c", IOP_QUERY], parse_status=True)

    def aggregate(self) -> None:
        if not messagebox.askyesno(
            "Uruchomić agregację?",
            "Przeliczyć warstwy BDOT10k dla wszystkich trzech rozdzielczości H3?\n"
            "Operacja może potrwać długo i zmieni wynik w PostGIS.",
        ):
            return
        self._run("Agregacja BDOT10k", compose_python("aggregate_h3.py"))

    def compute_iop(self) -> None:
        self._run("Obliczenie IOP", compose_python("compute_iop.py"), after_success=self.check_database)

    def plot_maps(self) -> None:
        self._run("Mapy PNG", compose_python("plot_iop.py"), after_success=self._open_output)

    def _open_output(self) -> None:
        folder = ROOT / "app" / "output"
        self._append(f"Mapy zapisane w: {folder}")
        if hasattr(os, "startfile") and folder.is_dir():
            os.startfile(folder)  # Windows: otworz folder w Eksploratorze

    def open_map(self) -> None:
        from h3_map import H3Map
        if self.map_window and self.map_window.winfo_exists():
            self.map_window.lift()
            return
        self.map_window = H3Map(self)

    def open_weights(self) -> None:
        from weights_editor import WeightsEditor
        if self.weights_window and self.weights_window.winfo_exists():
            self.weights_window.lift()
            return
        self.weights_window = WeightsEditor(self, on_saved=self.check_database)

    def open_gis(self) -> None:
        """Aplikacja GIS (logowanie, mapa, SHP, analiza wag, PDF) w osobnym procesie."""
        script = ROOT / "gui" / "gis_app.py"
        subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
        self._append(f"Uruchomiono {script.relative_to(ROOT)}")

    def _poll(self) -> None:
        try:
            while True:
                action, value = self.messages.get_nowait()
                if action == "log":
                    self._append(str(value))
                elif action == "done":
                    success, message, lines = value
                    self._append(message)
                    self.status.set(message)
                    self._set_busy(False)
                    if success and self.parse_status:
                        self._parse_stats(lines)
                    if success and self.after_success:
                        callback, self.after_success = self.after_success, None
                        self.after(50, callback)
                    if not success:
                        messagebox.showerror("Błąd operacji", message)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _parse_stats(self, lines) -> None:
        iop_seen = False
        for line in lines:
            parts = line.strip().split("|")
            if len(parts) != 3:
                continue
            group, key, val = parts
            if group == "H3" and key in self.counts:
                self.counts[key].set(val)
            elif group == "RESULT" and key in self.results:
                self.results[key].set(val)
            elif group == "COLUMNS":
                self.columns.set(key)
            elif group == "IOP" and key in self.iop:
                iop_seen = True
                counts = dict(item.split(":") for item in val.split(",") if ":" in item)
                for i in range(5):
                    self.iop[key][i].set(counts.get(str(i + 1), "0"))
        if not iop_seen:
            for res in self.iop:
                for var in self.iop[res]:
                    var.set("—")
            self._append("Brak grid.h3_iop – kliknij „Oblicz IOP”.")


if __name__ == "__main__":
    Lab3App().mainloop()
