"""
Lightweight Terminal CLI viewer for Swarm Boards.
Usage:
  python3 -m polytoken_swarm.cli [--swarm-dir .swarm/default] [--watch]
"""

import argparse
import json
import time
from pathlib import Path
from .board_manager import BoardManager


def print_message(m):
    seq = m.get("seq", 0)
    agent = m.get("agent", "unknown")
    text = m.get("text", "")
    t = m.get("time", "")
    print(f"\033[1;36m#{seq}\033[0m [\033[1;33m{agent}\033[0m] (\033[90m{t}\033[0m):\n{text}\n{'-'*60}")


def main():
    parser = argparse.ArgumentParser(description="Swarm Terminal Board Viewer")
    parser.add_argument("--swarm-dir", default="./.swarm/default", help="Path to swarm directory")
    parser.add_argument("--watch", "-w", action="store_true", help="Keep watching for new messages")
    parser.add_argument("--status", "-s", action="store_true", help="Print roster status")
    args = parser.parse_args()

    bm = BoardManager(Path(args.swarm_dir))

    if args.status:
        members = bm.read_members().get("members", {})
        print(f"\n--- Swarm Roster ({len(members)} members) ---")
        for name, m in members.items():
            state = m.get("wake_mode", "unknown")
            role = m.get("role", "worker")
            read_seq = m.get("last_read_seq", 0)
            print(f" • \033[1m{name}\033[0m ({role}): state={state}, last_read=#{read_seq}")
        print()
        if not args.watch:
            return

    last_seq = 0
    msgs = bm.read_raw_messages()
    for m in msgs:
        print_message(m)
        last_seq = max(last_seq, m.get("seq", 0))

    if args.watch:
        print("\033[90mWatching for new messages... (Ctrl+C to exit)\033[0m")
        while True:
            time.sleep(1.0)
            new_msgs = [m for m in bm.read_raw_messages() if m.get("seq", 0) > last_seq]
            for m in new_msgs:
                print_message(m)
                last_seq = max(last_seq, m.get("seq", 0))


if __name__ == "__main__":
    main()
