# LockLauncher

LockLauncher stops two people from editing the same shared Excel file at the
same time and overwriting each other's work.

Here's the problem it solves: when an Excel file lives in a shared cloud folder
(Proton Drive), two people can open it at once. Whoever saves last wins, and the
other person's changes silently vanish. LockLauncher puts a "someone is editing
this — please wait" sign on the file so that can't happen.

**How people use it:** instead of opening the Excel file directly, everyone
double-clicks **LockLauncher** (a small program that sits in the same folder).
LockLauncher checks whether anyone else has the file open. If it's free, it
opens Excel for you and quietly puts up the "in use" sign. When you close Excel,
the sign comes down automatically. If someone else already has it open, it tells
you who, and when they started.

**Your Excel file is never uploaded anywhere.** LockLauncher uses a tiny server
(a cheap always-on computer in the cloud) to keep track of *who has the file
open* — nothing more. The server never sees the file or its contents. It only
remembers a note like `{ in use, by Alice, since 2:14pm }`.

```
Your PC                              The little server (a VPS)
  LockLauncher  ── internet ──►        keeps one note per file:
  Proton Drive folder\                   "Budget.xlsx: in use by Alice, 2:14pm"
    Budget.xlsx
    LockLauncher.exe
```

---

## Who this guide is for

There are really two jobs here:

- **Everyday users** just double-click LockLauncher and open their file. If
  that's you, skip straight to [Part 3: Using LockLauncher](#part-3-using-locklauncher).
- **Whoever sets it up** (installs the server, builds the LockLauncher program,
  and hands it out) needs Parts 1 and 2. You do **not** need to be a programmer,
  but you will copy and paste some commands. Follow them exactly and it works.

You set the server up **once**. After that you only ever rebuild the
LockLauncher program when something changes (a new file to manage, a new
password, etc.).

---

## What's in this project

```
locklauncher/
├── server/                     Runs on the always-on cloud computer
│   ├── main.py                   the whole server program
│   ├── requirements.txt          list of things the server needs installed
│   ├── locklauncher.service      tells the server to keep running 24/7
│   └── deploy.sh                 pushes updates to the server
├── client/                     Runs on each person's Windows PC
│   ├── launcher.py               the LockLauncher program's source
│   ├── config.ini              ◄ THE SETTINGS FILE YOU EDIT (server address,
│   │                             password, which file, etc.)
│   ├── build.bat                 double-click on Windows to build the program
│   └── requirements.txt
└── scripts/
    └── status.sh                 quick "is anyone using the file?" check
```

The one file you'll touch most is **`client/config.ini`**. That's where all the
settings live.

---

## Part 1: Set up the server (once)

The server is a small always-on computer you rent in the cloud (a "VPS").
These steps were tested on **Debian 12**. You do this **one time**.

You'll be typing commands into the server over SSH. If you've never done that,
your VPS provider (e.g. Hetzner, DigitalOcean) has a "how to connect via SSH"
guide — follow it to get a black terminal window logged into your server, then
come back here.

Copy and paste these blocks one at a time. Replace `<your-repo-url>` with the
web address where this project lives (ask whoever gave you this).

```bash
# Install the basic tools the server needs
apt update && apt install -y python3-venv ufw git

# Download this project onto the server
git clone <your-repo-url> ~/locklauncher
cd ~/locklauncher

# Lock down the firewall — allow only SSH and LockLauncher's port
ufw allow 22
ufw allow 47291
ufw enable

# Set up the server's private workspace
python3 -m venv ~/locklauncher/venv
~/locklauncher/venv/bin/pip install -r server/requirements.txt

# Create a secret password (the "API key") that only your PCs will know
echo "API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > ~/locklauncher/.env
chmod 600 ~/locklauncher/.env

# Turn LockLauncher's server on, and make it restart itself forever
cp server/locklauncher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now locklauncher
```

**Check it's working:**

```bash
curl http://localhost:47291/health
```

You should see `{"status":"ok"}`. 🎉

**Get the secret password** you just generated — you'll need it in Part 2:

```bash
cat ~/locklauncher/.env
```

Copy the long string after `API_KEY=` and keep it handy.

> **The port number (47291)** is just an uncommon number picked so random
> internet bots don't stumble onto it. The real security is the secret
> password. If you ever want to change the port, edit it in both
> `server/locklauncher.service` and `client/config.ini`.

### Later: updating the server

If the server program (`server/main.py`) ever changes, push the new version
from your own computer:

```bash
./server/deploy.sh
```

(Open `server/deploy.sh` first and set `VPS_HOST` to your server's address.
Also make sure your changes are pushed to the shared repo first — the server
downloads them from there.)

---

## Part 2: Build the LockLauncher program (on Windows)

LockLauncher is handed out as a single Windows file, `LockLauncher.exe`. You
build it on a **Windows PC that has Python installed** (get Python from
[python.org](https://www.python.org/downloads/) — during install, tick "Add
Python to PATH").

### Step 1 — Copy the `client` folder to the Windows PC

Just the `client` folder is enough.

### Step 2 — Edit the settings file `config.ini`

Open `client/config.ini` in Notepad. It looks like this:

```ini
[server]
url = http://<your-server-ip>:47291
api_key = <the secret password from Part 1>

[file]
name = Budget.xlsx
id = budget

[build]
exe_name = LockLauncher
```

Fill in each line:

- **`url`** — your server's address, e.g. `http://203.0.113.5:47291`.
- **`api_key`** — the long secret password you copied at the end of Part 1.
- **`name`** — the exact Excel filename, including `.xlsx`, e.g. `Budget.xlsx`.
- **`id`** — a short nickname for *this* file. See the important note below.
- **`exe_name`** — what the built program should be called. For a file called
  Budget you might use `Budget Launcher`, so people know which button opens which
  file.

> ### ⚠️ Important if you're managing more than one file
>
> Every file you manage needs its **own unique `id`** — a short nickname like
> `budget`, `sales`, `inventory`. The `id` is what the server uses to tell the
> files apart. If two files share the same `id`, LockLauncher will think they're
> the same file and one person opening Budget will block someone else from
> opening Sales.
>
> Good news: **all your files can share the same server and the same
> `api_key`.** You only change `name`, `id`, and `exe_name` for each new file.
>
> If you leave `id` blank, LockLauncher just uses the filename as the id — fine
> when you only have one file, but set a real `id` as soon as you have two.

### Step 3 — Build it

Double-click **`build.bat`**. A black window opens and does the work. When it
finishes you'll see:

```
Build complete: dist\LockLauncher.exe
```

Your finished program is in the newly-created **`dist`** folder. All the
settings are baked inside it — you don't need to ship `config.ini` alongside it.

### Step 4 — Hand it out

Copy the finished `.exe` from `dist\` into the **same Proton Drive folder as the
Excel file**, so it sits right next to it. Everyone who shares that folder now
has LockLauncher.

### If the build fails

- **"pip is not recognized"** or **"pyinstaller is not recognized"** — Python
  wasn't added to PATH during install. Reinstall Python and tick "Add Python to
  PATH", or use the current `build.bat` (it already works around this).
- Anything else — the black window stays open with the error. Read the last few
  lines; it usually names the missing piece.

### Rebuilding later

Any time the server address, the password, or a filename changes: edit
`config.ini`, double-click `build.bat` again, and give people the new `.exe`.

---

## Part 3: Using LockLauncher

*(This is the part to share with everyday users.)*

**Open your shared file by double-clicking LockLauncher instead of the Excel
file itself.** That's the only habit to build.

1. **Double-click LockLauncher.**
2. **First time only:** it asks for your name. This is just so coworkers can see
   who's got the file open. It's saved on your PC and you won't be asked again.
3. **If the file is free**, you get two choices:
   - **Open & Edit (Lock)** — opens the file in Excel for you and marks it "in
     use" for everyone else. When you close Excel, it's automatically freed. You
     don't have to do anything to release it.
   - **Open Read-Only** — opens a look-only copy without locking anything. Great
     for a quick peek when you don't need to change anything.
4. **If someone else has it open**, LockLauncher tells you **who** and **how
   long ago** they started, and offers:
   - **Release Lock & Open** *(red button)* — forces the file free and opens it
     for *you*. Only use this if you're sure the other person is actually done
     (for example their computer crashed and left the file stuck). Using it while
     they're really editing can cause a clash.
   - **Open Read-Only** — view a copy without disturbing them.
   - **Edit a Copy** — makes a dated copy on your Desktop and opens that. Your
     changes go into *your copy only* — they do **not** flow back into the shared
     file automatically.
   - **Cancel** — closes without doing anything.

### "Waiting to finish syncing…"

Sometimes after someone else finishes, Proton Drive hasn't finished copying
their latest changes down to your PC yet. If LockLauncher notices your copy is
still catching up, it shows a **"Syncing"** window and **waits** — it opens the
file **automatically** the moment your copy is up to date. You can just leave it;
there's nothing to click. (There's an **Open Anyway** button if you're in a
hurry and understand you might not have the very latest version, and **Cancel**
to back out.)

This safety check only runs when you're opening a file to *edit* it. Read-Only
and Edit-a-Copy skip it, because those already mean "I know this might not be the
newest version."

### Pointing LockLauncher at a moved or renamed file

If the Excel file gets moved or renamed and LockLauncher can't find it, it will
offer to let you locate it. You can also do this any time by running
LockLauncher with a `--settings` switch:

```
LockLauncher.exe --settings
```

That pops up a file picker; your choice is remembered on your PC. (Tip: make a
desktop shortcut to LockLauncher, and add ` --settings` to the end of the
Target field for a one-click way to do this.)

---

## Troubleshooting

**"Cannot find: …\Budget.xlsx"** — LockLauncher isn't in the same folder as the
Excel file, or Proton Drive hasn't finished loading. Make sure Proton Drive is
running and the folder shows as synced.

**"Timed out trying to reach …"** — the server may be off, or a firewall is
blocking it. On the server, check `systemctl status locklauncher` and
`ufw status`.

**"Connection refused by …"** — the server program isn't running. On the server:
`systemctl status locklauncher`, then `systemctl restart locklauncher`.

**"Could not resolve the server address …"** — the address in `config.ini` is
wrong, or the PC has no internet.

**"The server rejected the API key" (401)** — the password in `config.ini`
doesn't match the one on the server. Re-check with `cat ~/locklauncher/.env` on
the server, fix `config.ini`, and rebuild the `.exe`.

**The file shows as locked but nobody has it open** — wait about five minutes
and it clears itself. While someone has the file open, their LockLauncher
checks in with the server every minute; if their computer crashes, gets shut
off, loses its connection, or the launcher is closed, the check-ins stop and
the server frees the file on its own. You don't have to do anything. If you
don't want to wait, **Release Lock & Open** still clears it instantly.

To see exactly what the server is holding, how long since the holder last
checked in, and whether it has already been freed:

```bash
./scripts/status.sh http://<vps-ip>:47291 <api-key>
```

To change the five-minute window, add `LOCK_STALE_MINUTES=5` (or your preferred
value) to `~/locklauncher/.env` on the server and run
`systemctl restart locklauncher`. Keep it at several times the one-minute
check-in interval, so a brief internet hiccup can't free a file someone is
actively editing.

**"Your lock was released…"** — this appears if your PC couldn't reach the
server for several minutes while you had the file open. Someone else may have
taken it in the meantime, so save your work to a new copy rather than over the
shared file, then merge it in once you can see who changed what.

**Opening Budget also locks Sales (or vice-versa)** — the two files have the
same `id` in their `config.ini`. Give each file a unique `id`, rebuild both, and
hand out the new versions. See the note in [Part 2, Step 2](#step-2--edit-the-settings-file-configini).

**The "Syncing" window never opens the file** — Proton Drive may be stuck. Make
sure the Proton Drive app is running and signed in on both PCs and the folder
shows as fully synced (not "syncing…"). If the folder really is synced and the
window still won't clear, use **Open Anyway** — and see the note on stale
fingerprints below, which the server now avoids creating.

---

## For the technically curious

The server is a small FastAPI app. It stores one note per file, keyed by the
`id` from `config.ini`, in a single `lock_state.json` file. No Excel content or
file paths are ever sent to it.

| Method | Path      | Auth        | Body / query                          | Purpose |
|--------|-----------|-------------|----------------------------------------|---------|
| GET    | `/health` | none        | —                                      | Liveness check |
| GET    | `/status` | none        | `?lock_id=budget`                      | Who holds this file's lock |
| POST   | `/lock`   | `X-API-Key` | `{"name": "Alice", "lock_id": "budget", "token": "…"}` | Take the lock (409 if already held) |
| DELETE | `/lock`   | `X-API-Key` | `{"lock_id": "budget", "hash": "…", "token": "…"}` | Release; `hash` is remembered as `last_hash`. With a `token`, refused (409) if the lock has passed to someone else; without one, it is the deliberate override behind **Release Lock & Open** |
| POST   | `/heartbeat` | `X-API-Key` | `{"lock_id": "budget", "token": "…"}` | Renew the lease (409 if the lock is no longer yours) |
| GET    | `/locks`  | `X-API-Key` | —                                      | Every lock, with ages, heartbeat age, and whether it lapsed |

`lock_id` defaults to `default` if a client doesn't send one, so older builds
keep working. `/status` is unauthenticated and never returns the `token`. The `last_hash` is a SHA-256 fingerprint of the file recorded on a
clean close; the next opener compares their local copy against it to detect a
lagging Proton Drive sync (that's what powers the "Syncing" window).

**Locks are leases, not flags.** A lock is normally released by the launcher
process that took it, which stays running in the background the whole time the
file is open. If that process dies first — reboot, sign-out, antivirus, an
unmounted drive — nothing is left to send the release, and under a plain
lock-flag design the file would stay locked forever.

So the holder instead renews its claim: `POST /heartbeat` every
`HEARTBEAT_SECONDS` (60) for as long as the file is open, and the server
honours the lock only while those keep arriving. After `LOCK_STALE_MINUTES`
(default 5) of silence the lock is reported as free. Nothing has to notice the
holder died; the lock simply stops being renewed.

Each run sends a random `token` with its lock, and must present it to renew or
release. That stops a watcher thread left over from an earlier run — one whose
lease already lapsed and passed to someone else — from renewing or releasing a
lock that is no longer its own.

`LOCK_TTL_HOURS` (default 8) remains only as a fallback for locks taken by a
client too old to send heartbeats; nothing uses it once both machines are
updated.

**Orphaned `~$` files.** Excel keeps its `~$<filename>` owner file open for as
long as the workbook is, and deletes it on close. A crash — or a cloud sync
that re-creates the file after Excel removed it — can leave one behind, and a
leftover does double damage: Excel sees it and offers the next person
read-only, and the watcher below waits forever for a file that will never
disappear. Because Excel holds that file with write sharing denied, one we can
open for writing ourselves is provably an orphan. LockLauncher clears such a
file before opening the workbook, and treats one that stays unheld for 30
seconds mid-session as a closed session rather than waiting on it.

**Why a release without a hash clears `last_hash`.** A clean close sends the
fingerprint of the file that was just saved. A forced release — the **Release
Lock & Open** button, or an expiry — sends none, and by then the file has almost
certainly been edited since the stored fingerprint was taken. Keeping that
fingerprint would leave the next opener comparing their copy against bytes that
no longer exist anywhere, so the "Syncing" window would wait forever for a sync
that had already finished. The server clears it instead, which just skips the
sync check for one open.

**How releasing works under the hood:** Excel creates a hidden lock file named
`~$<filename>` while a file is open and deletes it on close. LockLauncher
watches for that file to disappear (reading it purely locally — it never relies
on that file syncing to the cloud) and then tells the server to release the
lock.

Check status from your own machine any time:

```bash
./scripts/status.sh http://<vps-ip>:47291
```

Or, with the API key, see every lock at once along with its age and whether the
server already considers it expired:

```bash
./scripts/status.sh http://<vps-ip>:47291 <api-key>
```
