"""The demand queue and its lifecycle, including one worktree per claimed demand.

Status files live on the truth branch in the main checkout, so everyone sees the
queue. All other work for a demand happens in its own worktree and branch (P6).

Lifecycle: new -> claimed <-> returned -> approved -> closed, or rejected.
A reset sends a claimed or approved demand back to new and removes its worktree.
"""

from __future__ import annotations

import getpass
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from devloop.config import Conventions, WorktreeSetup, git, main_checkout, read_yaml

TRANSITIONS: dict[str, set[str]] = {
    "new": {"claimed", "rejected"},
    "claimed": {"returned", "approved", "rejected", "new"},
    "returned": {"claimed", "rejected", "new"},
    "approved": {"closed", "rejected", "new", "claimed"},
    "closed": set(),
    "rejected": set(),
}


@dataclass(frozen=True)
class Demand:
    id: str
    folder: Path
    status: dict[str, object]

    @property
    def state(self) -> str:
        return str(self.status.get("state", "new"))

    @property
    def branch(self) -> str:
        return f"demand/{self.id}"


class Queue:
    def __init__(self, start: Path) -> None:
        self.main = main_checkout(start)
        self.conventions = Conventions.load(self.main)
        self.root = self.main / "loop" / "demands"

    # ---- reading

    def all(self) -> list[Demand]:
        if not self.root.exists():
            return []
        return [self._load(p) for p in sorted(self.root.iterdir()) if (p / "status.yaml").exists()]

    def get(self, ref: str) -> Demand:
        matches = [d for d in self.all() if d.id == ref or d.id.startswith(ref + "-") or d.id[:4] == ref]
        if len(matches) != 1:
            raise SystemExit(f"No single demand matches '{ref}'. Run `loop status` to see the queue.")
        return matches[0]

    def _load(self, folder: Path) -> Demand:
        return Demand(folder.name, folder, read_yaml(folder / "status.yaml"))

    def worktree_path(self, demand: Demand) -> Path:
        base = self.conventions.worktree_root.replace("{repo}", self.main.name)
        return (self.main / base / demand.id).resolve()

    # ---- writing status on the truth branch

    def _require_truth_branch(self) -> None:
        current = git(self.main, "branch", "--show-current")
        if current != self.conventions.truth_branch:
            raise SystemExit(
                f"The main checkout is on '{current}'. Switch it to "
                f"'{self.conventions.truth_branch}' so status changes land there."
            )

    def _write_status(self, demand: Demand, state: str, message: str, **extra: object) -> Demand:
        if state not in TRANSITIONS.get(demand.state, set()):
            raise SystemExit(f"{demand.id} is '{demand.state}' and can't move to '{state}'.")
        self._require_truth_branch()
        status = dict(demand.status)
        status["state"] = state
        status[f"{state}_at"] = time.strftime("%Y-%m-%dT%H:%M")
        status.update({k: v for k, v in extra.items() if v is not None})
        path = demand.folder / "status.yaml"
        path.write_text(yaml.safe_dump(status, sort_keys=False), encoding="utf-8")
        git(self.main, "add", str(path))
        git(self.main, "commit", "-q", "-m", f"loop: {demand.id} {message}", "--", str(path))
        return self._load(demand.folder)

    # ---- lifecycle

    def new(self, title: str, text: str = "") -> Demand:
        self._require_truth_branch()
        numbers = [int(d.id[:4]) for d in self.all() if d.id[:4].isdigit()]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
        folder = self.root / f"{max(numbers, default=0) + 1:04d}-{slug}"
        folder.mkdir(parents=True)
        (folder / "demand.md").write_text(
            f"# {title}\n\n{text or 'Describe the change you want, in words or visuals.'}\n", encoding="utf-8"
        )
        status = {"state": "new", "new_at": time.strftime("%Y-%m-%dT%H:%M"), "by": getpass.getuser()}
        (folder / "status.yaml").write_text(yaml.safe_dump(status, sort_keys=False), encoding="utf-8")
        git(self.main, "add", str(folder))
        git(self.main, "commit", "-q", "-m", f"loop: new demand {folder.name}", "--", str(folder))
        return self._load(folder)

    def claim(self, ref: str, who: str | None = None) -> tuple[Demand, Path, list[str]]:
        """Create the demand's worktree and branch, prepare it, and mark it claimed."""
        demand = self.get(ref)
        if demand.state not in ("new", "returned"):
            raise SystemExit(f"{demand.id} is '{demand.state}'; only new or returned demands can be claimed.")
        path = self.worktree_path(demand)
        demand = self._write_status(
            demand, "claimed", "claimed", claimed_by=who or getpass.getuser(), worktree=str(path), branch=demand.branch
        )
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            has_branch = bool(git(self.main, "branch", "--list", demand.branch))
            if has_branch:
                git(self.main, "worktree", "add", str(path), demand.branch)
            else:
                git(self.main, "worktree", "add", "-b", demand.branch, str(path), self.conventions.truth_branch)
        return demand, path, self._prepare(path)

    def _prepare(self, path: Path) -> list[str]:
        setup = WorktreeSetup.load(self.main)
        notes: list[str] = []
        for item in setup.copy:
            source = self.main / item
            if source.is_dir():
                shutil.copytree(source, path / item, dirs_exist_ok=True)
                notes.append(f"copied {item}/")
            elif source.exists():
                (path / item).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, path / item)
                notes.append(f"copied {item}")
        for command in setup.setup:
            result = subprocess.run(command, shell=True, cwd=path, capture_output=True, text=True, check=False)
            outcome = "ok" if result.returncode == 0 else f"failed ({result.returncode})"
            notes.append(f"ran `{command}`: {outcome}")
        return notes

    def approve(self, ref: str) -> Demand:
        """Bring the branch up to date with the truth branch and record the plan's base commit."""
        demand = self.get(ref)
        path = self.worktree_path(demand)
        if not path.exists():
            raise SystemExit(f"{demand.id} has no worktree. Claim it first.")
        dirty = git(path, "status", "--porcelain")
        if dirty:
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", f"loop: {demand.id} stage 1 plan")
        truth = self.conventions.truth_branch
        result = subprocess.run(["git", "rebase", truth], cwd=path, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=path, capture_output=True, check=False)
            raise SystemExit(f"{demand.id} conflicts with the latest {truth}. Resolve it in {path} first.")
        base = git(self.main, "rev-parse", "--short", truth)
        return self._write_status(demand, "approved", f"approved on base {base}", base=base)

    def set_state(self, ref: str, state: str, reason: str | None = None) -> Demand:
        demand = self.get(ref)
        if state == "new":
            return self.reset(ref, reason)
        return self._write_status(demand, state, state, reason=reason)

    def reset(self, ref: str, reason: str | None = None) -> Demand:
        """Back to new: remove the worktree and branch. Drafts in the branch are discarded."""
        demand = self.get(ref)
        self._remove_worktree(demand)
        status = {k: v for k, v in demand.status.items() if k in ("by", "new_at")}
        return self._write_status(
            Demand(demand.id, demand.folder, {**status, "state": demand.state}), "new", "reset to new", reason=reason
        )

    def close(self, ref: str, merged: str) -> Demand:
        demand = self.get(ref)
        closed = self._write_status(demand, "closed", f"closed by {merged}", merged=merged)
        self._remove_worktree(demand, keep_branch=True)
        return closed

    def _remove_worktree(self, demand: Demand, keep_branch: bool = False) -> None:
        path = self.worktree_path(demand)
        if path.exists():
            git(self.main, "worktree", "remove", "--force", str(path))
        if not keep_branch and git(self.main, "branch", "--list", demand.branch):
            git(self.main, "branch", "-D", demand.branch)
