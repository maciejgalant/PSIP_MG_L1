"""Logowanie do aplikacji GIS: konta w gui/users.json (hasla jako PBKDF2-SHA256 z sola).

Plik users.json jest w .gitignore - kazdy tworzy konto lokalnie przy pierwszym uruchomieniu.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

USERS_FILE = Path(os.getenv("PSIP_USERS_FILE", Path(__file__).with_name("users.json")))
ITERATIONS = 200_000
LOGIN_RE = re.compile(r"[A-Za-z0-9_.\-]{3,32}")


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def load_users() -> dict:
    if not USERS_FILE.is_file():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_users(users: dict) -> None:
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(USERS_FILE)


def create_user(login: str, password: str) -> None:
    if not LOGIN_RE.fullmatch(login):
        raise ValueError("Login: 3–32 znaki (litery, cyfry, _ . -)")
    if len(password) < 4:
        raise ValueError("Hasło musi mieć co najmniej 4 znaki")
    users = load_users()
    if login.lower() in {u.lower() for u in users}:
        raise ValueError(f"Użytkownik „{login}” już istnieje")
    salt = secrets.token_bytes(16)
    users[login] = {"salt": salt.hex(), "hash": _hash(password, salt), "iterations": ITERATIONS}
    save_users(users)


def verify(login: str, password: str) -> bool:
    users = load_users()
    entry = next((v for k, v in users.items() if k.lower() == login.lower()), None)
    if not entry:
        _hash(password, b"x" * 16)  # stały czas odpowiedzi
        return False
    expected = _hash(password, bytes.fromhex(entry["salt"]), int(entry.get("iterations", ITERATIONS)))
    return hmac.compare_digest(expected, entry["hash"])


def canonical_login(login: str) -> str:
    return next((k for k in load_users() if k.lower() == login.lower()), login)


class LoginWindow(tk.Toplevel):
    """Okno logowania (modal). on_success(login) wywolywane po poprawnym logowaniu."""

    def __init__(self, master, on_success, on_cancel, theme: dict) -> None:
        super().__init__(master)
        self.on_success, self.on_cancel = on_success, on_cancel
        self.title("PSIP GIS | Logowanie")
        self.resizable(False, False)
        self.configure(bg=theme["bg"])
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.login = tk.StringVar()
        self.password = tk.StringVar()
        self.password2 = tk.StringVar()
        self.mode = "register" if not load_users() else "login"
        self.message = tk.StringVar()
        self._build()
        self.bind("<Return>", lambda e: self._submit())
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{(self.winfo_screenwidth() - w) // 2}+{(self.winfo_screenheight() - h) // 3}")
        self.grab_set()
        self.focus_force()
        self.entry_login.focus_set()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=26)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="PSIP GIS", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.subtitle = ttk.Label(frame, style="Muted.TLabel")
        self.subtitle.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 16))
        ttk.Label(frame, text="Login").grid(row=2, column=0, sticky="w", pady=4)
        self.entry_login = ttk.Entry(frame, textvariable=self.login, width=28)
        self.entry_login.grid(row=2, column=1, pady=4)
        ttk.Label(frame, text="Hasło").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.password, show="•", width=28).grid(row=3, column=1, pady=4)
        self.label2 = ttk.Label(frame, text="Powtórz hasło")
        self.entry2 = ttk.Entry(frame, textvariable=self.password2, show="•", width=28)
        ttk.Label(frame, textvariable=self.message, style="Error.TLabel", wraplength=300).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.btn_submit = ttk.Button(buttons, style="Accent.TButton", command=self._submit)
        self.btn_submit.pack(side="right")
        self.btn_switch = ttk.Button(buttons, command=self._switch)
        self.btn_switch.pack(side="left")
        self._apply_mode()

    def _apply_mode(self) -> None:
        register = self.mode == "register"
        if register:
            self.label2.grid(row=4, column=0, sticky="w", pady=4)
            self.entry2.grid(row=4, column=1, pady=4)
        else:
            self.label2.grid_remove()
            self.entry2.grid_remove()
        first = register and not load_users()
        self.subtitle.configure(text="Utwórz pierwsze konto użytkownika" if first else
                                "Rejestracja nowego konta" if register else "Zaloguj się, aby otworzyć mapę")
        self.btn_submit.configure(text="Utwórz konto" if register else "Zaloguj")
        self.btn_switch.configure(text="Mam konto – logowanie" if register else "Utwórz konto")
        if first:
            self.btn_switch.state(["disabled"])
        else:
            self.btn_switch.state(["!disabled"])
        self.message.set("")

    def _switch(self) -> None:
        self.mode = "login" if self.mode == "register" else "register"
        self._apply_mode()

    def _submit(self) -> None:
        login, password = self.login.get().strip(), self.password.get()
        if self.mode == "register":
            if password != self.password2.get():
                self.message.set("Hasła nie są takie same.")
                return
            try:
                create_user(login, password)
            except ValueError as error:
                self.message.set(str(error))
                return
            messagebox.showinfo("Konto", f"Utworzono konto „{login}”.", parent=self)
        elif not verify(login, password):
            self.password.set("")
            self.message.set("Nieprawidłowy login lub hasło.")
            return
        self.grab_release()
        self.destroy()
        self.on_success(canonical_login(login))

    def _cancel(self) -> None:
        self.grab_release()
        self.destroy()
        self.on_cancel()
