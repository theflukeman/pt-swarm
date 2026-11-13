"""
Workspace Manager for Swarm Agents.

Handles workspace isolation modes:
- inherit: Shares the parent workspace root.
- share: Creates a git worktree for parallel non-colliding writes.
- branch: Creates an isolated git clone.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Optional


class WorkspaceManager:
    def __init__(self, repo_root: Path, worktrees_dir: Optional[Path] = None):
        self.repo_root = Path(repo_root).resolve()
        self.worktrees_dir = (
            Path(worktrees_dir).resolve()
            if worktrees_dir
            else self.repo_root / ".swarm" / "workspaces"
        )
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def _is_git_repo(self) -> bool:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"

    def provision_workspace(self, agent_name: str, mode: str = "inherit") -> Path:
        """
        Provisions a workspace for an agent based on the requested mode.
        """
        if mode == "inherit":
            return self.repo_root

        target_dir = self.worktrees_dir / agent_name
        copy_ignore = shutil.ignore_patterns(".git", ".swarm", "node_modules", ".venv", "__pycache__")

        if mode == "share":
            if not target_dir.exists():
                if self._is_git_repo():
                    branch_name = f"swarm/{agent_name}"
                    has_branch = (
                        subprocess.run(
                            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                            cwd=str(self.repo_root),
                            capture_output=True,
                        ).returncode
                        == 0
                    )
                    cmd = (
                        ["git", "worktree", "add", str(target_dir), branch_name]
                        if has_branch
                        else ["git", "worktree", "add", "-b", branch_name, str(target_dir)]
                    )
                    res = subprocess.run(cmd, cwd=str(self.repo_root), capture_output=True, text=True)
                    if res.returncode != 0:
                        shutil.copytree(self.repo_root, target_dir, ignore=copy_ignore)
                else:
                    shutil.copytree(self.repo_root, target_dir, ignore=copy_ignore)
            return target_dir

        elif mode == "branch":
            if not target_dir.exists():
                if self._is_git_repo():
                    cmd = ["git", "clone", str(self.repo_root), str(target_dir)]
                    res = subprocess.run(cmd, cwd=str(self.repo_root), capture_output=True, text=True)
                    if res.returncode != 0:
                        shutil.copytree(self.repo_root, target_dir, ignore=copy_ignore)
                else:
                    shutil.copytree(self.repo_root, target_dir, ignore=copy_ignore)
            return target_dir

        return self.repo_root

    def cleanup_workspace(self, agent_name: str, mode: str):
        """
        Cleans up provisioned worktree or directory when an agent is killed.
        """
        if mode == "inherit":
            return

        target_dir = self.worktrees_dir / agent_name
        if mode == "share" and self._is_git_repo():
            if target_dir.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(target_dir)],
                    cwd=str(self.repo_root),
                    capture_output=True,
                )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(self.repo_root),
                capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", f"swarm/{agent_name}"],
                cwd=str(self.repo_root),
                capture_output=True,
            )
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
