"""Desktop control panel for PSIP LAB 3 (run from repository root on Windows).

Requires Python with Tkinter and Docker Desktop / Docker Compose available in PATH.
No third-party Python packages are needed on the host.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose-local.yml"
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


class Lab3App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PSIP LAB 3 | H3 i BDOT10k")
        self.geometry("900x630")
        self.minsize(730, 500)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.status = tk.StringVar(value="Gotowy do sprawdzenia środowiska")
        self.counts = {str(r): tk.StringVar(value="—") for r in (7, 8, 9)}
        self.results = {str(r): tk.StringVar(value="—") for r in (7, 8, 9)}
        self.columns = tk.StringVar(value="—")
        self._build_ui()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        theme = ttk.Style(self)
        if "clam" in theme.theme_names():
            theme.theme_use("clam")
        self.configure(bg="#f3f6fa")
        container = ttk.Frame(self, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="PSIP LAB 3", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(container, text="PostGIS • H3 7/8/9 • Agregacja BDOT10k",
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 16))

        controls = ttk.LabelFrame(container, text="Sterowanie", padding=12)
        controls.pack(fill="x")
        buttons = ttk.Frame(controls)
        buttons.pack(fill="x")
        self.btn_start = ttk.Button(buttons, text="Uruchom kontenery", command=self.start_containers)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_check = ttk.Button(buttons, text="Sprawdź bazę", command=self.check_database)
        self.btn_check.pack(side="left", padx=(0, 8))
        self.btn_aggregate = ttk.Button(buttons, text="Agreguj BDOT → H3", command=self.aggregate)
        self.btn_aggregate.pack(side="left")
        ttk.Label(controls, textvariable=self.status).pack(anchor="w", pady=(10, 0))

        overview = ttk.LabelFrame(container, text="PostGIS / grid", padding=12)
        overview.pack(fill="x", pady=12)
        headers = ("Rozdzielczość", "Siatka H3", "Wynik agregacji")
        for col, title in enumerate(headers):
            ttk.Label(overview, text=title, font=("Segoe UI", 10, "bold")).grid(
                row=0, column=col, sticky="w", padx=14, pady=4)
        for row, res in enumerate(("7", "8", "9"), start=1):
            ttk.Label(overview, text=f"H3 {res}").grid(row=row, column=0, sticky="w", padx=14, pady=3)
            ttk.Label(overview, textvariable=self.counts[res]).grid(row=row, column=1, sticky="w", padx=14)
            ttk.Label(overview, textvariable=self.results[res]).grid(row=row, column=2, sticky="w", padx=14)
        ttk.Label(overview, text="Kolumny tabeli wynikowej:").grid(row=4, column=0, sticky="w", padx=14, pady=6)
        ttk.Label(overview, textvariable=self.columns).grid(row=4, column=1, sticky="w", padx=14)
        for i in range(3):
            overview.columnconfigure(i, weight=1)

        logs = ttk.LabelFrame(container, text="Dziennik operacji", padding=8)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, height=14, wrap="word", font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(container, text="Operacje wykonują się lokalnie przez Docker Compose. Nie uruchamiaj dwóch agregacji jednocześnie.").pack(
            anchor="w", pady=(10, 0))

    def _append(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        for button in (self.btn_start, self.btn_check, self.btn_aggregate):
            button.configure(state=state)

    def _run(self, title: str, command: list[str], parse_status: bool = False) -> None:
        if self.busy:
            return
        if not COMPOSE.is_file():
            messagebox.showerror("Brak pliku", f"Nie znaleziono {COMPOSE}")
            return
        if not shutil.which("docker"):
            messagebox.showerror("Docker", "Polecenie docker nie jest dostępne w PATH.")
            return
        self._set_busy(True)
        self.status.set(title)
        self.messages.put(("log", "> " + " ".join(command)))

        def worker() -> None:
            try:
                process = subprocess.Popen(
                    command, cwd=str(ROOT), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, stdin=subprocess.DEVNULL,
                )
                lines = []
                assert process.stdout is not None
                for line in process.stdout:
                    clean = line.rstrip()
                    lines.append(clean)
                    self.messages.put(("log", clean))
                code = process.wait()
                if code:
                    self.messages.put(("done", (False, f"{title}: kod błędu {code}")))
                else:
                    if parse_status:
                        self.messages.put(("stats", lines))
                    self.messages.put(("done", (True, f"{title}: zakończono")))
            except Exception as error:
                self.messages.put(("done", (False, f"{title}: {error}")))

        threading.Thread(target=worker, daemon=True).start()

    def start_containers(self) -> None:
        self._run("Uruchamianie kontenerów",
                  ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--no-build"])

    def check_database(self) -> None:
        self._run("Kontrola bazy",
                  ["docker", "compose", "-f", str(COMPOSE), "exec", "-T",
                   "postgis", "psql", "-X", "-A", "-t", "-P", "pager=off",
                   "-U", "psip", "-d", "psip", "-c", QUERY], parse_status=True)

    def aggregate(self) -> None:
        if not messagebox.askyesno(
            "Uruchomić agregację?",
            "Przeliczyć warstwy BDOT10k dla wszystkich trzech rozdzielczości H3?\n"
            "Operacja może potrwać długo i zmieni wynik w PostGIS.",
        ):
            return
        self._run("Agregacja BDOT10k",
                  ["docker", "compose", "-f", str(COMPOSE), "exec", "-T",
                   "python", "python", "/app/aggregate_h3.py"])

    def _poll(self) -> None:
        try:
            while True:
                action, value = self.messages.get_nowait()
                if action == "log":
                    self._append(str(value))
                elif action == "stats":
                    self._parse_stats(value)
                elif action == "done":
                    success, message = value
                    self._append(message)
                    self.status.set(message)
                    self._set_busy(False)
                    if not success:
                        messagebox.showerror("Błąd operacji", message)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _parse_stats(self, lines: object) -> None:
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


if __name__ == "__main__":
    Lab3App().mainloop()
