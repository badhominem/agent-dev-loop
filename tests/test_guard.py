import json
from pathlib import Path

from devloop.guard import run


def claude(tool: str, cwd: Path, **tool_input: object) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)})


def copilot(tool: str, cwd: Path, **args: object) -> str:
    return json.dumps({"toolName": tool, "toolArgs": json.dumps(args), "cwd": str(cwd)})


def env(repo: Path, role: str = "builder", interactive: bool = False) -> dict[str, str]:
    return {
        "LOOP_ROOT": str(repo),
        "LOOP_ROLE": role,
        "LOOP_DEMAND": "0007-x",
        "LOOP_INTERACTIVE": "1" if interactive else "",
    }


def test_builder_may_write_source(repo: Path) -> None:
    code, _ = run(claude("Write", repo, file_path=str(repo / "src/app/new.py"), content="x"), env(repo))
    assert code == 0


def test_builder_may_not_write_tests(repo: Path) -> None:
    code, msg = run(claude("Edit", repo, file_path="tests/acceptance/test_x.py"), env(repo))
    assert code == 2
    assert "may not write tests/acceptance/test_x.py" in msg


def test_test_writer_may_not_write_source(repo: Path) -> None:
    code, _ = run(claude("Write", repo, file_path="src/app/core.py"), env(repo, "outside-test-writer"))
    assert code == 2


def test_nobody_edits_conventions(repo: Path) -> None:
    for role in ("planner", "builder", "outside-test-writer", "reviewer"):
        code, _ = run(claude("Edit", repo, file_path="loop/conventions.yaml"), env(repo, role))
        assert code == 2, role


def test_planner_writes_only_its_own_demand(repo: Path) -> None:
    ok, _ = run(claude("Write", repo, file_path="loop/demands/0007-x/plan.yaml"), env(repo, "planner"))
    other, _ = run(claude("Write", repo, file_path="loop/demands/0008-y/plan.yaml"), env(repo, "planner"))
    assert (ok, other) == (0, 2)


def test_write_outside_worktree_is_blocked(repo: Path) -> None:
    code, msg = run(claude("Write", repo, file_path="/etc/passwd"), env(repo))
    assert code == 2 and "outside" in msg


def test_copilot_native_payload(repo: Path) -> None:
    code, _ = run(copilot("create", repo, path="tests/acceptance/t.py"), env(repo))
    assert code == 2
    code, _ = run(copilot("bash", repo, command="git status"), env(repo))
    assert code == 0


def test_apply_patch_paths_are_checked(repo: Path) -> None:
    patch = "*** Begin Patch\n*** Update File: tests/acceptance/x.py\n@@\n-a\n+b\n*** End Patch"
    code, _ = run(copilot("apply_patch", repo, input=patch), env(repo))
    assert code == 2


def test_denied_command(repo: Path) -> None:
    code, msg = run(claude("Bash", repo, command="git push --force origin main"), env(repo))
    assert code == 2 and "denied" in msg


def test_chained_command_checks_every_part(repo: Path) -> None:
    code, _ = run(claude("Bash", repo, command="git status && git reset --hard"), env(repo))
    assert code == 2


def test_unlisted_command_blocked_when_unattended_but_not_interactive(repo: Path) -> None:
    payload = claude("Bash", repo, command="curl https://example.com")
    assert run(payload, env(repo))[0] == 2
    assert run(payload, env(repo, "planner", interactive=True))[0] == 0


def test_shell_redirect_into_tests_is_blocked(repo: Path) -> None:
    code, _ = run(claude("Bash", repo, command="echo x > tests/acceptance/t.py"), env(repo))
    assert code == 2


def test_env_prefix_is_ignored_for_matching(repo: Path) -> None:
    code, _ = run(claude("Bash", repo, command="PYTHONPATH=src python -m pytest -q"), env(repo))
    assert code == 0


def test_without_role_only_deny_list_applies(repo: Path) -> None:
    plain = {"LOOP_ROOT": str(repo)}
    assert run(claude("Write", repo, file_path="loop/conventions.yaml"), plain)[0] == 0
    assert run(claude("Bash", repo, command="git clean -fd"), plain)[0] == 2


def test_every_call_is_logged(repo: Path) -> None:
    run(claude("Bash", repo, command="git status"), env(repo))
    lines = (repo / ".loop" / "commands.log").read_text().splitlines()
    assert json.loads(lines[-1])["command"] == "git status"
