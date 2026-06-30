# LockLauncher

A tiny lock-coordination system for a single Excel file shared by two users over
Proton Drive. A small VPS holds the lock status (who has the file open, since
when). A Windows launcher `.exe` sits next to the Excel file — double-click it,
and it checks the lock, opens the file, and releases the lock automatically
when you close Excel.

**The Excel file itself never touches the server.** The server only ever
stores `{locked, locked_by, locked_at}` — no file content, no file path.

```
[User's PC]                         [VPS — Debian]
  LockLauncher.exe  <-- HTTPS -->     FastAPI lock server
  ProtonDrive\                          stores only: {locked, "Alice", time}
    Budget.xlsx
    ~$Budget.xlsx   (Excel's own lock file — used to detect close)
    LockLauncher.exe
```

---

## Repository layout

```
locklauncher/
├── server/
│   ├── main.py                 FastAPI app — the whole server
│   ├── requirements.txt
│   ├── locklauncher.service    systemd unit
│   └── deploy.sh                git pull + restart, run from your dev machine
├── client/
│   ├── launcher.py             the launcher's source
│   ├── config.ini              server URL / API key / filename — EDIT before building
│   ├── build.bat                run on Windows to produce LockLauncher.exe
│   └── requirements.txt
└── scripts/
    └── status.sh                quick `curl` status check
```

---

## 1. Server setup (one-time, on the VPS)

Tested on Debian 12 (Bookworm). Run as root.

```bash
apt update && apt install -y python3-venv ufw git

# Clone the repo onto the server (adjust the URL to wherever you host it)
git clone <your-repo-url> ~/locklauncher
cd ~/locklauncher

# Firewall — only SSH and the lock server's port
ufw allow 22
ufw allow 47291
ufw enable

# Python environment
python3 -m venv ~/locklauncher/venv
~/locklauncher/venv/bin/pip install -r server/requirements.txt

# Generate the API key once and save it server-side only
echo "API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > ~/locklauncher/.env
chmod 600 ~/locklauncher/.env

# Install as a systemd service
cp server/locklauncher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now locklauncher
```

Verify it's up:

```bash
curl http://localhost:47291/health
# {"status":"ok"}
```

Grab the API key you'll need for the client config:

```bash
cat ~/locklauncher/.env
```

### Port

The server listens on **47291** (an arbitrary, non-default port, chosen to
avoid casual bot scanning — there's no real secrecy here, the API key is the
actual gate). To change it, edit the `--port` flag in
`server/locklauncher.service` and the `url` in `client/config.ini`, then
re-deploy.

### Redeploying after server code changes

From your dev machine, edit `server/deploy.sh` to set `VPS_HOST`, then:

```bash
./server/deploy.sh
```

This SSHes in, does `git pull`, reinstalls dependencies, and restarts the
service. (Push your changes to the repo first — the server pulls from git,
it doesn't receive files directly.)

### Checking status from your own machine

```bash
./scripts/status.sh http://<vps-ip>:47291
```

Or directly:

```bash
curl -s http://<vps-ip>:47291/status | python3 -m json.tool
```

### API reference

| Method | Path      | Auth        | Body                | Description          |
|--------|-----------|-------------|----------------------|-----------------------|
| GET    | `/health` | none        | —                     | Liveness check        |
| GET    | `/status` | none        | —                     | Current lock state    |
| POST   | `/lock`   | `X-API-Key` | `{"name": "Alice"}`  | Acquire lock (409 if already locked) |
| DELETE | `/lock`   | `X-API-Key` | —                     | Release lock           |

---

## 2. Building the client `.exe` (on Windows)

The launcher is Python, bundled into a single `LockLauncher.exe` via
PyInstaller. You need a Windows machine with Python installed
(python.org installer, which provides the `py` launcher).

1. Copy the `client/` folder to the Windows machine.
2. Edit `client/config.ini`:
   ```ini
   [server]
   url = http://<vps-ip>:47291
   api_key = <the API_KEY value from the server's .env>

   [file]
   name = Budget.xlsx
   ```
   `name` must exactly match the Excel file's filename (with extension).
3. Run `build.bat` (double-click it, or run from a terminal). It will:
   - `py -m pip install -r requirements.txt`
   - `py -m PyInstaller --onefile --windowed --add-data "config.ini;." --name LockLauncher launcher.py`
4. Output: `dist\LockLauncher.exe` — a single file. `config.ini` is baked
   inside it; nothing else needs to ship alongside it.
5. Copy `LockLauncher.exe` into the same Proton Drive folder as the Excel
   file.

If `build.bat` fails with `'pip' is not recognized` or
`'pyinstaller' is not recognized` — make sure you're using the current
version of `build.bat`, which invokes everything through `py -m ...` rather
than bare commands (some Python installs don't add the `Scripts` folder to
PATH, but the `py` launcher itself is always on PATH).

### Rebuilding after a config or server change

Whenever the VPS IP, API key, or Excel filename changes, edit
`client/config.ini` and re-run `build.bat`, then redistribute the new
`LockLauncher.exe`.

---

## 3. Using LockLauncher (end users)

1. Double-click `LockLauncher.exe`.
2. **First run only:** you'll be asked for your name. It's saved locally to
   `%LOCALAPPDATA%\LockLauncher\user.json` and used to label the lock and to
   show others who's editing.
3. If the file is **not locked**: the lock is acquired, Excel opens the file,
   and LockLauncher waits quietly in the background. When you close Excel,
   the lock is released automatically — no extra action needed.
4. If the file **is locked**, you'll see who has it and for how long, with
   four options:
   - **Release Lock & Open** — force-clears a stale lock (e.g. the other
     user's app crashed) and opens the file for editing.
   - **Open Read-Only** — opens a temporary copy for viewing only.
   - **Edit a Copy** — saves a timestamped copy to your Desktop and opens
     it. Changes here do **not** sync back to the shared file automatically.
   - **Cancel** — does nothing.

### How lock release actually works

LockLauncher watches for Excel's own hidden lock file
(`~$<filename>.xlsx`), which Excel creates locally the moment it opens a
file and deletes the moment it closes it. This file lives in the same
(possibly cloud-synced) folder, but LockLauncher only ever reads it on the
local machine — it never depends on that file syncing across Proton Drive.
When it disappears, LockLauncher tells the server to release the lock.

---

## Troubleshooting

**"Cannot find: ...\Budget.xlsx"** — the Excel file isn't in the same folder
as `LockLauncher.exe`, or Proton Drive isn't mounted/synced yet.

**"Timed out trying to reach ..."** — the server may be down, or a firewall
is blocking port 47291. On the server: `systemctl status locklauncher` and
`ufw status`.

**"Connection refused by ..."** — the service isn't running. On the server:
`systemctl status locklauncher`, then `systemctl restart locklauncher` if
needed.

**"Could not resolve the server address ..."** — the IP/hostname in
`config.ini` is wrong, or this machine has no internet access.

**"The server rejected the API key"** (HTTP 401) — `api_key` in
`config.ini` doesn't match `API_KEY` in the server's `.env`. Re-check
`cat ~/locklauncher/.env` on the server, fix `config.ini`, and rebuild.

**Lock appears stuck even though no one has the file open** — this can
happen if the app was killed before the watcher thread could release the
lock (e.g. a forced shutdown). Use **Release Lock & Open** from the locked
dialog to clear it.
