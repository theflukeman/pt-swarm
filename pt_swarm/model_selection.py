"""Turn a member's model request into a selector `polytoken exec --model` accepts.

The roster stores what a member asked for, which may be the sentinel ``default``.
`polytoken exec` rejects that sentinel ("model 'default' is not configured"), so the
request has to be resolved to something runnable before a worker process starts.

Resolution order, first match wins:

1. A concrete selector (a model name, a ``name(level)`` reasoning variant, or a
   model-group reference such as ``@mg:group``) passes through untouched.
2. The tier aliases ``full``, ``mini``, and ``nano`` become Polytoken's reserved
   tier groups.
3. ``session`` means the model the Polytoken session is running, recorded by the
   `model_hook` hook; it falls back to the role tier when nothing is recorded.
4. Unset, ``default``, and ``auto`` resolve to the member's role tier: workers do
   the work on the full tier, while the narrator and monitor -- polling loops that
   mostly read the board and write short posts -- run on the mini tier.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

TIER_GROUP_FULL = "polytoken:default_model_full"
TIER_GROUP_MINI = "polytoken:default_model_mini"
TIER_GROUP_NANO = "polytoken:default_model_nano"

FULL_SELECTOR = f"@mg:{TIER_GROUP_FULL}"
MINI_SELECTOR = f"@mg:{TIER_GROUP_MINI}"
NANO_SELECTOR = f"@mg:{TIER_GROUP_NANO}"

TIER_ALIASES = {
    "full": FULL_SELECTOR,
    "mini": MINI_SELECTOR,
    "nano": NANO_SELECTOR,
}

# Roles whose work is board polling and short status posts, not the task itself.
MINI_TIER_ROLES = {"monitor", "narrator"}

DEFAULT_REQUESTS = {"", "default", "auto"}
SESSION_REQUESTS = {"session"}

SESSION_MODEL_FILE = "session_model.json"


def tier_selector(role: Optional[str]) -> str:
    """The reserved tier group a role runs on when no model is requested."""
    return MINI_SELECTOR if (role or "").strip().lower() in MINI_TIER_ROLES else FULL_SELECTOR


def resolve_model(
    request: Optional[str] = None,
    role: str = "worker",
    session_model: Optional[str] = None,
) -> Tuple[str, str]:
    """Return ``(desired, applied)`` for a member's model request.

    ``desired`` is the request as recorded on the roster; ``applied`` is the
    selector handed to the worker process. ``applied`` is never the ``default``
    sentinel.
    """
    raw = "" if request is None else str(request).strip()
    alias = raw.lower()
    if alias in DEFAULT_REQUESTS:
        return "default", tier_selector(role)
    if alias in TIER_ALIASES:
        return raw, TIER_ALIASES[alias]
    if alias in SESSION_REQUESTS:
        return raw, session_model or tier_selector(role)
    return raw, raw


def session_model_path(swarm_dir) -> Path:
    return Path(swarm_dir) / SESSION_MODEL_FILE


def read_session_model(swarm_dir) -> Optional[str]:
    """The model recorded for the running session, or None when unknown."""
    try:
        data = json.loads(session_model_path(swarm_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    model = data.get("model") if isinstance(data, dict) else None
    return model.strip() if isinstance(model, str) and model.strip() else None


def write_session_model(swarm_dir, model: str) -> Path:
    """Record the running session's model so the server can offer it as a selector."""
    import datetime

    target = session_model_path(swarm_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model.strip(),
        "updated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target
