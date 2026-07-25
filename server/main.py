import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

STATE_FILE = Path("lock_state.json")
_state_lock = threading.Lock()

# Lock id used when a client doesn't send one (older exes, or a config.ini
# with no [file] id). Keeps a single shared file working exactly as before.
DEFAULT_ID = "default"

app = FastAPI(title="LockLauncher")


def _default_lock() -> dict:
    return {"locked": False, "locked_by": None, "locked_at": None, "last_hash": None}


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


def get_lock(data: dict, lock_id: str) -> dict:
    lock = data.get("locks", {}).get(lock_id)
    merged = _default_lock()
    if isinstance(lock, dict):
        merged.update(lock)
    return merged


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.environ["API_KEY"]:
        raise HTTPException(status_code=401, detail="Invalid API key")


class LockRequest(BaseModel):
    name: str
    lock_id: str = DEFAULT_ID


class ReleaseRequest(BaseModel):
    hash: str | None = None
    lock_id: str = DEFAULT_ID


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def get_status(lock_id: str = Query(DEFAULT_ID)):
    return get_lock(load_all(), lock_id)


@app.post("/lock")
def acquire_lock(req: LockRequest, _=Depends(require_api_key)):
    with _state_lock:
        data = load_all()
        lock = get_lock(data, req.lock_id)
        if lock["locked"]:
            raise HTTPException(status_code=409, detail=f"Locked by {lock['locked_by']}")
        data.setdefault("locks", {})[req.lock_id] = {
            "locked": True,
            "locked_by": req.name,
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "last_hash": lock.get("last_hash"),
        }
        save_all(data)
        return {"ok": True}


@app.delete("/lock")
def release_lock(req: ReleaseRequest | None = None, _=Depends(require_api_key)):
    with _state_lock:
        data = load_all()
        lock_id = req.lock_id if req else DEFAULT_ID
        lock = get_lock(data, lock_id)
        data.setdefault("locks", {})[lock_id] = {
            "locked": False,
            "locked_by": None,
            "locked_at": None,
            # A clean close (file watcher) sends the hash of the file it just
            # saved. A forced release (stale lock override) sends none, so
            # the previously recorded hash is left in place.
            "last_hash": req.hash if (req and req.hash) else lock.get("last_hash"),
        }
        save_all(data)
        return {"ok": True}
