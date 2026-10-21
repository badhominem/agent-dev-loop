"""Loading the repo's loop configuration: conventions, tool registry and worktree setup."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLES = ("planner", "outside-test-writer", "builder", "inside-test-writer", "reviewer")

DEFAULT_ZONES: dict[str, tuple[str, ...]] = {
    "planner": ("loop/demands/{demand}/", "loop/behaviour/", "loop/context.yaml"),
    "outside-test-writer": ("tests/acceptance/", "loop/demands/{demand}/tests.yaml"),
    "builder": ("{source}/", "loop/components/", "loop/demands/{demand}/build.yaml"),
    "inside-test-writer": ("tests/unit/",),
    "reviewer": (".loop/",),
}

DEFAULT_TEST_COMMAND = ("python", "-m", "pytest", "-q", "-p", "no:warnings", "tests/acceptance", "--junitxml={junit}")


def as_dict(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def as_strs(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return as_dict(loaded)


@dataclass(frozen=True)
class TableRule:
    """A call that touches a table, e.g. storage.read_rows("raw.orders") -> reads."""

    call: str
    arg: int
    mode: str  # "reads" or "writes"


@dataclass(frozen=True)
class Conventions:
    truth_branch: str = "main"
    source_roots: tuple[str, ...] = ()
    component_level: str = "auto"
    pr_size_limit: int = 400
    inside_tests: bool = False
    harness: str = "claude"
    table_rules: tuple[TableRule, ...] = ()
    sql_tables: bool = False
    view_max_nodes: int = 30
    max_back_rounds: int = 3
    test_command: tuple[str, ...] = DEFAULT_TEST_COMMAND
    worktree_root: str = "../{repo}-worktrees"
    zones: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_ZONES))

    @classmethod
    def load(cls, repo: Path) -> Conventions:
        data = read_yaml(repo / "loop" / "conventions.yaml")
        rules = tuple(
            TableRule(str(r.get("call", "")), int(str(r.get("arg", 0))), str(r.get("mode", "reads")))
            for r in (as_dict(x) for x in as_list(data.get("table_rules")) if isinstance(x, dict))
        )
        zones = dict(DEFAULT_ZONES)
        zones.update({k: as_strs(v) for k, v in as_dict(data.get("zones")).items()})
        roots = as_strs(data.get("source_roots")) or detect_source_roots(repo)
        return cls(
            truth_branch=str(data.get("truth_branch", "main")),
            source_roots=roots,
            component_level=str(data.get("component_level", "auto")),
            pr_size_limit=int(str(data.get("pr_size_limit", 400))),
            inside_tests=bool(data.get("inside_tests", False)),
            harness=str(data.get("harness", "claude")),
            table_rules=rules,
            sql_tables=bool(data.get("sql_tables", False)),
            view_max_nodes=int(str(data.get("view_max_nodes", 30))),
            max_back_rounds=int(str(data.get("max_back_rounds", 3))),
            test_command=as_strs(data.get("test_command")) or DEFAULT_TEST_COMMAND,
            worktree_root=str(data.get("worktree_root", "../{repo}-worktrees")),
            zones=zones,
        )

    def zones_for(self, role: str, demand: str) -> tuple[str, ...]:
        out: list[str] = []
        for pattern in self.zones.get(role, ()):
            if "{source}" in pattern:
                out += [pattern.replace("{source}", r) for r in self.source_roots]
            else:
                out.append(pattern.replace("{demand}", demand or "_none_"))
        return tuple(out)


def detect_source_roots(repo: Path) -> tuple[str, ...]:
    """src/ layout if present, otherwise top-level folders that are Python packages."""
    if (repo / "src").is_dir():
        return ("src",)
    skip = {"tests", "test", "docs", "loop", ".loop", ".git", ".github", ".claude", "node_modules"}
    roots = sorted(p.name for p in repo.iterdir() if p.is_dir() and p.name not in skip and (p / "__init__.py").exists())
    return tuple(roots) or ("src",)


@dataclass(frozen=True)
class ToolRule:
    name: str
    description: str
    allow: tuple[str, ...]
    ask: tuple[str, ...]
    deny: tuple[str, ...]


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolRule, ...]

    @classmethod
    def load(cls, repo: Path) -> ToolRegistry:
        path = repo / "loop" / "tools.toml"
        if not path.exists():
            return cls(())
        raw = as_dict(tomllib.loads(path.read_text(encoding="utf-8")).get("tools"))
        tools = tuple(
            ToolRule(
                name,
                str(as_dict(entry).get("description", "")),
                as_strs(as_dict(entry).get("allow")),
                as_strs(as_dict(entry).get("ask")),
                as_strs(as_dict(entry).get("deny")),
            )
            for name, entry in raw.items()
        )
        return cls(tools)

    def decide(self, command: str) -> str:
        """Return "deny", "ask" or "allow" for one simple command (no chaining).

        The longest matching prefix wins, so "python -m pytest" (allow) beats
        "python" (ask). On a tie, deny beats ask beats allow. Unlisted means ask.
        """
        rank = {"deny": 0, "ask": 1, "allow": 2}
        best: tuple[int, int, str] | None = None
        for tool in self.tools:
            for verdict, prefixes in (("deny", tool.deny), ("ask", tool.ask), ("allow", tool.allow)):
                for p in prefixes:
                    if command == p or command.startswith(p + " "):
                        key = (-len(p), rank[verdict], verdict)
                        best = key if best is None or key < best else best
        return best[2] if best else "ask"


@dataclass(frozen=True)
class WorktreeSetup:
    copy: tuple[str, ...]
    setup: tuple[str, ...]

    @classmethod
    def load(cls, repo: Path) -> WorktreeSetup:
        path = repo / "loop" / "worktree.toml"
        if not path.exists():
            return cls((), ())
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(as_strs(data.get("copy")), as_strs(data.get("setup")))


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel"))


def main_checkout(start: Path) -> Path:
    """The primary checkout (not a demand worktree): where status files are committed."""
    common = Path(git(start, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent
