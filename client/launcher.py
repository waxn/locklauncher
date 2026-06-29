import configparser
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, simpledialog

import requests


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _resource_dir() -> Path:
    """Directory containing bundled resources (config.ini) when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _exe_dir() -> Path:
    """Directory of the running .exe — same folder as the Excel file."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _localappdata() -> Path:
    path = os.environ.get("LOCALAPPDATA", "")
    return Path(path) if path else Path.home() / "AppData" / "Local"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(_resource_dir() / "config.ini")
    return cfg


# ---------------------------------------------------------------------------
# User identity (persisted per-device in AppData)
# ---------------------------------------------------------------------------

def load_user_name() -> str | None:
    user_file = _localappdata() / "LockLauncher" / "user.json"
    try:
        return json.loads(user_file.read_text()).get("name")
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save_user_name(name: str) -> None:
    user_dir = _localappdata() / "LockLauncher"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "user.json").write_text(json.dumps({"name": name}))


# ---------------------------------------------------------------------------
# Server calls
# ---------------------------------------------------------------------------

def fetch_status(server_url: str) -> dict:
    resp = requests.get(f"{server_url}/status", timeout=5)
    resp.raise_for_status()
    return resp.json()


def post_lock(server_url: str, api_key: str, name: str) -> bool:
    """Returns True if lock acquired, False if already locked (409)."""
    resp = requests.post(
        f"{server_url}/lock",
        json={"name": name},
        headers={"X-API-Key": api_key},
        timeout=5,
    )
    if resp.status_code == 409:
        return False
    resp.raise_for_status()
    return True


def delete_lock(server_url: str, api_key: str) -> None:
    requests.delete(
        f"{server_url}/lock",
        headers={"X-API-Key": api_key},
        timeout=5,
    ).raise_for_status()


# ---------------------------------------------------------------------------
# File monitoring
# ---------------------------------------------------------------------------

def _watch_and_release(excel_path: Path, server_url: str, api_key: str) -> None:
    """
    Background thread: waits for Excel to open the file, then waits for it to
    close, then releases the server lock. Polls for Excel's hidden lock file.
    """
    lock_file = excel_path.parent / f"~${excel_path.name}"

    # Give Excel up to 60 s to create the lock file after os.startfile
    for _ in range(120):
        if lock_file.exists():
            break
        time.sleep(0.5)

    # Wait until Excel closes the file
    while lock_file.exists():
        time.sleep(0.5)

    # Release the server lock, retrying on transient network failures
    while True:
        try:
            delete_lock(server_url, api_key)
            return
        except Exception:
            time.sleep(10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _age_string(iso_str: str) -> str:
    try:
        locked_at = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - locked_at
        mins = int(delta.total_seconds() // 60)
        if mins < 1:
            return "just now"
        if mins == 1:
            return "1 min ago"
        if mins < 60:
            return f"{mins} min ago"
        return f"{mins // 60} hr ago"
    except Exception:
        return iso_str


def _open(path: Path) -> None:
    os.startfile(str(path))


def _open_readonly_copy(excel_path: Path) -> None:
    tmp = Path(tempfile.mkdtemp()) / excel_path.name
    shutil.copy2(excel_path, tmp)
    _open(tmp)


def _open_edit_copy(excel_path: Path) -> None:
    desktop = Path.home() / "Desktop"
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dest = desktop / f"{excel_path.stem}_copy_{ts}{excel_path.suffix}"
    shutil.copy2(excel_path, dest)
    _open(dest)
    messagebox.showinfo(
        "LockLauncher",
        f"Copy saved to your Desktop:\n{dest.name}\n\n"
        "Changes will NOT sync to the shared file automatically.",
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _prompt_name(root: tk.Tk) -> str | None:
    name = simpledialog.askstring(
        "Welcome to LockLauncher",
        "Enter your name.\nThis is saved on this device and shown to others when\nyou have the file open.",
        parent=root,
    )
    return name.strip() if name and name.strip() else None


def _show_locked_dialog(root: tk.Tk, status: dict) -> str:
    """Shows the locked-file dialog. Returns one of: release / readonly / copy / cancel."""
    locker = status.get("locked_by", "someone")
    age = _age_string(status.get("locked_at", ""))

    dialog = tk.Toplevel(root)
    dialog.title("LockLauncher")
    dialog.resizable(False, False)
    dialog.grab_set()
    dialog.lift()
    dialog.focus_force()

    dialog.update_idletasks()
    w, h = 300, 240
    sw = dialog.winfo_screenwidth()
    sh = dialog.winfo_screenheight()
    dialog.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    tk.Label(
        dialog,
        text=f"File is locked by {locker}\n({age})",
        font=("Segoe UI", 11, "bold"),
        pady=16,
    ).pack()

    choice = tk.StringVar(value="cancel")

    def pick(val: str) -> None:
        choice.set(val)
        dialog.destroy()

    frame = tk.Frame(dialog, padx=28, pady=4)
    frame.pack(fill="x")

    buttons = [
        ("Release Lock & Open", "release"),
        ("Open Read-Only", "readonly"),
        ("Edit a Copy", "copy"),
        ("Cancel", "cancel"),
    ]
    for label, val in buttons:
        tk.Button(frame, text=label, width=24, command=lambda v=val: pick(v)).pack(pady=3)

    dialog.wait_window()
    return choice.get()


# ---------------------------------------------------------------------------
# Core acquire-and-open flow
# ---------------------------------------------------------------------------

def _do_acquire_and_open(server_url: str, api_key: str, name: str, excel_path: Path) -> bool:
    """
    Acquires the server lock, opens the file, starts the file watcher, and
    blocks until the file is closed (watcher releases the lock). Returns False
    if the lock was already taken (409).
    """
    if not post_lock(server_url, api_key, name):
        return False

    _open(excel_path)

    t = threading.Thread(
        target=_watch_and_release,
        args=(excel_path, server_url, api_key),
        daemon=True,
    )
    t.start()
    t.join()  # Keep the process alive until the file watcher releases the lock
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    root.withdraw()

    cfg = load_config()
    server_url = cfg["server"]["url"].rstrip("/")
    api_key = cfg["server"]["api_key"]
    excel_name = cfg["file"]["name"]
    excel_path = _exe_dir() / excel_name

    # Verify the Excel file is reachable
    if not excel_path.exists():
        messagebox.showerror(
            "LockLauncher",
            f"Cannot find:\n{excel_path}\n\nMake sure Proton Drive is mounted.",
        )
        sys.exit(1)

    # Ensure we have a user name
    name = load_user_name()
    if not name:
        name = _prompt_name(root)
        if not name:
            messagebox.showerror("LockLauncher", "A name is required to use LockLauncher.")
            sys.exit(1)
        save_user_name(name)

    # Fetch current lock status
    try:
        status = fetch_status(server_url)
    except Exception:
        if messagebox.askyesno(
            "LockLauncher — Server Unreachable",
            "Cannot reach the lock server.\n\nOpen a read-only copy instead?",
        ):
            _open_readonly_copy(excel_path)
        sys.exit(0)

    # Main loop — handles the (rare) race where we try to acquire a just-locked file
    while True:
        if not status.get("locked"):
            try:
                acquired = _do_acquire_and_open(server_url, api_key, name, excel_path)
            except Exception as e:
                messagebox.showerror("LockLauncher", f"Network error:\n{e}")
                sys.exit(1)

            if acquired:
                sys.exit(0)

            # Race condition: someone grabbed the lock between our status check
            # and our POST — re-fetch and fall through to the locked dialog
            try:
                status = fetch_status(server_url)
            except Exception as e:
                messagebox.showerror("LockLauncher", f"Network error:\n{e}")
                sys.exit(1)

        action = _show_locked_dialog(root, status)

        if action == "cancel":
            sys.exit(0)

        elif action == "readonly":
            _open_readonly_copy(excel_path)
            sys.exit(0)

        elif action == "copy":
            _open_edit_copy(excel_path)
            sys.exit(0)

        elif action == "release":
            try:
                delete_lock(server_url, api_key)
            except Exception as e:
                messagebox.showerror("LockLauncher", f"Could not release lock:\n{e}")
                sys.exit(1)

            try:
                acquired = _do_acquire_and_open(server_url, api_key, name, excel_path)
            except Exception as e:
                messagebox.showerror("LockLauncher", f"Network error:\n{e}")
                sys.exit(1)

            if acquired:
                sys.exit(0)

            # Someone else grabbed it in the brief window — loop back to locked dialog
            try:
                status = fetch_status(server_url)
            except Exception as e:
                messagebox.showerror("LockLauncher", f"Network error:\n{e}")
                sys.exit(1)


if __name__ == "__main__":
    main()
