"""The `loop` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devloop import guard
from devloop.config import Conventions, git
from devloop.demands import Demand, Queue
from devloop.facts import FactExtractor
from devloop.index import load_opinions, opinions_dir, stub_from_facts
from devloop.installer import install
from devloop.review import Report, build_report, onboard_report, write_outputs, zip_name
from devloop.runner import Runner, Session

LIFECYCLE = "new -> claimed <-> returned -> approved -> closed, or rejected"


def _queue() -> Queue:
    return Queue(Path.cwd())


def _base(queue: Queue, demand: Demand) -> str:
    base = demand.status.get("base")
    return str(base) if base else git(queue.main, "rev-parse", "--short", queue.conventions.truth_branch)


def _worktree(queue: Queue, demand: Demand) -> Path:
    path = queue.worktree_path(demand)
    if not path.exists():
        raise SystemExit(f"{demand.id} has no worktree. Run `loop claim {demand.id[:4]}` first.")
    return path


def _print_report(report: Report, out: Path) -> None:
    for warning in report.warnings:
        print(f"[warn] {warning}")
    for f in report.findings:
        print(f"[{f.outcome}] {f.text}")
    print(f"[loop] views and report in {out}")
    if report.stage == "review":
        print(f"[loop] verdict: {report.verdict()}")
        print(f"[loop] To share the drawn report, drag {out / zip_name(report)} into the PR description.")


# ---------------------------------------------------------------- commands


def cmd_install(args: argparse.Namespace) -> None:
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    harnesses = [h.strip() for h in args.harness.split(",") if h.strip()]
    for line in install(repo, harnesses):
        print(f"[loop] wrote {line}")
    print("[loop] Next: review loop/conventions.yaml and loop/tools.toml, then run `loop onboard`.")


def cmd_onboard(_: argparse.Namespace) -> None:
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    conventions = Conventions.load(repo)
    facts = FactExtractor(repo, conventions).extract()
    existing = load_opinions(repo)
    folder = opinions_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    created = 0
    for cid, fact in sorted(facts.items()):
        if cid not in existing:
            (folder / f"{cid}.yaml").write_text(stub_from_facts(fact).to_yaml(), encoding="utf-8")
            created += 1
    report = onboard_report(repo)
    out = repo / ".loop" / "onboard"
    write_outputs(report, out)
    listed = sum(len(f.files) for f in facts.values())
    print(
        f"[loop] {len(facts)} components found with component_level: {conventions.component_level}, "
        f"scanning {', '.join(conventions.include) or 'nothing'}; {listed} data, config or program files listed; "
        f"{created} draft component files written to loop/components/."
    )
    print(
        f"[loop] Open {out / 'report.html'} and correct the drafts: write each responsibility, "
        "tighten may_depend_on, then remove `uncertain: true`."
    )
    print("[loop] When the map looks right, commit it to the truth branch as 'loop: onboard'.")


def cmd_new(args: argparse.Namespace) -> None:
    demand = _queue().new(args.title, args.text or "")
    print(f"[loop] created {demand.folder}. Describe it in demand.md, then `loop plan {demand.id[:4]}`.")


def cmd_status(_: argparse.Namespace) -> None:
    demands = _queue().all()
    if not demands:
        print('[loop] The queue is empty. Add a demand with `loop new "<title>"`.')
    for d in demands:
        extra = f" · base {d.status['base']}" if d.status.get("base") else ""
        who = f" · {d.status['claimed_by']}" if d.status.get("claimed_by") and d.state == "claimed" else ""
        print(f"{d.id:40} {d.state:9}{who}{extra}")


def cmd_claim(args: argparse.Namespace) -> None:
    demand, path, notes = _queue().claim(args.demand)
    print(f"[loop] {demand.id} claimed. Worktree: {path}")
    for note in notes:
        print(f"[loop]   {note}")


def cmd_plan(args: argparse.Namespace) -> None:
    queue = _queue()
    demand = queue.get(args.demand)
    if demand.state in ("new", "returned"):
        demand, _, _ = queue.claim(args.demand)
    path = _worktree(queue, demand)
    runner = Runner(queue.conventions, args.harness, args.dry_run)
    runner.run(Session("planner", demand.id, path, _base(queue, demand)), interactive=True)
    if not args.dry_run:
        _views(queue, demand)


def _views(queue: Queue, demand: Demand) -> None:
    path = _worktree(queue, demand)
    report = build_report(path, demand.id, _base(queue, demand), "plan")
    out = path / ".loop" / demand.id
    write_outputs(report, out)
    _print_report(report, out)


def cmd_views(args: argparse.Namespace) -> None:
    queue = _queue()
    _views(queue, queue.get(args.demand))


def cmd_approve(args: argparse.Namespace) -> None:
    demand = _queue().approve(args.demand)
    print(f"[loop] {demand.id} approved on base {demand.status['base']}. Next: `loop run {demand.id[:4]}`.")


def _require_approved(demand: Demand) -> None:
    if demand.state != "approved":
        raise SystemExit(f"{demand.id} is '{demand.state}'. Approve it first with `loop approve`.")


def _build(queue: Queue, demand: Demand, runner: Runner, round_no: int, roles: list[str]) -> None:
    path = _worktree(queue, demand)
    for role in roles:
        runner.run(Session(role, demand.id, path, _base(queue, demand), round_no), interactive=False)


def _review(queue: Queue, demand: Demand, runner: Runner, round_no: int) -> Report:
    path = _worktree(queue, demand)
    base = _base(queue, demand)
    out = path / ".loop" / demand.id
    (out / "review.yaml").unlink(missing_ok=True)
    report = build_report(path, demand.id, base, "review")
    write_outputs(report, out)
    runner.run(Session("reviewer", demand.id, path, base, round_no), interactive=False)
    report = build_report(path, demand.id, base, "review")
    write_outputs(report, out)
    _print_report(report, out)
    return report


def cmd_build(args: argparse.Namespace) -> None:
    queue = _queue()
    demand = queue.get(args.demand)
    _require_approved(demand)
    roles = ["outside-test-writer", "builder"] + (["inside-test-writer"] if queue.conventions.inside_tests else [])
    _build(queue, demand, Runner(queue.conventions, args.harness, args.dry_run), 1, roles)


def cmd_review(args: argparse.Namespace) -> None:
    queue = _queue()
    demand = queue.get(args.demand)
    _review(queue, demand, Runner(queue.conventions, args.harness, args.dry_run), 1)


def cmd_run(args: argparse.Namespace) -> None:
    """Stage 2 and 3 together, sending work back automatically up to max_back_rounds."""
    queue = _queue()
    demand = queue.get(args.demand)
    _require_approved(demand)
    runner = Runner(queue.conventions, args.harness, args.dry_run)
    inside = ["inside-test-writer"] if queue.conventions.inside_tests else []
    roles = ["outside-test-writer", "builder", *inside]
    for round_no in range(1, queue.conventions.max_back_rounds + 2):
        _build(queue, demand, runner, round_no, roles)
        report = _review(queue, demand, runner, round_no)
        if args.dry_run or report.verdict() != "back":
            break
        back = [f.text for f in report.findings if f.outcome == "back"]
        if round_no > queue.conventions.max_back_rounds:
            print("[loop] Still failing after the allowed rounds. Over to you.")
            break
        roles = ["outside-test-writer"] if any("outside-test writer" in t for t in back) else []
        roles += ["builder", *inside] if any("outside-test writer" not in t for t in back) else []
        print(f"[loop] sending back to: {', '.join(roles)}")
    print(f"[loop] Open {queue.worktree_path(demand) / '.loop' / demand.id / 'report.html'}")


def cmd_state(args: argparse.Namespace) -> None:
    demand = _queue().set_state(args.demand, args.state, args.reason)
    print(f"[loop] {demand.id} is now {demand.state}.")


def cmd_reset(args: argparse.Namespace) -> None:
    demand = _queue().reset(args.demand, args.reason)
    print(f"[loop] {demand.id} is back to new; its worktree and branch are removed.")


def cmd_close(args: argparse.Namespace) -> None:
    demand = _queue().close(args.demand, args.merged)
    print(f"[loop] {demand.id} closed by {args.merged}.")


def cmd_guard(_: argparse.Namespace) -> None:
    guard.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop", description="The Agent Dev Loop. Lifecycle: " + LIFECYCLE)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: object, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    p = add("install", cmd_install, "add the loop to this repo and generate harness files")
    p.add_argument("--harness", default="claude,copilot", help="comma-separated: claude, copilot")
    add("onboard", cmd_onboard, "map an existing repo: draft component files and a system map")
    p = add("new", cmd_new, "add a demand to the queue")
    p.add_argument("title")
    p.add_argument("--text", help="the demand's description")
    add("status", cmd_status, "show the queue")
    p = add("claim", cmd_claim, "claim a demand: create its worktree and branch")
    p.add_argument("demand")
    for name, func, text in (
        ("plan", cmd_plan, "stage 1: claim if needed and start the planner with you"),
        ("build", cmd_build, "stage 2: outside-test writer, then builder"),
        ("review", cmd_review, "stage 3: measure, review, write the PR and report"),
        ("run", cmd_run, "stages 2 and 3, sending work back automatically"),
    ):
        p = add(name, func, text)
        p.add_argument("demand")
        p.add_argument("--harness", help="override the harness in conventions.yaml")
        p.add_argument("--dry-run", action="store_true", help="print the agent commands without running them")
    p = add("views", cmd_views, "draw the demand's views from the current state")
    p.add_argument("demand")
    p = add("approve", cmd_approve, "approve the plan: update onto the truth branch and record the base")
    p.add_argument("demand")
    p = add("state", cmd_state, "set a demand's state, e.g. returned or rejected")
    p.add_argument("demand")
    p.add_argument("state", choices=["returned", "rejected", "new", "claimed"])
    p.add_argument("--reason")
    p = add("reset", cmd_reset, "send a demand back to new and remove its worktree")
    p.add_argument("demand")
    p.add_argument("--reason")
    p = add("close", cmd_close, "close a merged demand")
    p.add_argument("demand")
    p.add_argument("--merged", required=True, help="the merge commit or PR")
    add("guard", cmd_guard, "the hook command; harnesses call this before every tool call")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
