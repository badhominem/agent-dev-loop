from pathlib import Path

import pytest

from devloop.demands import Queue
from tests.conftest import git


def test_new_demand_is_committed_to_main(repo: Path) -> None:
    demand = Queue(repo).new("Dedupe orders")
    assert demand.id == "0001-dedupe-orders"
    assert demand.state == "new"
    assert "new demand 0001" in git(repo, "log", "-1", "--format=%s")


def test_claim_creates_worktree_copies_config_and_runs_setup(repo: Path) -> None:
    (repo / ".env").write_text("TOKEN=dev\n")
    (repo / "loop" / "worktree.toml").write_text('copy = [".env"]\nsetup = ["touch ready.txt"]\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    queue = Queue(repo)
    queue.new("First")
    demand, path, notes = queue.claim("0001")
    assert demand.state == "claimed"
    assert (path / ".env").read_text() == "TOKEN=dev\n"
    assert (path / "ready.txt").exists()
    assert git(path, "branch", "--show-current") == "demand/0001-first"
    assert "copied .env" in notes
    assert demand.status["worktree"] == str(path)


def test_claim_twice_is_refused(repo: Path) -> None:
    queue = Queue(repo)
    queue.new("First")
    queue.claim("0001")
    with pytest.raises(SystemExit):
        queue.claim("0001")


def test_approve_records_base_and_commits_plan(repo: Path) -> None:
    queue = Queue(repo)
    queue.new("First")
    _, path, _ = queue.claim("0001")
    (path / "loop" / "demands" / "0001-first" / "plan.yaml").write_text("add: []\n")
    approved = queue.approve("0001")
    assert approved.state == "approved"
    # The base is main as the plan saw it: just before the approval commit itself.
    assert approved.status["base"] == git(repo, "rev-parse", "--short", "main~1")
    assert git(path, "merge-base", "HEAD", "main~1").startswith(str(approved.status["base"]))
    assert "plan" in git(path, "log", "-1", "--format=%s")


def test_reset_removes_worktree_and_branch(repo: Path) -> None:
    queue = Queue(repo)
    queue.new("First")
    _, path, _ = queue.claim("0001")
    demand = queue.reset("0001", "nobody picked it up")
    assert demand.state == "new"
    assert not path.exists()
    assert git(repo, "branch", "--list", "demand/0001-first") == ""


def test_invalid_transition_is_refused(repo: Path) -> None:
    queue = Queue(repo)
    queue.new("First")
    with pytest.raises(SystemExit):
        queue.set_state("0001", "closed")


def test_status_commits_need_the_truth_branch(repo: Path) -> None:
    queue = Queue(repo)
    git(repo, "switch", "-q", "-c", "other")
    with pytest.raises(SystemExit):
        queue.new("Nope")


def test_queue_works_from_inside_a_worktree(repo: Path) -> None:
    queue = Queue(repo)
    queue.new("First")
    _, path, _ = queue.claim("0001")
    assert Queue(path).main == repo
