"""Provider environment variables that ``polytoken`` itself needs, derived not listed.

Polytoken's global config references credentials by name (``api_key: $SOME_API_KEY``).
MCP stdio children start with a *cleared* environment -- only ``HOME``, ``PATH``,
``LANG``, and ``TMPDIR`` by default -- so a child's own ``polytoken`` cannot load that
config, and every worker it spawns dies at startup with ``config load failed``.

Which names are required differs per install, so nothing here hardcodes a provider. The
names are read out of the config files themselves, and the caller declares them in the
swarm server's ``pass_env:``:

    python3 -m pt_swarm.env_vars            # the pass_env block to paste
    python3 -m pt_swarm.env_vars --names    # just the names, one per line
"""

import argparse
import os
import re
from pathlib import Path
from typing import List, Optional, Sequence

# Passed to stdio children already, so declaring them again says nothing new.
DEFAULT_PASSED = {"HOME", "PATH", "LANG", "TMPDIR", "PWD"}

ENV_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def default_config_paths() -> List[Path]:
    """The polytoken config files an install may keep its provider references in."""
    paths: List[Path] = []
    explicit = (os.environ.get("POLYTOKEN_CONFIG") or "").strip()
    if explicit:
        paths.append(Path(explicit))
    config_home = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(config_home) if config_home else Path.home() / ".config"
    paths.append(base / "polytoken" / "config.yaml")
    paths.append(Path.cwd() / ".polytoken" / "config.yaml")
    return paths


def referenced_env_names(paths: Optional[Sequence] = None) -> List[str]:
    """Every env var the polytoken configs reference, sorted and deduplicated."""
    names = set()
    for path in (paths if paths is not None else default_config_paths()):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        for match in ENV_REF_RE.finditer(text):
            name = match.group(1)
            if name not in DEFAULT_PASSED:
                names.add(name)
    return sorted(names)


def pass_env_block(names: Sequence[str], indent: int = 4) -> str:
    """The YAML to paste under ``mcp_servers.<name>`` in a polytoken config."""
    if not names:
        return ""
    pad = " " * indent
    items = "".join(f"{pad}  - {name}\n" for name in names)
    return f"{pad}pass_env:\n{items}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pt-swarm-env-vars",
        description="List the env vars the polytoken config references, for pass_env.",
    )
    parser.add_argument(
        "--names", action="store_true", help="print bare names, one per line"
    )
    parser.add_argument(
        "--config",
        action="append",
        default=None,
        help="config file to scan (repeatable; defaults to the standard locations)",
    )
    args = parser.parse_args(argv)

    names = referenced_env_names(args.config)
    if args.names:
        if names:
            print("\n".join(names))
        return 0
    if not names:
        print("# no env-var references found in the polytoken config")
        return 0
    print(pass_env_block(names), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
