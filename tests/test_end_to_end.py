import os
import sys
from pathlib import Path

import pytest
import yaml

from devloop.cli import main
from tests.conftest import git

FAKE = Path(__file__).parent / "fixtures" / "fake_agent.py"


def test_the_whole_loop_with_a_stand_in_harness(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\nbdd_features_base_dir = "loop/behaviour"\n'
    )
    conventions = repo / "loop" / "conventions.yaml"
    conventions.write_text(
        conventions.read_text().replace("harness: claude", f'harness: "exec:{sys.executable} {FAKE} {{role}} {{mode}}"')
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "project setup")

    main(["install", "--harness", "claude,copilot"])
    main(["onboard"])
    stub = yaml.safe_load((repo / "loop/components/src.app.core.yaml").read_text())
    assert stub["uncertain"] is True and stub["boundary"] == "app"
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "loop: onboard")

    main(["new", "Greeting by name", "--text", "Greet people by name."])
    main(["plan", "0001"])
    main(["approve", "0001"])
    main(["run", "0001"])

    out = capsys.readouterr().out
    assert "verdict: clean" in out, out
    worktree = repo.parent / "proj-worktrees" / "0001-greeting-by-name"
    report = worktree / ".loop" / "0001-greeting-by-name"
    pr = (report / "pr.md").read_text()
    assert "The greeting was built as planned." in pr
    assert "Used an f-string" in pr
    assert "greeting-01" in (report / "behaviour.json").read_text()
    log = git(worktree, "log", "--format=%s", "-6")
    assert "outside-test-writer" in log and "builder" in log
    assert os.path.exists(report / "report.html")


def test_dry_run_prints_agent_commands(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    main(["new", "Something"])
    main(["claim", "0001"])
    main(["state", "0001", "returned", "--reason", "too vague"])
    main(["claim", "0001"])
    main(["plan", "0001", "--dry-run", "--harness", "copilot"])
    out = capsys.readouterr().out
    assert "copilot --agent loop-planner" in out
    main(["status"])
    assert "claimed" in capsys.readouterr().out
