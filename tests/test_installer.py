import json
from pathlib import Path

from devloop.installer import install


def test_install_generates_both_harnesses(tmp_path: Path) -> None:
    written = install(tmp_path, ["claude", "copilot"])
    assert (tmp_path / "loop" / "principles.md").exists()
    assert (tmp_path / ".claude" / "agents" / "loop-builder.md").exists()
    assert (tmp_path / ".github" / "agents" / "loop-planner.agent.md").exists()
    hook = json.loads((tmp_path / ".github" / "hooks" / "loop-guard.json").read_text())
    assert hook["hooks"]["preToolUse"][0]["bash"] == "loop guard"
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "loop guard"
    assert "Bash(git status *)" in settings["permissions"]["allow"]
    assert "Bash(git push --force *)" in settings["permissions"]["deny"]
    assert ".loop/" in (tmp_path / ".gitignore").read_text()
    assert written


def test_install_twice_adds_no_duplicates_and_keeps_user_files(tmp_path: Path) -> None:
    install(tmp_path, ["claude"])
    (tmp_path / "loop" / "conventions.yaml").write_text("truth_branch: develop\n")
    install(tmp_path, ["claude"])
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["PreToolUse"]) == 1
    allow = settings["permissions"]["allow"]
    assert len(allow) == len(set(allow))
    assert (tmp_path / "loop" / "conventions.yaml").read_text() == "truth_branch: develop\n"


def test_existing_agents_md_gets_a_pointer(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Our agents\n\nUse tabs.\n")
    install(tmp_path, [])
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("# Our agents") and "loop/principles.md" in text


def test_repo_agent_override_wins(tmp_path: Path) -> None:
    (tmp_path / "loop" / "agents").mkdir(parents=True)
    (tmp_path / "loop" / "agents" / "builder.md").write_text("---\ndescription: Our builder\n---\nBuild carefully.\n")
    install(tmp_path, ["copilot"])
    text = (tmp_path / ".github" / "agents" / "loop-builder.agent.md").read_text()
    assert 'description: "Our builder"' in text and "Build carefully." in text
