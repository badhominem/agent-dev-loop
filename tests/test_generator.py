import subprocess
from pathlib import Path

import pytest

from devloop.config import Conventions, TableRule, detect_includes
from devloop.facts import FactExtractor
from devloop.index import load_opinions, stub_from_facts
from devloop.review import build_report, write_outputs

BUNDLE = Path(__file__).parent / "fixtures" / "shop.bundle"
DEMAND = "0012-dedupe-orders"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    tree = tmp_path / "shop"
    subprocess.run(["git", "clone", "-q", "-b", "demand/0012-dedupe-orders", str(BUNDLE), str(tree)], check=True)
    subprocess.run(["git", "branch", "-q", "main", "origin/main"], cwd=tree, check=True)
    return tree


def test_review_finds_every_planted_problem(shop: Path) -> None:
    report = build_report(shop, DEMAND, "553c155", "review")
    texts = {(f.outcome, f.text) for f in report.findings}
    assert ("back", "Scenario orders-dedupe-03 fails. Back to the builder.") in texts
    assert ("back", "Scenario report-daily-02 has no test. Back to the outside-test writer.") in texts
    assert any(o == "stop" and "src.shop.orders.dedupe now depends on src.shop.reporting.daily" in t for o, t in texts)
    assert any(o == "stop" and "src.shop.orders.ingest writes stg.orders" in t for o, t in texts)
    assert any(o == "carry_on" and "_key" in t for o, t in texts)
    assert report.verdict() == "stop"
    assert set(report.diagrams) == {"system", "change", "data", "behaviour", "plan_vs_actual", "contract_break"}


def test_outputs_are_written(shop: Path) -> None:
    report = build_report(shop, DEMAND, "553c155", "review")
    out = shop / ".loop" / DEMAND
    write_outputs(report, out)
    assert (out / "pr.md").read_text().startswith("# 0012 Dedupe orders")
    pr = (out / "pr.md").read_text()
    assert pr.count("```mermaid") == 4 and "flowchart LR" in pr  # plan vs actual, contract break, behaviour, data
    assert "section `#plan_vs_actual` of `report.html`" in pr
    assert "**Breaks the contract:**" in pr and "Not flagged:" in pr
    assert "zip" not in pr
    assert "Findings" in (out / "report.html").read_text()


def test_stage_one_views_need_no_tests(shop: Path) -> None:
    report = build_report(shop, DEMAND, "553c155", "plan")
    assert "plan_vs_actual" not in report.diagrams
    assert report.findings == []


def test_reviewer_captions_and_findings_are_merged(shop: Path) -> None:
    folder = shop / ".loop" / DEMAND
    folder.mkdir(parents=True)
    (folder / "review.yaml").write_text(
        "captions:\n  change: Dedupe joins the orders pipeline.\n"
        "findings:\n  - {outcome: back, text: KeepFirstDeduplicator contradicts scenario 03}\n"
    )
    report = build_report(shop, DEMAND, "553c155", "review")
    assert report.captions["change"] == "Dedupe joins the orders pipeline."
    assert any(f.text.startswith("Review: KeepFirst") for f in report.findings)


def test_package_grain_groups_modules(shop: Path) -> None:
    conventions = Conventions(
        include=("src/",), component_level="package", table_rules=(TableRule("write_rows", 0, "writes"),)
    )
    facts = FactExtractor(shop, conventions).extract()
    assert set(facts) == {"src.shop.shared", "src.shop.orders", "src.shop.reporting"}
    assert "src.shop.reporting" in facts["src.shop.orders"].imports
    assert facts["src.shop.orders"].writes == {"stg.orders", "clean.orders"}


def test_relative_imports_sql_and_flat_layout(tmp_path: Path) -> None:
    pkg = tmp_path / "app"
    (pkg / "db").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "db" / "__init__.py").write_text("")
    (pkg / "db" / "queries.py").write_text(
        'TOTALS = "INSERT INTO mart.totals SELECT * FROM raw.orders o JOIN raw.customers c ON 1=1"\n'
    )
    (pkg / "jobs.py").write_text("from .db import queries\nfrom . import db\n")
    assert detect_includes(tmp_path) == ("app/",)
    conventions = Conventions(include=("app/",), component_level="module", sql_tables=True)
    facts = FactExtractor(tmp_path, conventions).extract()
    assert "app.db.queries" in facts["app.jobs"].imports
    assert facts["app.db.queries"].writes == {"mart.totals"}
    assert facts["app.db.queries"].reads == {"raw.orders", "raw.customers"}


def test_broken_file_is_skipped_with_a_warning(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "bad.py").write_text("def broken(:\n")
    (tmp_path / "src" / "app" / "good.py").write_text("x = 1\n")
    extractor = FactExtractor(tmp_path, Conventions(include=("src/",), component_level="module"))
    facts = extractor.extract()
    assert "src.app.good" in facts and "src.app.bad" not in facts
    assert extractor.warnings


def test_onboarding_stub_freezes_todays_dependencies(shop: Path) -> None:
    conventions = Conventions(include=("src/",), component_level="module")
    facts = FactExtractor(shop, conventions).extract()
    stub = stub_from_facts(facts["src.shop.orders.dedupe"])
    assert stub.uncertain
    assert "src.shop.reporting.daily" in stub.may_depend_on
    assert stub.key_items == ("Deduplicator",)
    assert "src.shop.orders.ingest" in load_opinions(shop)


def test_auto_grain_flat_package_and_subpackages(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "tool"
    (pkg / "plugins" / "deep").mkdir(parents=True)
    for f in ("__init__.py", "parser.py", "runner.py", "plugins/__init__.py", "plugins/a.py", "plugins/deep/b.py"):
        (pkg / f).write_text("from tool import parser\n" if f == "plugins/deep/b.py" else "x = 1\n")
    facts = FactExtractor(tmp_path, Conventions(include=("src/",))).extract()
    assert set(facts) == {"src.tool", "src.tool.parser", "src.tool.runner", "src.tool.plugins"}
    assert facts["src.tool.plugins"].is_package and facts["src.tool.plugins"].default_boundary() == "plugins"
    assert facts["src.tool.parser"].default_boundary() == "tool"
    assert "src.tool.parser" in facts["src.tool.plugins"].imports
