"""Scanning what `include` lists: Python without __init__.py, data and config files, and path-based ids."""

from pathlib import Path

from devloop.config import Conventions, detect_includes, normalize_include
from devloop.facts import ComponentFacts, FactExtractor
from devloop.views import fact_edges, system_view
from tests.test_guard import claude, env, run


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def extract(root: Path, include: tuple[str, ...], **options: object) -> tuple[dict[str, ComponentFacts], list[str]]:
    extractor = FactExtractor(root, Conventions(include=include, **options))  # type: ignore[arg-type]
    return extractor.extract(), extractor.warnings


# ---- python without __init__.py


def test_python_needs_no_init_file(tmp_path: Path) -> None:
    write(tmp_path, {"src/app/orders.py": "def run(): ...\n", "src/app/billing.py": "from app import orders\n"})
    facts, warnings = extract(tmp_path, ("src/",), component_level="module")
    assert set(facts) == {"src.app.orders", "src.app.billing"}
    assert "src.app.orders" in facts["src.app.billing"].imports
    assert warnings == []


def test_a_top_level_folder_without_init_is_the_package(tmp_path: Path) -> None:
    write(tmp_path, {"shop/orders.py": "x = 1\n", "shop/billing.py": "from shop import orders\n"})
    facts, _ = extract(tmp_path, ("shop/",), component_level="module")
    assert set(facts) == {"shop.orders", "shop.billing"}
    assert "shop.orders" in facts["shop.billing"].imports
    assert facts["shop.orders"].default_boundary() == "shop"


def test_detection_takes_any_top_level_folder_and_skips_tests_and_docs(tmp_path: Path) -> None:
    write(tmp_path, {"app/a.py": "", "scripts/b.py": "", "tests/t.py": "", "docs/x.md": "", "node_modules/m.js": ""})
    (tmp_path / ".hidden").mkdir()
    assert detect_includes(tmp_path) == ("app/", "scripts/")
    (tmp_path / "src").mkdir()
    assert detect_includes(tmp_path) == ("src/",)


def test_include_entries_are_normalised_by_what_is_on_disk(tmp_path: Path) -> None:
    write(tmp_path, {"app/a.py": "", "main.py": ""})
    assert normalize_include(tmp_path, ("app", "main.py/", "./later.py", "new/")) == (
        "app/",
        "main.py",
        "later.py",
        "new/",
    )


# ---- files that aren't python


def test_data_and_config_files_are_listed_on_the_component_that_owns_the_folder(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/app/__init__.py": "VERSION = 1\n",
            "src/app/core.py": "x = 1\n",
            "src/app/defaults.yaml": "a: 1\n",
            "src/app/deep/tables.json": "{}\n",
            "src/app/deep/run.sh": "echo hi\n",
            "src/app/notes.md": "# not listed\n",
        },
    )
    facts, _ = extract(tmp_path, ("src/",))
    app = facts["src.app"]
    assert {(f.name, f.kind, f.path) for f in app.files} == {
        ("defaults.yaml", "config", "src/app/defaults.yaml"),
        ("tables.json", "data", "src/app/deep/tables.json"),
        ("run.sh", "program", "src/app/deep/run.sh"),
    }


def test_a_folder_without_python_becomes_a_component_of_its_own(tmp_path: Path) -> None:
    write(tmp_path, {"src/app/core.py": "x = 1\n", "config/orders.yaml": "a: 1\n", "config/shops.toml": "b = 1\n"})
    facts, _ = extract(tmp_path, ("src/", "config/"))
    config = facts["config"]
    assert config.parts == ("config",) and config.is_package
    assert [f.name for f in config.files] == ["orders.yaml", "shops.toml"]
    assert config.functions == [] and config.classes == [] and config.modules == []
    assert config.default_boundary() == "config"


def test_top_level_files_can_be_listed_and_belong_to_root(tmp_path: Path) -> None:
    write(tmp_path, {"main.py": "def go(): ...\n", "pyproject.toml": "[x]\n", "Makefile": "all:\n", "other.txt": "t\n"})
    facts, _ = extract(tmp_path, ("main.py", "pyproject.toml", "Makefile", "other.txt"))
    assert set(facts) == {"main", "root"}
    assert {(f.name, f.kind) for f in facts["root"].files} == {
        ("pyproject.toml", "config"),
        ("Makefile", "program"),
        ("other.txt", "other"),
    }


def test_exclude_and_ignored_folders_are_left_out(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "data/a.json": "{}",
            "data/skip.lock.json": "{}",
            "data/old/b.json": "{}",
            "data/node_modules/c.json": "{}",
            "data/__pycache__/d.json": "{}",
            "data/.secret.json": "{}",
        },
    )
    facts, _ = extract(tmp_path, ("data/",), exclude=("*.lock.json", "old"))
    assert [f.path for f in facts["data"].files] == ["data/a.json"]


def test_a_missing_include_gives_a_warning(tmp_path: Path) -> None:
    facts, warnings = extract(tmp_path, ("nowhere/",))
    assert facts == {} and any("nowhere/" in w for w in warnings)


# ---- ids


def test_names_are_kept_and_the_path_is_stored_in_parts(tmp_path: Path) -> None:
    write(tmp_path, {"my-app/v1.2/jobs.py": "x = 1\n", "my-app/v1.2/jobs.yaml": "a: 1\n"})
    facts, _ = extract(tmp_path, ("my-app/",), component_level="module")
    comp = facts["my-app.v1.2.jobs"]
    assert comp.parts == ("my-app", "v1.2", "jobs")
    assert comp.default_boundary() == "v1.2"
    assert facts["my-app.v1.2"].parts == ("my-app", "v1.2")
    assert facts["my-app.v1.2"].files[0].name == "jobs.yaml"


def test_two_paths_with_the_same_id_give_a_warning(tmp_path: Path) -> None:
    write(tmp_path, {"a.b/c.py": "x = 1\n", "a/b.c.py": "y = 1\n"})
    facts, warnings = extract(tmp_path, ("a.b/", "a/"), component_level="module")
    assert len(facts) == 1 and any("already" in w for w in warnings)


def test_an_ambiguous_import_draws_no_edge_and_warns(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"one/util.py": "x = 1\n", "two/util.py": "y = 1\n", "app/main.py": "import util\n"},
    )
    facts, warnings = extract(tmp_path, ("one/", "two/", "app/"), component_level="module")
    assert facts["app.main"].imports == {}
    assert any("util" in w and "no edge" in w for w in warnings)


def test_stdlib_names_do_not_match_nested_modules(tmp_path: Path) -> None:
    write(tmp_path, {"src/app/logging.py": "x = 1\n", "src/app/main.py": "import logging\n"})
    facts, _ = extract(tmp_path, ("src/",), component_level="module")
    assert facts["src.app.main"].imports == {}


def test_relative_imports_use_the_location(tmp_path: Path) -> None:
    write(tmp_path, {"app/db/queries.py": "x = 1\n", "app/jobs.py": "from .db import queries\n"})
    facts, _ = extract(tmp_path, ("app/",), component_level="module")
    assert "app.db.queries" in facts["app.jobs"].imports


# ---- uses


def test_a_module_that_names_a_config_file_uses_it(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "src/app/jobs.py": 'CONFIG = "orders_schema.yaml"\nOTHER = "config/rates.json"\n',
            "src/app/orders_schema.yaml": "a: 1\n",
            "config/rates.json": "{}\n",
            "config/unused.yaml": "z: 1\n",
        },
    )
    facts, warnings = extract(tmp_path, ("src/", "config/"), component_level="module")
    uses = facts["src.app.jobs"].uses
    assert uses == {"src.app": {"src/app/orders_schema.yaml"}, "config": {"config/rates.json"}}
    assert warnings == []
    edges = fact_edges(facts, uses=True, files=True)
    assert {(e.a, e.b) for e in edges} == {("src.app.jobs", "src.app"), ("src.app.jobs", "config")}
    assert "src.app.jobs" in system_view(facts, {}, 30)


def test_an_ambiguous_file_name_warns_and_records_nothing(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "app/job.py": 'F = "settings.yaml"\nG = "a/settings.yaml"\n',
            "a/settings.yaml": "x: 1\n",
            "b/settings.yaml": "x: 2\n",
        },
    )
    facts, warnings = extract(tmp_path, ("app/", "a/", "b/"), component_level="module")
    assert facts["app.job"].uses == {"a": {"a/settings.yaml"}}
    assert any("settings.yaml" in w and "2 files" in w for w in warnings)


# ---- builder zones and the guard


def test_the_builder_zone_follows_include_for_folders_and_files() -> None:
    conventions = Conventions(include=("src/", "config/", "main.py"))
    zones = conventions.zones_for("builder", "0001-x")
    assert zones[:3] == ("src/", "config/", "main.py")


def test_the_builder_may_write_included_files_and_folders_only(repo: Path) -> None:
    conventions = repo / "loop" / "conventions.yaml"
    conventions.write_text(conventions.read_text() + "\ninclude: [src/, config/, pyproject.toml]\n")
    (repo / "config").mkdir()
    for target in ("config/rates.yaml", "pyproject.toml", "src/app/x.py"):
        code, msg = run(claude("Write", repo, file_path=target, content="x"), env(repo))
        assert code == 0, msg
    for target in ("README.md", "scripts/run.sh", "pyproject.toml.bak"):
        code, _ = run(claude("Write", repo, file_path=target, content="x"), env(repo))
        assert code == 2
