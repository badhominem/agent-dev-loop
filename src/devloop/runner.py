"""Starting each agent as its own fresh session, in the demand's worktree.

The harness is Claude Code, GitHub Copilot CLI, or a custom command. Each session
gets LOOP_ROLE, LOOP_DEMAND and LOOP_ROOT in its environment, which the guard hook
uses to enforce that role's write zones, and a .loop/session.yaml it reads first.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import yaml

from devloop.config import Conventions, git


class Harness(ABC):
    @abstractmethod
    def command(self, role: str, kickoff: str, interactive: bool) -> list[str]: ...


class ClaudeCode(Harness):
    def command(self, role: str, kickoff: str, interactive: bool) -> list[str]:
        if interactive:
            return ["claude", "--agent", f"loop-{role}", kickoff]
        return ["claude", "-p", "--agent", f"loop-{role}", "--permission-mode", "acceptEdits", kickoff]


class Copilot(Harness):
    def command(self, role: str, kickoff: str, interactive: bool) -> list[str]:
        if interactive:
            return ["copilot", "--agent", f"loop-{role}"]
        return ["copilot", "-p", kickoff, "--agent", f"loop-{role}", "--allow-all-tools", "--no-ask-user", "-s"]


class CustomCommand(Harness):
    """harness: "exec:<command with {role} and {kickoff}>" in conventions.yaml."""

    def __init__(self, template: str) -> None:
        self.template = template

    def command(self, role: str, kickoff: str, interactive: bool) -> list[str]:
        mode = "interactive" if interactive else "headless"
        return [
            part.replace("{role}", role).replace("{kickoff}", kickoff).replace("{mode}", mode)
            for part in shlex.split(self.template)
        ]


def harness_for(name: str) -> Harness:
    if name.startswith("exec:"):
        return CustomCommand(name.removeprefix("exec:"))
    if name == "copilot":
        return Copilot()
    if name == "claude":
        return ClaudeCode()
    raise SystemExit(f"Unknown harness '{name}'. Use claude, copilot or exec:<command>.")


KICKOFF = {
    "planner": "You are the planner for demand {demand}. Read .loop/session.yaml and start stage 1.",
    "outside-test-writer": "Write the outside tests for demand {demand}. Read .loop/session.yaml first.",
    "builder": "Build demand {demand} until its outside tests pass. Read .loop/session.yaml first.",
    "inside-test-writer": "Write inside tests for demand {demand}. Read .loop/session.yaml first.",
    "reviewer": "Review demand {demand}. Read .loop/session.yaml first.",
}


@dataclass(frozen=True)
class Session:
    role: str
    demand: str
    worktree: Path
    base: str
    round: int = 1


class Runner:
    def __init__(self, conventions: Conventions, harness: str | None = None, dry_run: bool = False) -> None:
        self.conventions = conventions
        self.harness = harness_for(harness or conventions.harness)
        self.dry_run = dry_run

    def run(self, session: Session, interactive: bool) -> int:
        folder = session.worktree / ".loop"
        folder.mkdir(exist_ok=True)
        info = {
            "role": session.role,
            "demand": session.demand,
            "base": session.base,
            "round": session.round,
            "worktree": str(session.worktree),
            "truth_branch": self.conventions.truth_branch,
            "started": time.strftime("%Y-%m-%dT%H:%M"),
        }
        (folder / "session.yaml").write_text(yaml.safe_dump(info, sort_keys=False), encoding="utf-8")
        kickoff = KICKOFF[session.role].format(demand=session.demand)
        command = self.harness.command(session.role, kickoff, interactive)
        env = {
            **os.environ,
            "LOOP_ROLE": session.role,
            "LOOP_DEMAND": session.demand,
            "LOOP_ROOT": str(session.worktree),
            "LOOP_INTERACTIVE": "1" if interactive else "",
        }
        print(f"[loop] {session.role} · {session.demand} · round {session.round}")
        print("[loop] " + " ".join(shlex.quote(c) for c in command))
        if self.dry_run:
            return 0
        code = subprocess.run(command, cwd=session.worktree, env=env, check=False).returncode
        if not interactive:
            self.commit_leftovers(session)
        return code

    @staticmethod
    def commit_leftovers(session: Session) -> None:
        if git(session.worktree, "status", "--porcelain"):
            git(session.worktree, "add", "-A")
            git(
                session.worktree, "commit", "-q", "-m", f"loop: {session.demand} {session.role} (round {session.round})"
            )
