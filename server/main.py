import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

STATE_FILE = Path("lock_state.json")
_state_lock = threading.Lock()

app = FastAPI(title="LockLauncher")


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            state.setdefault("last_hash", None)
            return state
    except (json.JSONDecodeError, OSError):
        pass
    return {"locked": False, "locked_by": None, "locked_at": None, "last_hash": None}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.environ["API_KEY"]:
        raise HTTPException(status_code=401, detail="Invalid API key")


class LockRequest(BaseModel):
    name: str


class ReleaseRequest(BaseModel):
    hash: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def get_status():
    return load_state()


@app.post("/lock")
def acquire_lock(req: LockRequest, _=Depends(require_api_key)):
    with _state_lock:
        state = load_state()
        if state["locked"]:
            raise HTTPException(status_code=409, detail=f"Locked by {state['locked_by']}")
        new_state = {
            "locked": True,
            "locked_by": req.name,
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "last_hash": state.get("last_hash"),
        }
        save_state(new_state)
        return {"ok": True}


@app.delete("/lock")
def release_lock(req: ReleaseRequest | None = None, _=Depends(require_api_key)):
    with _state_lock:
        state = load_state()
        save_state({
            "locked": False,
            "locked_by": None,
            "locked_at": None,
            # A clean close (file watcher) sends the hash of the file it just
            # saved. A forced release (stale lock override) sends none, so
            # the previously recorded hash is left in place.
            "last_hash": req.hash if (req and req.hash) else state.get("last_hash"),
        })
        return {"ok": True}
