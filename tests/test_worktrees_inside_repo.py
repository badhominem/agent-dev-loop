"""The loop also works with worktrees inside the repo (.loop/worktrees), the setup Copilot CLI's folder trust needs."""

import subprocess
import sys
from pathlib import Path

from devloop.cli import main
from tests.conftest import git

FAKE = Path("tests/fixtures/fake_agent.py").resolve()


def test_worktrees_inside_repo(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\nbdd_features_base_dir = "loop/behaviour"\n'
    )
    c = repo / "loop" / "conventions.yaml"
    c.write_text(
        c.read_text()
        .replace("harness: claude", f'harness: "exec:{sys.executable} {FAKE} {{role}} {{mode}}"')
        .replace('worktree_root: "../{repo}-worktrees"', 'worktree_root: ".loop/worktrees"')
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    main(["install", "--harness", "copilot"])
    main(["onboard"])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "loop: onboard")
    main(["new", "Greeting by name"])
    main(["plan", "0001"])
    main(["approve", "0001"])
    main(["run", "0001"])
    out = capsys.readouterr().out
    assert "verdict: clean" in out, out
    wt = repo / ".loop" / "worktrees" / "0001-greeting-by-name"
    assert (wt / ".github" / "agents" / "loop-builder.agent.md").exists()
    assert git(repo, "status", "--porcelain") == ""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=repo, capture_output=True, text=True
    )
    assert "worktrees" not in r.stdout, r.stdout
