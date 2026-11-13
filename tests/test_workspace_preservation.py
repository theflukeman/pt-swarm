import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pt_swarm.workspace_manager import WorkspaceManager


class WorkspacePreservationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _init_git_repo(self):
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.root, check=True, capture_output=True)

    def test_cleanup_preserves_clean_clone_with_worker_only_commit(self):
        self._init_git_repo()
        manager = WorkspaceManager(self.root)
        workspace = manager.provision_workspace("worker-commit", mode="branch")
        (workspace / "worker.txt").write_text("worker work\n", encoding="utf-8")
        subprocess.run(["git", "add", "worker.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "worker work"], cwd=workspace, check=True, capture_output=True)

        manager.cleanup_workspace("worker-commit", mode="branch")

        self.assertTrue((workspace / "worker.txt").exists())

    def test_cleanup_preserves_share_worktree_with_ignored_worker_file(self):
        self._init_git_repo()
        (self.root / ".gitignore").write_text("worker-output/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "ignore worker output"], cwd=self.root, check=True, capture_output=True)
        manager = WorkspaceManager(self.root)
        workspace = manager.provision_workspace("ignored-share", mode="share")
        ignored_file = workspace / "worker-output" / "result.txt"
        ignored_file.parent.mkdir()
        ignored_file.write_text("worker output\n", encoding="utf-8")

        manager.cleanup_workspace("ignored-share", mode="share")

        self.assertEqual(ignored_file.read_text(encoding="utf-8"), "worker output\n")

    def test_cleanup_preserves_clone_with_ignored_worker_file(self):
        self._init_git_repo()
        (self.root / ".gitignore").write_text("worker-output/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "ignore worker output"], cwd=self.root, check=True, capture_output=True)
        manager = WorkspaceManager(self.root)
        workspace = manager.provision_workspace("ignored-clone", mode="branch")
        ignored_file = workspace / "worker-output" / "result.txt"
        ignored_file.parent.mkdir()
        ignored_file.write_text("worker output\n", encoding="utf-8")

        manager.cleanup_workspace("ignored-clone", mode="branch")

        self.assertEqual(ignored_file.read_text(encoding="utf-8"), "worker output\n")

    def test_cleanup_preserves_edited_non_git_share_copy(self):
        (self.root / "base.txt").write_text("base\n", encoding="utf-8")
        manager = WorkspaceManager(self.root)
        workspace = manager.provision_workspace("fallback-edit", mode="share")
        (workspace / "worker.txt").write_text("worker edit\n", encoding="utf-8")

        manager.cleanup_workspace("fallback-edit", mode="share")

        self.assertEqual((workspace / "worker.txt").read_text(encoding="utf-8"), "worker edit\n")


if __name__ == "__main__":
    unittest.main()
