"""Hook handler that records the active session model for the swarm server.

Polytoken sets ``POLYTOKEN_MODEL_NAME`` for hook handlers, but MCP stdio children
start with a cleared environment and never see it. A hook writing the name into the
swarm directory is how the server learns which model the session is running -- which
is what makes ``model="session"`` resolve to something real.

Wire it into ``hooks.json`` on ``session_start`` and ``pre_user_prompt`` so a model
switch mid-session is picked up:

    {"name": "swarm-session-model", "event": "session_start",
     "handler": {"bash": "python3 -m pt_swarm.model_hook; echo '{\"outcome\":\"allow\"}'"}}
"""

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from .model_selection import write_session_model

DEFAULT_SWARM_SUBPATH = (".swarm", "default")


def resolve_swarm_dir(explicit: Optional[str] = None) -> Path:
    """Where the swarm lives, preferring the most specific source available."""
    if explicit:
        return Path(explicit)
    env_dir = (os.environ.get("POLYTOKEN_SWARM_DIR") or "").strip()
    if env_dir:
        return Path(env_dir)
    project = (os.environ.get("POLYTOKEN_PROJECT_DIR") or "").strip() or os.getcwd()
    return Path(project).joinpath(*DEFAULT_SWARM_SUBPATH)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pt-swarm-model-hook",
        description="Record POLYTOKEN_MODEL_NAME into the swarm directory.",
    )
    parser.add_argument(
        "--swarm-dir",
        default=None,
        help="Swarm directory to write into (default: $POLYTOKEN_SWARM_DIR, "
        "then $POLYTOKEN_PROJECT_DIR/.swarm/default).",
    )
    args = parser.parse_args(argv)

    model = (os.environ.get("POLYTOKEN_MODEL_NAME") or "").strip()
    if not model:
        return 0
    swarm_dir = resolve_swarm_dir(args.swarm_dir)
    agent_name = (os.environ.get("POLYTOKEN_AGENT_NAME") or "").strip()
    if agent_name and agent_name != "coordinator":
        swarm_dir = swarm_dir / "workers" / agent_name
    try:
        write_session_model(swarm_dir, model)
    except OSError:
        # A hook must never block the session over a bookkeeping failure.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
