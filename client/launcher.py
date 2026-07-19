import configparser
import hashlib
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
from tkinter import filedialog, messagebox, simpledialog

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
# File path override (persisted per-device; lets a user point LockLauncher at
# a different file location without rebuilding the exe)
# ---------------------------------------------------------------------------

def _settings_file() -> Path:
    return _localappdata() / "LockLauncher" / "settings.json"


def load_file_override() -> Path | None:
    try:
        data = json.loads(_settings_file().read_text())
        path = data.get("file_path")
        return Path(path) if path else None
    except (OSError, json.JSONDecodeError):
        return None


def save_file_override(path: Path) -> None:
    settings_dir = _localappdata() / "LockLauncher"
    settings_dir.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps({"file_path": str(path)}))


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------

def _describe_error(e: Exception, server_url: str) -> str:
    """Turn a requests exception into a detailed, actionable message."""
    if isinstance(e, requests.exceptions.MissingSchema):
        return (
            f"The server URL in config.ini looks malformed:\n{server_url}\n\n"
            "It should look like: http://203.0.113.5:47291"
        )

    if isinstance(e, requests.exceptions.ConnectTimeout):
        return (
            f"Timed out trying to reach:\n{server_url}\n\n"
            "The server may be down, or a firewall is blocking the connection.\n"
            "Double-check the IP and port in config.ini."
        )

    if isinstance(e, requests.exceptions.ReadTimeout):
        return (
            f"Connected to {server_url} but it did not respond in time.\n\n"
            "The server process may be hung or overloaded. Try again, or check\n"
            "its status with: systemctl status locklauncher"
        )

    if isinstance(e, requests.exceptions.SSLError):
        return f"SSL/TLS error connecting to:\n{server_url}\n\n{e}"

    if isinstance(e, requests.exceptions.ConnectionError):
        cause = str(e)
        if any(s in cause for s in ("NameResolutionError", "getaddrinfo failed", "Name or service not known")):
            return (
                f"Could not resolve the server address in:\n{server_url}\n\n"
                "Check that the IP/hostname in config.ini is correct and that\n"
                "this machine has internet access."
            )
        if "Connection refused" in cause:
            return (
                f"Connection refused by:\n{server_url}\n\n"
                "The server process may not be running, or the port number is\n"
                "wrong. On the server, check: systemctl status locklauncher"
            )
        if "timed out" in cause.lower():
            return (
                f"Timed out trying to reach:\n{server_url}\n\n"
                "The server may be down, or a firewall (e.g. ufw) is blocking\n"
                "the port. On the server, check: ufw status"
            )
        return (
            f"Could not connect to:\n{server_url}\n\n"
            "Check your internet connection and that the server is running.\n\n"
            f"Details: {cause}"
        )

    if isinstance(e, requests.exceptions.HTTPError):
        status = e.response.status_code if e.response is not None else "?"
        if status == 401:
            return (
                "The server rejected the API key.\n\n"
                "Check that api_key in config.ini matches the API_KEY value in\n"
                "the server's .env file, then rebuild the exe."
            )
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        return f"Server returned an error (HTTP {status}) from:\n{server_url}\n\n{body}"

    return f"Unexpected error talking to:\n{server_url}\n\n{type(e).__name__}: {e}"


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


def delete_lock(server_url: str, api_key: str, file_hash: str | None = None) -> None:
    requests.delete(
        f"{server_url}/lock",
        headers={"X-API-Key": api_key},
        json={"hash": file_hash},
        timeout=5,
    ).raise_for_status()


# ---------------------------------------------------------------------------
# File hashing (used to detect a stale Proton Drive sync before opening)
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_file_with_retry(path: Path, attempts: int = 5, delay: float = 1.0) -> str | None:
    """Excel can briefly hold the file right after closing; retry a few times."""
    for _ in range(attempts):
        try:
            return _hash_file(path)
        except OSError:
            time.sleep(delay)
    return None


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

    # Let the filesystem settle, then hash the final saved file so the next
    # opener can detect whether their local Proton Drive copy has synced.
    time.sleep(1)
    file_hash = _hash_file_with_retry(excel_path)

    # Release the server lock, retrying on transient network failures
    while True:
        try:
            delete_lock(server_url, api_key, file_hash=file_hash)
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


def _version_mismatch_dialog(filename: str) -> str:
    """Custom dialog for hash mismatch. Returns 'retry', 'open', or 'cancel'."""
    dialog = tk.Toplevel()
    dialog.title("LockLauncher — Wrong Version")
    dialog.resizable(False, False)
    dialog.grab_set()
    dialog.lift()
    dialog.focus_force()

    dialog.update_idletasks()
    w, h = 320, 260
    sw = dialog.winfo_screenwidth()
    sh = dialog.winfo_screenheight()
    dialog.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    tk.Label(
        dialog,
        text=f"Your copy of {filename}\nmay not be fully synced yet.",
        font=("Segoe UI", 11, "bold"),
        pady=12,
    ).pack()

    tk.Label(
        dialog,
        text="Proton Drive is probably still syncing the\n"
             "other device's changes. Wait a few seconds\n"
             "then click Retry.",
        justify="center",
        pady=4,
    ).pack()

    choice = tk.StringVar(value="cancel")

    def pick(val: str) -> None:
        choice.set(val)
        dialog.destroy()

    frame = tk.Frame(dialog, padx=28, pady=10)
    frame.pack(fill="x")

    tk.Button(frame, text="Retry", width=24, command=lambda: pick("retry")).pack(pady=3)
    tk.Button(frame, text="Open Anyway", width=24, command=lambda: pick("open")).pack(pady=3)
    tk.Button(frame, text="Cancel", width=24, command=lambda: pick("cancel")).pack(pady=3)

    dialog.wait_window()
    return choice.get()


def _check_version_or_warn(excel_path: Path, last_hash: str | None) -> bool:
    """
    Confirms the local file matches the hash recorded the last time the lock
    was released cleanly. Returns True if it's safe to proceed, False if the
    user gave up waiting for Proton Drive to sync.
    """
    if not last_hash:
        return True

    while True:
        try:
            local_hash = _hash_file(excel_path)
        except OSError:
            local_hash = None

        if local_hash == last_hash:
            return True

        action = _version_mismatch_dialog(excel_path.name)
        if action == "retry":
            continue
        elif action == "open":
            return True  # proceeds normally; hash is updated from this file on close
        else:
            return False


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


def _run_settings_dialog(current_path: Path | None) -> Path | None:
    """
    Lets the user pick which Excel file LockLauncher should manage, overriding
    the filename baked into config.ini. Returns the new path, or None if
    cancelled. Run via `LockLauncher.exe --settings`.
    """
    messagebox.showinfo(
        "LockLauncher Settings",
        "Choose the shared Excel file LockLauncher should manage.",
    )
    chosen = filedialog.askopenfilename(
        title="Select the shared Excel file",
        filetypes=[("Excel files", "*.xlsx *.xlsm *.xls"), ("All files", "*.*")],
        initialdir=str(current_path.parent) if current_path else str(_exe_dir()),
    )
    if not chosen:
        return None
    new_path = Path(chosen)
    save_file_override(new_path)
    messagebox.showinfo("LockLauncher Settings", f"Saved:\n{new_path}")
    return new_path


def _show_open_choice_dialog(root: tk.Tk, filename: str) -> str:
    """Shown when the file is unlocked and available. Returns one of: edit / readonly / cancel."""
    dialog = tk.Toplevel(root)
    dialog.title("LockLauncher")
    dialog.resizable(False, False)
    dialog.grab_set()
    dialog.lift()
    dialog.focus_force()

    dialog.update_idletasks()
    w, h = 300, 200
    sw = dialog.winfo_screenwidth()
    sh = dialog.winfo_screenheight()
    dialog.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    tk.Label(
        dialog,
        text=f"{filename}\nis available.",
        font=("Segoe UI", 11, "bold"),
        pady=16,
    ).pack()

    choice = tk.StringVar(value="cancel")

    def pick(val: str) -> None:
        choice.set(val)
        dialog.destroy()

    frame = tk.Frame(dialog, padx=28, pady=4)
    frame.pack(fill="x")

    tk.Button(frame, text="Open & Edit (Lock)", width=24, command=lambda: pick("edit")).pack(pady=3)
    tk.Button(frame, text="Open Read-Only", width=24, command=lambda: pick("readonly")).pack(pady=3)
    tk.Button(frame, text="Cancel", width=24, command=lambda: pick("cancel")).pack(pady=3)

    dialog.wait_window()
    return choice.get()


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
        style = (
            dict(bg="#c0392b", fg="white", activebackground="#a93226", activeforeground="white")
            if val == "release"
            else {}
        )
        tk.Button(frame, text=label, width=24, command=lambda v=val: pick(v), **style).pack(pady=3)

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

    # `LockLauncher.exe --settings` opens a file picker to change which file
    # is managed, without needing to rebuild the exe. Make a shortcut with
    # this flag appended to the target for easy access.
    if any(arg in ("--settings", "/settings") for arg in sys.argv[1:]):
        _run_settings_dialog(load_file_override())
        sys.exit(0)

    try:
        cfg = load_config()
        server_url = cfg["server"]["url"].rstrip("/")
        api_key = cfg["server"]["api_key"]
        excel_name = cfg["file"]["name"]
    except (KeyError, configparser.Error) as e:
        messagebox.showerror(
            "LockLauncher — Config Error",
            f"Could not read config.ini (it may be missing or malformed).\n\n"
            f"Missing key: {e}\n\n"
            "Rebuild the exe after editing client/config.ini with the correct\n"
            "server URL, API key, and Excel filename.",
        )
        sys.exit(1)
    default_path = _exe_dir() / excel_name

    override = load_file_override()
    excel_path = override if override else default_path

    # Verify the Excel file is reachable
    if not excel_path.exists():
        if messagebox.askyesno(
            "LockLauncher",
            f"Cannot find:\n{excel_path}\n\n"
            "Make sure Proton Drive is mounted, or locate the file now?",
        ):
            picked = _run_settings_dialog(excel_path)
            if not picked or not picked.exists():
                messagebox.showerror("LockLauncher", "File still not found.")
                sys.exit(1)
            excel_path = picked
        else:
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
    except Exception as e:
        detail = _describe_error(e, server_url)
        if messagebox.askyesno(
            "LockLauncher — Server Unreachable",
            f"{detail}\n\nOpen a read-only copy instead?",
        ):
            _open_readonly_copy(excel_path)
        sys.exit(0)

    # Main loop — handles the (rare) race where we try to acquire a just-locked file
    while True:
        if not status.get("locked"):
            choice = _show_open_choice_dialog(root, excel_path.name)

            if choice == "cancel":
                sys.exit(0)

            if choice == "readonly":
                _open_readonly_copy(excel_path)
                sys.exit(0)

            if not _check_version_or_warn(excel_path, status.get("last_hash")):
                sys.exit(0)

            try:
                acquired = _do_acquire_and_open(server_url, api_key, name, excel_path)
            except Exception as e:
                messagebox.showerror("LockLauncher — Could Not Acquire Lock", _describe_error(e, server_url))
                sys.exit(1)

            if acquired:
                sys.exit(0)

            # Race condition: someone grabbed the lock between our status check
            # and our POST — re-fetch and fall through to the locked dialog
            try:
                status = fetch_status(server_url)
            except Exception as e:
                messagebox.showerror("LockLauncher — Connection Lost", _describe_error(e, server_url))
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
                status = fetch_status(server_url)
            except Exception as e:
                messagebox.showerror("LockLauncher — Could Not Release Lock", _describe_error(e, server_url))
                sys.exit(1)

            if not _check_version_or_warn(excel_path, status.get("last_hash")):
                sys.exit(0)

            try:
                acquired = _do_acquire_and_open(server_url, api_key, name, excel_path)
            except Exception as e:
                messagebox.showerror("LockLauncher — Could Not Acquire Lock", _describe_error(e, server_url))
                sys.exit(1)

            if acquired:
                sys.exit(0)

            # Someone else grabbed it in the brief window — loop back to locked dialog
            try:
                status = fetch_status(server_url)
            except Exception as e:
                messagebox.showerror("LockLauncher — Connection Lost", _describe_error(e, server_url))
                sys.exit(1)


if __name__ == "__main__":
    main()
