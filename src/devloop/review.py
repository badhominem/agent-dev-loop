"""Measuring a demand and producing its views, findings, PR description and report.

Everything here is code, not AI (P7, P8). The reviewer agent may add captions
and its own findings in .loop/<demand>/review.yaml; those are merged in.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from devloop.config import Conventions, as_dict, as_list, as_strs, git, read_yaml
from devloop.facts import ComponentFacts, FactExtractor
from devloop.index import Opinion, Scenario, apply_plan, load_opinions, load_scenarios
from devloop.views import (
    Edge,
    behaviour_view,
    change_view,
    contract_break_view,
    group_for,
    plan_vs_actual_view,
    short,
    system_view,
)

TEMPLATES = Path(__file__).parent / "templates"
OUTCOMES = (("back", "Back to stage 2"), ("stop", "Stop for you"), ("carry_on", "Carry on, noted"))
VIEW_NAMES = {
    "system": "System map",
    "change": "Change",
    "data": "Data",
    "behaviour": "Behaviour",
    "plan_vs_actual": "Plan vs actual",
    "contract_break": "Contract break",
}
VIEW_WHEN = {
    "system": "before the demand",
    "change": "stage 1 · propose and approve",
    "data": "stage 1 · tables touched",
    "behaviour": "scenarios and test results",
    "plan_vs_actual": "stage 3 · PR",
    "contract_break": "stage 3 · stop for you",
}


@dataclass(frozen=True)
class Finding:
    outcome: str  # back, stop, carry_on
    text: str


class Snapshot:
    """A temporary read-only checkout of one commit."""

    def __init__(self, repo: Path, commit: str) -> None:
        self.repo = repo
        self.path = Path(tempfile.mkdtemp(prefix="loop-snap-")) / "tree"
        git(repo, "worktree", "add", "-q", "--detach", str(self.path), commit)

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_: object) -> None:
        git(self.repo, "worktree", "remove", "--force", str(self.path), check=False)
        shutil.rmtree(self.path.parent, ignore_errors=True)


@dataclass
class State:
    facts: dict[str, ComponentFacts]
    opinions: dict[str, Opinion]
    scenarios: dict[str, Scenario]
    warnings: list[str] = field(default_factory=list)


def read_state(tree: Path, conventions: Conventions) -> State:
    extractor = FactExtractor(tree, conventions)
    facts = extractor.extract()
    return State(facts, load_opinions(tree), load_scenarios(tree), extractor.warnings)


def run_tests(tree: Path, conventions: Conventions) -> tuple[dict[str, str], str]:
    report = Path(tempfile.mkdtemp(prefix="loop-junit-")) / "junit.xml"
    command = [part.replace("{junit}", str(report)) for part in conventions.test_command]
    result = subprocess.run(command, cwd=tree, capture_output=True, text=True, check=False)
    if not report.exists():
        return {}, (result.stdout + result.stderr)[-800:]
    outcomes: dict[str, str] = {}
    for case in ET.parse(report).iter("testcase"):
        failed = case.find("failure") is not None or case.find("error") is not None
        outcomes[case.get("name", "").split("[")[0]] = "failing" if failed else "passing"
    return outcomes, ""


# ---------------------------------------------------------------- checks


@dataclass
class Context:
    base: State
    head: State
    planned: dict[str, Opinion]
    scenario_status: dict[str, str]
    size: int
    conventions: Conventions
    build: dict[str, object]
    test_error: str


class Check(ABC):
    @abstractmethod
    def run(self, ctx: Context) -> list[Finding]: ...


class BuildStatusCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        if str(ctx.build.get("status", "")) == "blocked":
            question = str(ctx.build.get("question", "no question given"))
            return [Finding("stop", f"The builder stopped as blocked: {question}")]
        if not ctx.build:
            return [Finding("back", "build.yaml is missing. The builder must hand over its decisions and status.")]
        return []


class TestsCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        if ctx.test_error:
            return [Finding("back", "The tests didn't run. Output ends with: " + ctx.test_error.strip()[-200:])]
        out = []
        for sid, status in sorted(ctx.scenario_status.items()):
            if status == "failing":
                out.append(Finding("back", f"Scenario {sid} fails. Back to the builder."))
            elif status == "uncovered":
                out.append(Finding("back", f"Scenario {sid} has no test. Back to the outside-test writer."))
        return out


class IndexCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        out = [
            Finding("back", f"{c} has no component file in loop/components/.")
            for c in sorted(set(ctx.head.facts) - set(ctx.head.opinions))
        ]
        out += [
            Finding("back", f"loop/components/{c}.yaml points at no code.")
            for c in sorted(set(ctx.head.opinions) - set(ctx.head.facts))
        ]
        return out


class BoundaryCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        out = []
        for comp, facts in sorted(ctx.head.facts.items()):
            allowed = set(ctx.planned[comp].may_depend_on) if comp in ctx.planned else set()
            for dep, names in sorted(facts.imports.items()):
                if dep not in allowed:
                    out.append(
                        Finding(
                            "stop",
                            f"{short(comp)} now depends on {short(dep)} (imports "
                            f"{', '.join(sorted(names)[:3])}). The plan doesn't allow it.",
                        )
                    )
        return out


class DataFlowCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        out = []
        for comp, facts in sorted(ctx.head.facts.items()):
            plan = ctx.planned.get(comp)
            for verb, actual, wanted in (
                ("writes", facts.writes, plan.writes if plan else ()),
                ("reads", facts.reads, plan.reads if plan else ()),
            ):
                for t in sorted(actual - set(wanted)):
                    out.append(Finding("stop", f"{short(comp)} {verb} {t}, which the plan doesn't include."))
                for t in sorted(set(wanted) - actual):
                    out.append(Finding("stop", f"{short(comp)} was planned to {verb[:-1]} {t}, but doesn't."))
        return out


class InterfaceCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        out = []
        for comp, facts in sorted(ctx.head.facts.items()):
            planned_items = set(ctx.planned[comp].key_items) if comp in ctx.planned else set()
            before = ctx.base.facts.get(comp)
            old = before.public_names() if before else set()
            for item in sorted(planned_items - facts.public_names()):
                out.append(Finding("stop", f"Planned interface {item} in {short(comp)} is missing."))
            for c in facts.classes:
                if c.name in old or c.name in planned_items:
                    continue
                if c.is_abc:
                    out.append(Finding("stop", f"New interface {c.name} in {short(comp)} wasn't planned."))
                else:
                    out.append(Finding("carry_on", f"New class {c.name} inside {short(comp)}."))
            for p in facts.private:
                if not before or p not in before.private:
                    out.append(Finding("carry_on", f"New private helper {p} inside {short(comp)}."))
        return out


class SizeCheck(Check):
    def run(self, ctx: Context) -> list[Finding]:
        limit = ctx.conventions.pr_size_limit
        if ctx.size > limit:
            return [Finding("stop", f"{ctx.size} changed lines, over the repo's limit of {limit}.")]
        return [Finding("carry_on", f"{ctx.size} changed lines, within the limit of {limit}.")]


CHECKS: tuple[Check, ...] = (
    BuildStatusCheck(),
    TestsCheck(),
    IndexCheck(),
    BoundaryCheck(),
    DataFlowCheck(),
    InterfaceCheck(),
    SizeCheck(),
)


# ---------------------------------------------------------------- the report


@dataclass
class Report:
    demand: str
    title: str
    why: str
    base: str
    head: str
    mermaid: dict[str, str] = field(default_factory=dict)
    captions: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    stage: str = "review"
    warnings: list[str] = field(default_factory=list)

    def verdict(self) -> str:
        if any(f.outcome == "stop" for f in self.findings):
            return "stop"
        if any(f.outcome == "back" for f in self.findings):
            return "back"
        return "clean"


def _demand_text(folder: Path) -> tuple[str, str]:
    path = folder / "demand.md"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [folder.name]
    return lines[0].lstrip("# ").strip(), "\n".join(lines[1:]).strip()


def changed_lines(tree: Path, base: str, folders: list[str]) -> int:
    out = git(tree, "diff", "--numstat", base, "HEAD", "--", *folders)
    total = 0
    for line in out.splitlines():
        added, removed, _ = line.split("\t", 2)
        total += int(added) + int(removed) if added.isdigit() and removed.isdigit() else 0
    return total


def build_report(tree: Path, demand: str, base: str, stage: str) -> Report:
    """stage "plan": stage 1 views. stage "review": the full stage 3 measurement."""
    conventions = Conventions.load(tree)
    folder = tree / "loop" / "demands" / demand
    with Snapshot(tree, base) as snap:
        before = read_state(snap, conventions)
    head = read_state(tree, conventions)
    planned = apply_plan(before.opinions, read_yaml(folder / "plan.yaml"))
    title, why = _demand_text(folder)
    report = Report(demand, title, why, base, git(tree, "rev-parse", "--short", "HEAD"), stage=stage)
    max_nodes = conventions.view_max_nodes

    report.mermaid["system"] = system_view(before.facts, before.opinions, max_nodes)
    report.captions["system"] = (
        f"The repo at base {base}: {len(before.facts)} components and the tables "
        "they touch. Grey dotted arrows show which component uses which."
    )
    change, summary = change_view(before.opinions, planned, max_nodes)
    report.mermaid["change"] = change
    report.captions["change"] = (
        "Planned: " + "; ".join(f"{k} {', '.join(v)}" for k, v in summary.items() if v) + "."
        if any(summary.values())
        else "The plan changes nothing yet."
    )
    if (
        summary["tables added"]
        or summary["tables retired"]
        or any(
            set(before.opinions.get(c, Opinion(c)).reads) != set(o.reads)
            or set(before.opinions.get(c, Opinion(c)).writes) != set(o.writes)
            for c, o in planned.items()
        )
    ):
        report.mermaid["data"], _ = change_view(before.opinions, planned, max_nodes, data_only=True)
        report.captions["data"] = "Only the tables this demand touches, and which components read or write them."

    results: dict[str, str] = {}
    test_error = ""
    if stage == "review":
        results, test_error = run_tests(tree, conventions)
    behaviour, status = behaviour_view(before.scenarios, head.scenarios, results, only_changed=True)
    report.mermaid["behaviour"] = behaviour
    counts = {k: sum(v == k for v in status.values()) for k in ("passing", "failing", "uncovered")}
    report.captions["behaviour"] = (
        f"Scenarios this demand adds, changes or retires: {counts['passing']} passing, "
        f"{counts['failing']} failing, {counts['uncovered']} without a test."
        if stage == "review"
        else "Scenarios this demand adds, changes or retires. Tests come in stage 2."
    )
    report.warnings = before.warnings + head.warnings

    if stage != "review":
        return report

    pva, breaks = plan_vs_actual_view(planned, head.opinions, head.facts, max_nodes)
    report.mermaid["plan_vs_actual"] = pva
    report.captions["plan_vs_actual"] = (
        "Black: built as planned. Purple: built but not planned. "
        "Dashed blue: planned but not built. Red: breaks the approved contract."
    )
    if breaks:
        e: Edge = breaks[0]
        report.mermaid["contract_break"] = contract_break_view(e, before.facts, before.opinions, planned)
        allowed = ", ".join(short(x) for x in planned[e.a].may_depend_on) if e.a in planned else "nothing"
        report.captions["contract_break"] = (
            f"{short(e.a)} was approved to depend on {allowed}. The build makes "
            f"it depend on {short(e.b)}, across the {group_for(e.a, planned.get(e.a))}–"
            f"{group_for(e.b, head.opinions.get(e.b) or planned.get(e.b))} boundary."
        )

    build = read_yaml(folder / "build.yaml")
    report.decisions = list(as_strs(build.get("decisions")))
    report.assumptions = list(as_strs(build.get("assumptions")))
    size = changed_lines(tree, base, [*conventions.source_roots, "tests"])
    ctx = Context(before, head, planned, status, size, conventions, build, test_error)
    report.findings = [f for check in CHECKS for f in check.run(ctx)]
    merge_reviewer(report, tree / ".loop" / demand / "review.yaml")
    return report


def merge_reviewer(report: Report, path: Path) -> None:
    """Add the reviewer agent's captions and findings, if it has written any."""
    data = read_yaml(path)
    for key, text in as_dict(data.get("captions")).items():
        if key in report.mermaid and str(text).strip():
            report.captions[key] = str(text).strip()
    for item in (as_dict(x) for x in as_list(data.get("findings")) if isinstance(x, dict)):
        outcome = str(item.get("outcome", "back"))
        if outcome in ("back", "stop", "carry_on") and item.get("text"):
            report.findings.append(Finding(outcome, "Review: " + str(item["text"])))


# ---------------------------------------------------------------- writing outputs


def write_outputs(report: Report, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for key, text in report.mermaid.items():
        (out / f"{key}.mmd").write_text(text, encoding="utf-8")
    findings = [{"outcome": f.outcome, "text": f.text} for f in report.findings]
    (out / "findings.yaml").write_text(
        yaml.safe_dump({"verdict": report.verdict(), "findings": findings}, sort_keys=False), encoding="utf-8"
    )
    if report.stage == "review":
        (out / "pr.md").write_text(pr_markdown(report), encoding="utf-8")
    (out / "report.html").write_text(report_html(report), encoding="utf-8")


def pr_markdown(r: Report) -> str:
    lines = [f"# {r.title}", "", "## Why", "", r.why or "_No description in demand.md._", ""]
    for key in ("plan_vs_actual", "behaviour", "data"):
        if key in r.mermaid:
            lines += [
                f"## {VIEW_NAMES[key]}",
                "",
                r.captions.get(key, ""),
                "",
                "```mermaid",
                r.mermaid[key].rstrip(),
                "```",
                "",
            ]
    lines += ["## Findings", ""]
    for code, title in OUTCOMES:
        items = [f.text for f in r.findings if f.outcome == code]
        if items:
            lines += [f"**{title}**", "", *(f"- {t}" for t in items), ""]
    if r.decisions:
        lines += ["## Builder's decisions", "", *(f"- {d}" for d in r.decisions), ""]
    if r.assumptions:
        lines += ["## Builder's assumptions", "", *(f"- {a}" for a in r.assumptions), ""]
    lines += [f"_Generated by `loop review` from base {r.base} to {r.head}._", ""]
    return "\n".join(lines)


def report_html(r: Report) -> str:
    order = ["system", "change", "data", "behaviour", "findings", "plan_vs_actual", "contract_break"]
    sections, links = [], []
    for key in order:
        if key == "findings":
            if r.stage != "review":
                continue
            cols = []
            for code, title in OUTCOMES:
                items = "".join(f"<li>{html.escape(f.text)}</li>" for f in r.findings if f.outcome == code)
                cols.append(f'<div class="out {code}"><b>{title}</b><ul>{items or "<li>Nothing</li>"}</ul></div>')
            log = "".join(
                f"<div><b>{name}</b><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in items) or '<li>None</li>'}"
                "</ul></div>"
                for name, items in (("Builder's decisions", r.decisions), ("Builder's assumptions", r.assumptions))
            )
            sections.append(
                f'<section class="view" id="findings"><header><h2>Findings</h2><span class="when">'
                f'verdict: {r.verdict()}</span></header><div class="outs">{"".join(cols)}</div>'
                f'<div class="log">{log}</div></section>'
            )
            links.append('<a href="#findings">Findings</a>')
            continue
        if key not in r.mermaid:
            continue
        links.append(f'<a href="#{key}">{VIEW_NAMES[key]}</a>')
        sections.append(
            f'<section class="view" id="{key}"><header><h2>{VIEW_NAMES[key]}</h2>'
            f'<span class="when">{VIEW_WHEN[key]}</span></header>'
            f'<p class="cap">{html.escape(r.captions.get(key, ""))}</p>'
            f'<div class="diagram"><pre class="mermaid">{html.escape(r.mermaid[key])}</pre></div></section>'
        )
    page = (TEMPLATES / "report.html").read_text(encoding="utf-8")
    return (
        page.replace("{{title}}", html.escape(r.title))
        .replace("{{why}}", html.escape(r.why))
        .replace("{{meta}}", f"{html.escape(r.demand)} · base {r.base} · head {r.head} · {r.stage}")
        .replace("{{nav}}", "".join(links))
        .replace("{{sections}}", "\n".join(sections))
    )


def onboard_report(repo: Path) -> Report:
    conventions = Conventions.load(repo)
    state = read_state(repo, conventions)
    report = Report(
        "onboard",
        f"{repo.name} system map",
        "First map of the repo. Dotted yellow boxes are drafts still to confirm.",
        "-",
        git(repo, "rev-parse", "--short", "HEAD"),
        stage="onboard",
    )
    report.mermaid["system"] = system_view(state.facts, state.opinions, conventions.view_max_nodes)
    report.captions["system"] = (
        f"{len(state.facts)} components. Dotted yellow: responsibility and boundaries are drafts to confirm."
    )
    return report
