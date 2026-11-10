"""Motywy jasny/ciemny (ttk) i bazowe okno modalne aplikacji GIS."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

THEMES = {
    "jasny": {
        "bg": "#f3f6fa", "panel": "#ffffff", "fg": "#0f172a", "muted": "#64748b", "border": "#cbd5e1",
        "field": "#ffffff", "button": "#e2e8f0", "button_active": "#cbd5e1", "accent": "#2563eb",
        "accent_fg": "#ffffff", "select": "#bfdbfe", "map_bg": "#eef2f7", "map_fg": "#1e293b",
        "map_box": "#ffffff", "error": "#b91c1c", "highlight": "#facc15", "measure": "#e11d48",
    },
    "ciemny": {
        "bg": "#0f172a", "panel": "#1e293b", "fg": "#e2e8f0", "muted": "#94a3b8", "border": "#334155",
        "field": "#0b1220", "button": "#334155", "button_active": "#475569", "accent": "#3b82f6",
        "accent_fg": "#ffffff", "select": "#1d4ed8", "map_bg": "#111827", "map_fg": "#e5e7eb",
        "map_box": "#1f2937", "error": "#f87171", "highlight": "#fde047", "measure": "#fb7185",
    },
}

FONT = ("Segoe UI", 9)


def apply_theme(root: tk.Misc, name: str) -> dict:
    t = THEMES[name]
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["field"],
                    bordercolor=t["border"], lightcolor=t["bg"], darkcolor=t["bg"], troughcolor=t["panel"],
                    selectbackground=t["select"], selectforeground=t["fg"], insertcolor=t["fg"], font=FONT)
    style.configure("TFrame", background=t["bg"])
    style.configure("Panel.TFrame", background=t["panel"])
    style.configure("TLabel", background=t["bg"], foreground=t["fg"])
    style.configure("Panel.TLabel", background=t["panel"], foreground=t["fg"])
    style.configure("Muted.TLabel", background=t["bg"], foreground=t["muted"])
    style.configure("PanelMuted.TLabel", background=t["panel"], foreground=t["muted"])
    style.configure("Error.TLabel", background=t["bg"], foreground=t["error"])
    style.configure("Title.TLabel", background=t["bg"], foreground=t["fg"], font=("Segoe UI", 16, "bold"))
    style.configure("H.TLabel", background=t["bg"], foreground=t["fg"], font=("Segoe UI", 10, "bold"))
    style.configure("PanelH.TLabel", background=t["panel"], foreground=t["fg"], font=("Segoe UI", 10, "bold"))
    style.configure("Status.TLabel", background=t["panel"], foreground=t["fg"], padding=(8, 3))
    style.configure("TLabelframe", background=t["bg"], bordercolor=t["border"])
    style.configure("TLabelframe.Label", background=t["bg"], foreground=t["fg"], font=("Segoe UI", 9, "bold"))
    style.configure("TButton", background=t["button"], foreground=t["fg"], padding=(8, 4), borderwidth=1)
    style.map("TButton", background=[("active", t["button_active"]), ("disabled", t["bg"])],
              foreground=[("disabled", t["muted"])])
    style.configure("Tool.TButton", padding=(7, 5))
    style.configure("Small.TButton", padding=(3, 0))
    style.configure("Accent.TButton", background=t["accent"], foreground=t["accent_fg"])
    style.map("Accent.TButton", background=[("active", t["accent"]), ("disabled", t["button"])])
    style.configure("Active.TButton", background=t["accent"], foreground=t["accent_fg"])
    style.map("Active.TButton", background=[("active", t["accent"])])
    for widget in ("TCheckbutton", "TRadiobutton", "Panel.TCheckbutton"):
        bg = t["panel"] if widget.startswith("Panel") else t["bg"]
        style.configure(widget, background=bg, foreground=t["fg"], indicatorbackground=t["field"],
                        indicatorforeground=t["accent"], upperbordercolor=t["muted"], lowerbordercolor=t["muted"])
        style.map(widget, background=[("active", bg)],
                  indicatorbackground=[("pressed", t["button"]), ("selected", t["field"])],
                  indicatorforeground=[("selected", t["accent"])])
    style.configure("TEntry", fieldbackground=t["field"], foreground=t["fg"])
    style.configure("TSpinbox", fieldbackground=t["field"], foreground=t["fg"], arrowcolor=t["fg"])
    style.configure("TCombobox", fieldbackground=t["field"], foreground=t["fg"], arrowcolor=t["fg"],
                    background=t["button"])
    style.map("TCombobox", fieldbackground=[("readonly", t["field"])], foreground=[("readonly", t["fg"])],
              selectbackground=[("readonly", t["field"])], selectforeground=[("readonly", t["fg"])])
    style.configure("Treeview", background=t["field"], fieldbackground=t["field"], foreground=t["fg"],
                    bordercolor=t["border"], rowheight=21)
    style.map("Treeview", background=[("selected", t["select"])], foreground=[("selected", t["fg"])])
    style.configure("Treeview.Heading", background=t["button"], foreground=t["fg"], font=("Segoe UI", 9, "bold"))
    style.configure("Horizontal.TScale", background=t["bg"], troughcolor=t["panel"])
    style.configure("TScrollbar", background=t["button"], troughcolor=t["bg"], arrowcolor=t["fg"])
    style.configure("TPanedwindow", background=t["bg"])
    style.configure("TNotebook", background=t["bg"])
    style.configure("TNotebook.Tab", background=t["button"], foreground=t["fg"])
    root.option_add("*TCombobox*Listbox.background", t["field"])
    root.option_add("*TCombobox*Listbox.foreground", t["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", t["select"])
    return t


class Modal(tk.Toplevel):
    """Okno modalne: blokuje glowne okno (grab), wysrodkowane, Esc zamyka."""

    def __init__(self, master, title: str, theme: dict, width: int | None = None, height: int | None = None,
                 resizable: bool = False) -> None:
        super().__init__(master)
        self.theme = theme
        self.withdraw()
        self.title(title)
        self.configure(bg=theme["bg"])
        self.transient(master.winfo_toplevel())
        self.resizable(resizable, resizable)
        self.result = None
        self.buttons = ttk.Frame(self, padding=(16, 0, 16, 14))
        self.buttons.pack(fill="x", side="bottom")   # najpierw przyciski - zawsze widoczne
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._size = (width, height)

    def show(self, wait: bool = True):
        """Wyswietla modal; wait=True czeka na zamkniecie i zwraca self.result."""
        self.update_idletasks()
        master = self.master.winfo_toplevel()
        w = self._size[0] or self.winfo_reqwidth()
        h = self._size[1] or self.winfo_reqheight()
        if getattr(self, "dock_right", False):   # np. analiza wag: mapa ma zostac widoczna obok
            x = master.winfo_rootx() + max(master.winfo_width() - w - 24, 0)
            y = master.winfo_rooty() + 60
        else:
            x = master.winfo_rootx() + max((master.winfo_width() - w) // 2, 0)
            y = master.winfo_rooty() + max((master.winfo_height() - h) // 3, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            self.after(50, self.grab_set)
        self.focus_set()
        if wait:
            self.wait_window()
        return self.result

    def add_button(self, text: str, command, accent: bool = False, side: str = "right") -> ttk.Button:
        button = ttk.Button(self.buttons, text=text, command=command,
                            style="Accent.TButton" if accent else "TButton")
        button.pack(side=side, padx=(6, 0) if side == "right" else (0, 6))
        return button

    def close(self, result=None) -> None:
        if result is not None:
            self.result = result
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def info_modal(master, theme, title: str, text: str) -> None:
    modal = Modal(master, title, theme)
    ttk.Label(modal.body, text=text, justify="left", wraplength=420).pack(anchor="w")
    modal.add_button("OK", modal.close, accent=True)
    modal.bind("<Return>", lambda e: modal.close())
    modal.show()


def confirm_modal(master, theme, title: str, text: str, ok: str = "Tak", cancel: str = "Anuluj") -> bool:
    modal = Modal(master, title, theme)
    modal.result = False
    ttk.Label(modal.body, text=text, justify="left", wraplength=420).pack(anchor="w")
    modal.add_button(cancel, modal.close)
    modal.add_button(ok, lambda: modal.close(True), accent=True)
    modal.bind("<Return>", lambda e: modal.close(True))
    return bool(modal.show())
