"""The guard hook. Runs before every tool call in Claude Code and GitHub Copilot.

It reads the hook payload on stdin (Claude format with snake_case fields, or
Copilot's native camelCase format), then:

* blocks file writes outside the acting agent's zones (role from LOOP_ROLE),
* checks shell commands against loop/tools.toml,
* logs every decision to .loop/commands.log.

Blocking uses exit code 2 with the reason on stderr, which both harnesses treat
as a denied tool call. Allowing prints nothing and exits 0, so the harness's
normal permission flow still applies. Outside a `loop` session (no LOOP_ROLE)
only the tool registry's deny list is enforced.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from devloop.config import Conventions, ToolRegistry, as_dict, repo_root

SHELL_TOOLS = {"bash", "powershell", "shell", "run_in_terminal", "runinterminal"}
WRITE_TOOLS = {
    "write",
    "edit",
    "multiedit",
    "notebookedit",
    "create",
    "str_replace_editor",
    "apply_patch",
    "create_file",
    "replace_string_in_file",
    "insert_edit_into_file",
}
PATH_KEYS = ("file_path", "path", "notebook_path", "filePath", "filepath")
PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)
SEPARATORS = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
WRITE_COMMANDS = {"rm", "mv", "cp", "touch", "mkdir", "rmdir", "tee", "truncate"}


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, object]
    raw_args: object
    cwd: Path


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def parse_payload(raw: str) -> ToolCall:
    data = as_dict(json.loads(raw) if raw.strip() else {})
    tool = str(data.get("tool_name") or data.get("toolName") or "")
    raw_args: object = data["tool_input"] if "tool_input" in data else data.get("toolArgs")
    if isinstance(raw_args, str):
        with contextlib.suppress(json.JSONDecodeError):
            raw_args = json.loads(raw_args)
    cwd = Path(str(data.get("cwd") or os.getcwd()))
    return ToolCall(tool, as_dict(raw_args), raw_args, cwd)


class Guard:
    def __init__(self, root: Path, role: str, demand: str, interactive: bool) -> None:
        self.root = root.resolve()
        self.role = role
        self.demand = demand
        self.interactive = interactive
        self.conventions = Conventions.load(root)
        self.registry = ToolRegistry.load(root)

    # ---- decisions

    def check(self, call: ToolCall) -> Decision:
        name = call.tool.lower()
        if name in SHELL_TOOLS:
            return self._check_command(str(call.args.get("command", "")), call.cwd)
        if name in WRITE_TOOLS:
            return self._check_paths(self._write_paths(call), call.cwd)
        return Decision(True)

    def _check_command(self, command: str, cwd: Path) -> Decision:
        for part in (p.strip() for p in SEPARATORS.split(command) if p.strip()):
            simple = self._strip_env(part)
            verdict = self.registry.decide(simple)
            if verdict == "deny":
                return Decision(False, f"'{simple}' is denied by loop/tools.toml.")
            if verdict == "ask" and self.role and not self.interactive:
                return Decision(
                    False, f"'{simple}' needs a human yes (loop/tools.toml). Stop and report yourself blocked instead."
                )
            if self.role:
                targets = self._command_targets(part)
                if targets is None:
                    return Decision(False, f"Couldn't read '{part}' safely. Use a simpler command.")
                found = self._check_paths(targets, cwd)
                if not found.allowed:
                    return found
        return Decision(True)

    def _check_paths(self, paths: list[str], cwd: Path) -> Decision:
        if not self.role:
            return Decision(True)
        zones = self.conventions.zones_for(self.role, self.demand)
        for raw in paths:
            target = (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
            try:
                rel = target.relative_to(self.root).as_posix()
            except ValueError:
                return Decision(False, f"{raw} is outside this demand's worktree.")
            if not any(self._in_zone(rel, z) for z in zones):
                where = ", ".join(zones) or "nowhere"
                return Decision(False, f"The {self.role} may not write {rel}. Allowed: {where}.")
        return Decision(True)

    @staticmethod
    def _in_zone(rel: str, zone: str) -> bool:
        if zone.endswith("/"):
            return rel == zone.rstrip("/") or rel.startswith(zone)
        return rel == zone

    # ---- reading arguments

    @staticmethod
    def _write_paths(call: ToolCall) -> list[str]:
        paths = [str(call.args[k]) for k in PATH_KEYS if k in call.args]
        texts = [call.raw_args] if isinstance(call.raw_args, str) else [str(v) for v in call.args.values()]
        for text in texts:
            if isinstance(text, str):
                paths += [a or b for a, b in PATCH_PATH.findall(text)]
        return [p.strip() for p in paths if p.strip()]

    @staticmethod
    def _strip_env(part: str) -> str:
        words = part.split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
            words = words[1:]
        return " ".join(words)

    @staticmethod
    def _command_targets(part: str) -> list[str] | None:
        """Best effort: files a simple shell command would write. None if unreadable."""
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            return None
        if any(w.startswith(("$(", "`")) for w in words) or "`" in part or "$(" in part:
            return None
        targets: list[str] = []
        for i, word in enumerate(words):
            if word in (">", ">>") and i + 1 < len(words):
                targets.append(words[i + 1])
            elif word.startswith((">", ">>")) and len(word.lstrip(">")) > 0:
                targets.append(word.lstrip(">"))
        if words and words[0] in WRITE_COMMANDS:
            args = [w for w in words[1:] if not w.startswith("-") and w not in (">", ">>")]
            targets += args[-1:] if words[0] in ("mv", "cp") else args
        if words[:2] == ["sed", "-i"] or (words and words[0] == "sed" and "-i" in words):
            targets += [w for w in words[2:] if not w.startswith("-")][-1:]
        return [t for t in targets if t not in ("/dev/null", "&1", "&2")]

    # ---- logging

    def log(self, call: ToolCall, decision: Decision) -> None:
        folder = self.root / ".loop"
        folder.mkdir(exist_ok=True)
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "role": self.role or "-",
            "demand": self.demand or "-",
            "tool": call.tool,
            "command": str(call.args.get("command", "")) or None,
            "allowed": decision.allowed,
            "reason": decision.reason or None,
        }
        with (folder / "commands.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def run(stdin: str, env: dict[str, str]) -> tuple[int, str]:
    """Return (exit code, stderr text). Separated from main() for testing."""
    call = parse_payload(stdin)
    root = Path(env["LOOP_ROOT"]) if env.get("LOOP_ROOT") else repo_root(call.cwd)
    guard = Guard(
        root, env.get("LOOP_ROLE", ""), env.get("LOOP_DEMAND", ""), interactive=env.get("LOOP_INTERACTIVE", "") == "1"
    )
    decision = guard.check(call)
    with contextlib.suppress(OSError):
        guard.log(call, decision)
    return (0, "") if decision.allowed else (2, f"Blocked by the loop guard: {decision.reason}")


def main() -> None:
    code, message = run(sys.stdin.read(), dict(os.environ))
    if message:
        print(message, file=sys.stderr)
    sys.exit(code)
