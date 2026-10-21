"""The two indexes: component opinion files and the behaviour index (Gherkin)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from devloop.config import as_dict, as_list, as_strs, read_yaml
from devloop.facts import ComponentFacts

OPINION_FIELDS = ("responsibility", "boundary", "may_depend_on", "reads", "writes", "key_items", "flows")
FIELD_WORDS = {
    "responsibility": "responsibility",
    "boundary": "boundary",
    "may_depend_on": "allowed dependencies",
    "reads": "reads",
    "writes": "writes",
    "key_items": "key items",
    "flows": "flows",
}


@dataclass(frozen=True)
class Flow:
    to: str
    what: str


@dataclass(frozen=True)
class Opinion:
    id: str
    responsibility: str = ""
    boundary: str = ""
    may_depend_on: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    key_items: tuple[str, ...] = ()
    flows: tuple[Flow, ...] = ()
    uncertain: bool = False

    def to_yaml(self) -> str:
        data: dict[str, object] = {
            "id": self.id,
            "responsibility": self.responsibility,
            "boundary": self.boundary,
            "may_depend_on": list(self.may_depend_on),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "key_items": list(self.key_items),
            "flows": [{"to": f.to, "what": f.what} for f in self.flows],
        }
        if self.uncertain:
            data["uncertain"] = True
        return str(yaml.safe_dump(data, sort_keys=False))


def _flows(value: object) -> tuple[Flow, ...]:
    items = value if isinstance(value, list) else []
    return tuple(Flow(str(as_dict(f).get("to", "")), str(as_dict(f).get("what", ""))) for f in items)


def opinion_from(data: dict[str, object], base: Opinion | None = None) -> Opinion:
    """Build an opinion from YAML. With a base, only the fields present change."""
    given: dict[str, object] = {}
    for name in ("responsibility", "boundary"):
        if name in data:
            given[name] = str(data[name] or "")
    for name in ("may_depend_on", "reads", "writes", "key_items"):
        if name in data:
            given[name] = as_strs(data[name])
    if "flows" in data:
        given["flows"] = _flows(data["flows"])
    if "uncertain" in data:
        given["uncertain"] = bool(data["uncertain"])
    start = base or Opinion(id=str(data.get("id", "")))
    return replace(start, **given)  # type: ignore[arg-type]


def opinions_dir(repo: Path) -> Path:
    return repo / "loop" / "components"


def load_opinions(repo: Path) -> dict[str, Opinion]:
    folder = opinions_dir(repo)
    if not folder.exists():
        return {}
    return {o.id: o for o in (opinion_from(read_yaml(f)) for f in sorted(folder.glob("*.yaml"))) if o.id}


def apply_plan(opinions: dict[str, Opinion], plan: dict[str, object]) -> dict[str, Opinion]:
    planned = dict(opinions)
    for item in (as_dict(i) for i in as_list(plan.get("add")) if isinstance(i, dict)):
        planned[str(item["id"])] = opinion_from(item)
    for item in (as_dict(i) for i in as_list(plan.get("change")) if isinstance(i, dict)):
        cid = str(item.get("id", ""))
        if cid in planned:
            planned[cid] = opinion_from(item, planned[cid])
    for cid in as_strs(plan.get("remove")):
        planned.pop(cid, None)
    return planned


def changed_fields(a: Opinion, b: Opinion) -> list[str]:
    return [FIELD_WORDS[n] for n in OPINION_FIELDS if getattr(a, n) != getattr(b, n)]


def stub_from_facts(facts: ComponentFacts) -> Opinion:
    """A first draft for onboarding: today's dependencies become the allowed ones."""
    return Opinion(
        id=facts.id,
        responsibility="",
        boundary=facts.default_boundary(),
        may_depend_on=tuple(sorted(facts.imports)),
        reads=tuple(sorted(facts.reads)),
        writes=tuple(sorted(facts.writes)),
        key_items=tuple(facts.interfaces()),
        uncertain=True,
    )


# ---------------------------------------------------------------- behaviour index


@dataclass(frozen=True)
class Scenario:
    id: str
    feature: str
    name: str
    body: str
    file: str


def load_scenarios(repo: Path) -> dict[str, Scenario]:
    scenarios: dict[str, Scenario] = {}
    folder = repo / "loop" / "behaviour"
    if not folder.exists():
        return scenarios
    for path in sorted(folder.rglob("*.feature")):
        feature, tags = path.stem, ""
        entries: list[tuple[str, str, list[str]]] = []
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line.startswith("Feature:"):
                feature = line.removeprefix("Feature:").strip()
            elif line.startswith("@"):
                tags = f"{tags} {line}".strip()
            elif line.startswith(("Scenario:", "Scenario Outline:")):
                entries.append((tags, line.split(":", 1)[1].strip(), []))
                tags = ""
            elif entries and line and not line.startswith("#"):
                entries[-1][2].append(line)
        for tag_line, name, body in entries:
            found = re.search(r"@id:(\S+)", tag_line)
            key = found.group(1) if found else name
            scenarios[key] = Scenario(key, feature, name, "\n".join(body), path.name)
    return scenarios


def test_name(scenario_name: str) -> str:
    """The test function name pytest-bdd generates for a scenario."""
    return "test_" + re.sub(r"[^a-z0-9]+", "_", scenario_name.lower()).strip("_")
