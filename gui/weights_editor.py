"""Edytor wag IVRF (app/iop_weights.csv) z ponownym obliczeniem IOP (LAB 4).

Uruchom z katalogu repozytorium: py gui/weights_editor.py
Plik app/iop_weights.csv jest montowany do kontenera python (docker-compose-local.yml),
wiec "Zapisz i przelicz" od razu liczy IOP na nowych wagach.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from psip_common import CommandRunner, WEIGHTS_FILE, compose_python, read_weights, write_weights

GROUPS = {
    "wszystkie": "",
    "SK – sieć komunikacyjna": "sk",
    "PT – pokrycie terenu": "pt",
    "SW – sieć wodna": "sw",
    "BU – budynki i budowle": "bu",
    "KU – kompleksy użytkowania": "ku",
    "OI – obiekty inne": "oi",
    "SU – uzbrojenie terenu": "su",
    "AD / TC – administracja, ochrona": ("ad", "tc"),
}


def weight_color(value: float) -> str:
    if value > 0:
        return "#15803d"
    if value < 0:
        return "#b91c1c"
    return "#475569"


class WeightsEditor(tk.Toplevel):
    def __init__(self, master, on_saved=None):
        super().__init__(master)
        self.title("PSIP LAB 4 | Wagi IVRF")
        self.geometry("1040x720")
        self.minsize(820, 480)
        self.on_saved = on_saved
        self.messages: queue.Queue = queue.Queue()
        self.runner = CommandRunner(self.messages)
        self.rows: list[dict[str, str]] = []
        self.original: dict[str, float] = {}
        self.values: dict[str, tk.DoubleVar] = {}
        self.notes: dict[str, tk.StringVar] = {}
        self.widgets: dict[str, tuple] = {}
        self.group = tk.StringVar(value="wszystkie")
        self.search = tk.StringVar()
        self.status = tk.StringVar()
        self._build()
        self.reload()
        self.after(100, self._poll)

    def _build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Wagi IVRF", font=("Segoe UI", 16, "bold")).pack(side="left", padx=(0, 16))
        ttk.Label(top, text="Grupa").pack(side="left")
        box = ttk.Combobox(top, textvariable=self.group, values=list(GROUPS), state="readonly", width=30)
        box.pack(side="left", padx=(4, 12))
        box.bind("<<ComboboxSelected>>", lambda e: self._filter())
        ttk.Label(top, text="Szukaj").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search, width=20)
        entry.pack(side="left", padx=4)
        self.search.trace_add("write", lambda *a: self._filter())
        ttk.Label(self, text="−1 = utrudnia przejazd (np. woda), 0 = brak wpływu, +1 = ułatwia (np. droga). "
                             "Zmienione wiersze są pogrubione.", padding=(12, 0)).pack(anchor="w")

        frame = ttk.Frame(self, padding=(12, 6))
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, highlightthickness=0, background="#f8fafc")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

        bottom = ttk.Frame(self, padding=(12, 4, 12, 10))
        bottom.pack(fill="x")
        self.btn_save = ttk.Button(bottom, text="Zapisz", command=self.save)
        self.btn_save.pack(side="left")
        self.btn_compute = ttk.Button(bottom, text="Zapisz i przelicz IOP", command=self.save_and_compute)
        self.btn_compute.pack(side="left", padx=8)
        ttk.Button(bottom, text="Cofnij zmiany", command=self.reload).pack(side="left")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", padx=16)

        logs = ttk.LabelFrame(self, text="Wynik obliczenia", padding=6)
        logs.pack(fill="x", padx=12, pady=(0, 10))
        self.log = tk.Text(logs, height=9, wrap="none", font=("Consolas", 9), state="disabled")
        self.log.pack(fill="x")

    def _wheel(self, event):
        if str(event.widget).startswith(str(self.canvas)) or str(event.widget).startswith(str(self.inner)):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ---------- dane ----------
    def reload(self):
        try:
            self.rows = read_weights()
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Wagi", f"Nie można wczytać {WEIGHTS_FILE}:\n{error}", parent=self)
            return
        for child in self.inner.winfo_children():
            child.destroy()
        self.values, self.notes, self.widgets = {}, {}, {}
        self.original = {row["kryterium"]: float(row["waga"]) for row in self.rows}
        headers = ("Kryterium", "Klasa BDOT10k", "Waga IVRF", "", "Uzasadnienie")
        for col, text in enumerate(headers):
            ttk.Label(self.inner, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=col, sticky="w", padx=6, pady=4)
        for index, row in enumerate(self.rows, start=1):
            name = row["kryterium"]
            var = tk.DoubleVar(value=float(row["waga"]))
            note = tk.StringVar(value=row.get("uzasadnienie", ""))
            self.values[name], self.notes[name] = var, note
            label = tk.Label(self.inner, text=name, anchor="w", font=("Consolas", 9), bg="#f8fafc")
            klass = ttk.Label(self.inner, text=row.get("klasa_bdot", ""), width=34)
            scale = ttk.Scale(self.inner, from_=-1.0, to=1.0, variable=var, length=220,
                              command=lambda v, n=name: self._changed(n))
            value = tk.Label(self.inner, width=6, anchor="e", font=("Consolas", 10, "bold"), bg="#f8fafc")
            entry = ttk.Entry(self.inner, textvariable=note, width=48)
            self.widgets[name] = (label, klass, scale, value, entry)
            self._changed(name)
        self._filter()
        self.status.set(f"{len(self.rows)} kryteriów z {WEIGHTS_FILE.name}")

    def _changed(self, name):
        var = self.values[name]
        rounded = round(var.get() * 20) / 20  # krok 0,05
        if abs(rounded - var.get()) > 1e-9:
            var.set(rounded)
        label, _klass, _scale, value, _entry = self.widgets[name]
        value.configure(text=f"{rounded:+.2f}", fg=weight_color(rounded))
        changed = abs(rounded - self.original[name]) > 1e-9
        label.configure(font=("Consolas", 9, "bold" if changed else "normal"))

    def _filter(self):
        prefix = GROUPS.get(self.group.get(), "")
        text = self.search.get().strip().lower()
        for row in self.rows:
            name = row["kryterium"]
            widgets = self.widgets[name]
            visible = (not prefix or name.startswith(prefix)) and (
                not text or text in name or text in row.get("klasa_bdot", "").lower())
            for col, widget in enumerate(widgets):
                if visible:
                    widget.grid(row=self.rows.index(row) + 1, column=col, sticky="w", padx=6, pady=1)
                else:
                    widget.grid_remove()
        self.canvas.yview_moveto(0)

    def _collect(self):
        rows = []
        for row in self.rows:
            name = row["kryterium"]
            value = self.values[name].get()
            # Niezmienione wagi zostaja w oryginalnym zapisie (czytelny diff w git).
            text = row["waga"] if abs(value - self.original[name]) < 1e-9 else f"{value:.2f}".replace("-0.00", "0.00")
            rows.append({**row, "waga": text,
                         "uzasadnienie": self.notes[name].get().replace("\n", " ").strip()})
        return rows

    def save(self) -> bool:
        rows = self._collect()
        if not any(float(r["waga"]) for r in rows):
            messagebox.showerror("Wagi", "Wszystkie wagi są zerowe – IOP nie miałby sensu.", parent=self)
            return False
        try:
            write_weights(rows)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Wagi", f"Zapis nie powiódł się:\n{error}", parent=self)
            return False
        changed = sum(abs(float(r["waga"]) - self.original[r["kryterium"]]) > 1e-9 for r in rows)
        self.rows = rows
        self.original = {r["kryterium"]: float(r["waga"]) for r in rows}
        for name in self.values:
            self._changed(name)
        self.status.set(f"Zapisano {WEIGHTS_FILE.name} (zmienione wagi: {changed})")
        return True

    def save_and_compute(self):
        if self.runner.busy or not self.save():
            return
        self._set_busy(True)
        self._clear_log()
        self.runner.start("Obliczenie IOP", compose_python("compute_iop.py"))

    # ---------- log ----------
    def _set_busy(self, busy):
        for button in (self.btn_save, self.btn_compute):
            button.configure(state="disabled" if busy else "normal")
        if busy:
            self.status.set("Liczenie IOP w kontenerze python...")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", str(value) + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "done":
                    success, message, _lines = value
                    self._set_busy(False)
                    self.status.set(message)
                    if success and self.on_saved:
                        self.on_saved()
                    if not success:
                        messagebox.showerror("Obliczenie IOP", message, parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    root = tk.Tk()
    root.withdraw()
    window = WeightsEditor(root)
    window.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
