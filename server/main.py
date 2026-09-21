import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

STATE_FILE = Path("lock_state.json")
_state_lock = threading.Lock()

# Lock id used when a client doesn't send one (older exes, or a config.ini
# with no [file] id). Keeps a single shared file working exactly as before.
DEFAULT_ID = "default"

# A lock is a lease: the client that holds it pings /heartbeat about once a
# minute for as long as the file is open, and the lock is only honoured while
# those pings keep arriving. Anything that kills the launcher process -- a
# reboot, a sign-out, antivirus quarantining the exe, Proton Drive unmounting,
# or a leftover ~$File.xlsx that wedges the file watcher -- stops the pings, and
# the lock frees itself a few minutes later without anyone having to click
# anything. Must stay comfortably above the client's heartbeat interval so a
# brief network drop doesn't drop the lease.
LOCK_STALE_MINUTES = float(os.environ.get("LOCK_STALE_MINUTES", "5"))

# Fallback for locks taken by a client too old to send heartbeats. Those can
# only be aged out by wall-clock time, so this has to be longer than a real
# editing session. Once both clients are updated, nothing uses this path.
# Set either value to 0 to disable that form of expiry.
LOCK_TTL_HOURS = float(os.environ.get("LOCK_TTL_HOURS", "8"))

app = FastAPI(title="LockLauncher")


def _default_lock() -> dict:
    return {
        "locked": False,
        "locked_by": None,
        "locked_at": None,
        "last_hash": None,
        # Identifies the launcher run that holds the lease, so a zombie client
        # can't release or renew a lock that has since passed to someone else.
        "token": None,
        # Last heartbeat. None means the holder is an old client that doesn't
        # send them, which falls back to LOCK_TTL_HOURS.
        "heartbeat_at": None,
    }


def load_all() -> dict:
    """
    Returns the full multi-lock state: {"locks": {lock_id: {...}}}.

    Transparently migrates the legacy single-lock format (a flat
    {"locked": ..., "locked_by": ...}) onto the DEFAULT_ID key.
    """
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            if isinstance(data, dict) and isinstance(data.get("locks"), dict):
                return data
            if isinstance(data, dict) and ("locked" in data or "locked_by" in data):
                legacy = _default_lock()
                legacy.update({k: data.get(k, legacy[k]) for k in legacy})
                return {"locks": {DEFAULT_ID: legacy}}
    except (json.JSONDecodeError, OSError):
        pass
    return {"locks": {}}


def save_all(data: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(STATE_FILE)


def _parse_ts(value) -> datetime | None:
    """Parse a stored ISO timestamp, tolerating the tz-naive ones older
    versions could write."""
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _expired(lock: dict) -> bool:
    """True if this lock is held but its holder has gone away."""
    if not lock.get("locked"):
        return False

    now = datetime.now(timezone.utc)

    if lock.get("heartbeat_at") is not None:
        # Lease: honoured only while the holder keeps checking in.
        if LOCK_STALE_MINUTES <= 0:
            return False
        beat = _parse_ts(lock["heartbeat_at"])
        if beat is None:
            return True
        return now - beat > timedelta(minutes=LOCK_STALE_MINUTES)

    # Held by a client too old to send heartbeats: wall-clock fallback only.
    if LOCK_TTL_HOURS <= 0:
        return False
    locked_at = _parse_ts(lock.get("locked_at"))
    if locked_at is None:
        # A held lock with no usable timestamp can never age out on its own,
        # which is the exact situation this check exists to prevent.
        return True
    return now - locked_at > timedelta(hours=LOCK_TTL_HOURS)


def get_lock(data: dict, lock_id: str) -> dict:
    lock = data.get("locks", {}).get(lock_id)
    merged = _default_lock()
    if isinstance(lock, dict):
        merged.update(lock)
    if _expired(merged):
        # Drop last_hash too: see the release handler for why a hash that
        # survives a non-clean release can never match again.
        merged = _default_lock()
    return merged


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.environ["API_KEY"]:
        raise HTTPException(status_code=401, detail="Invalid API key")


class LockRequest(BaseModel):
    name: str
    lock_id: str = DEFAULT_ID
    token: str | None = None


class ReleaseRequest(BaseModel):
    hash: str | None = None
    lock_id: str = DEFAULT_ID
    token: str | None = None


class HeartbeatRequest(BaseModel):
    lock_id: str = DEFAULT_ID
    token: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def get_status(lock_id: str = Query(DEFAULT_ID)):
    with _state_lock:
        data = load_all()
        stored = data.get("locks", {}).get(lock_id)
        lock = get_lock(data, lock_id)
        # Persist the expiry so a dropped lease is cleaned up once rather than
        # being recomputed on every poll.
        if isinstance(stored, dict) and stored.get("locked") and not lock["locked"]:
            data.setdefault("locks", {})[lock_id] = lock
            save_all(data)
        # /status is unauthenticated, so never hand back the lease token --
        # holding it is what proves ownership on /heartbeat and DELETE /lock.
        return {k: v for k, v in lock.items() if k != "token"}


@app.get("/locks")
def list_locks(_=Depends(require_api_key)):
    """Admin view: every lock, how long since it was taken, how long since its
    last heartbeat, and whether the server already considers it dropped."""
    data = load_all()
    now = datetime.now(timezone.utc)

    def minutes_since(value) -> int | None:
        ts = _parse_ts(value)
        return None if ts is None else int((now - ts).total_seconds() // 60)

    out = {}
    for lock_id, raw in data.get("locks", {}).items():
        merged = _default_lock()
        if isinstance(raw, dict):
            merged.update(raw)
        out[lock_id] = {
            **{k: v for k, v in merged.items() if k != "token"},
            "age_minutes": minutes_since(merged.get("locked_at")),
            "heartbeat_age_minutes": minutes_since(merged.get("heartbeat_at")),
            "sends_heartbeats": merged.get("heartbeat_at") is not None,
            "expired": _expired(merged),
        }
    return {
        "stale_minutes": LOCK_STALE_MINUTES,
        "ttl_hours": LOCK_TTL_HOURS,
        "locks": out,
    }


@app.post("/lock")
def acquire_lock(req: LockRequest, _=Depends(require_api_key)):
    with _state_lock:
        data = load_all()
        lock = get_lock(data, req.lock_id)
        if lock["locked"]:
            raise HTTPException(status_code=409, detail=f"Locked by {lock['locked_by']}")
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("locks", {})[req.lock_id] = {
            "locked": True,
            "locked_by": req.name,
            "locked_at": now,
            "last_hash": lock.get("last_hash"),
            "token": req.token,
            # Only start a lease for a client that sent a token, since only
            # those can renew it. Leaving this None for older clients keeps
            # them on the wall-clock fallback instead of expiring them in
            # minutes for failing to send heartbeats they know nothing about.
            "heartbeat_at": now if req.token else None,
        }
        save_all(data)
        return {"ok": True}


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest, _=Depends(require_api_key)):
    """Renew the lease. The holder calls this on a timer for as long as the
    file is open; when the calls stop, the lock frees itself."""
    with _state_lock:
        data = load_all()
        lock = get_lock(data, req.lock_id)
        if not lock["locked"] or lock.get("token") != req.token:
            # Either the lease already lapsed and someone else took the file,
            # or this is a zombie from an earlier run. Tell it to stop rather
            # than letting it hold a lock that is no longer its own.
            raise HTTPException(status_code=409, detail="Lock is no longer held by you")
        lock["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        data.setdefault("locks", {})[req.lock_id] = lock
        save_all(data)
        return {"ok": True}


@app.delete("/lock")
def release_lock(req: ReleaseRequest | None = None, _=Depends(require_api_key)):
    with _state_lock:
        data = load_all()
        lock_id = req.lock_id if req else DEFAULT_ID
        token = req.token if req else None

        # A token means "release the lock I took". If the lease has since
        # lapsed and passed to someone else, refuse: a watcher thread left
        # over from a previous run must not yank the file out from under
        # whoever holds it now. A release with no token is the deliberate
        # "Release Lock & Open" override from the dialog, which is allowed to
        # clear anything.
        if token is not None:
            current = get_lock(data, lock_id)
            if current["locked"] and current.get("token") != token:
                raise HTTPException(
                    status_code=409, detail=f"Lock is now held by {current['locked_by']}"
                )

        data.setdefault("locks", {})[lock_id] = {
            "locked": False,
            "locked_by": None,
            "locked_at": None,
            # A clean close (file watcher) sends the hash of the file it just
            # saved, and that is the only hash worth keeping. A forced release
            # sends none -- and in that case the file has almost certainly been
            # edited since the stored hash was taken, so keeping it would make
            # the client's sync check compare against bytes that no longer
            # exist and wait forever for a sync that already finished. Clearing
            # it just skips the check for one open, which is the safe failure.
            "last_hash": req.hash if (req and req.hash) else None,
            "token": None,
            "heartbeat_at": None,
        }
        save_all(data)
        return {"ok": True}
