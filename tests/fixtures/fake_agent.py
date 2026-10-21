"""A stand-in for Claude Code or Copilot in the end-to-end test.

Called as: python fake_agent.py <role> <mode>. It does what each real agent is
asked to do, deterministically, so the whole loop can run in CI.
"""

import sys
from pathlib import Path

import yaml

role = sys.argv[1]
session = yaml.safe_load(Path(".loop/session.yaml").read_text())
demand = session["demand"]
folder = Path("loop/demands") / demand

if role == "planner":
    Path("loop/behaviour/greeting.feature").write_text(
        "Feature: Greeting\n\n"
        f"  @id:greeting-01 @demand:{demand[:4]}\n"
        "  Scenario: People are greeted by name\n"
        "    Given a person called Ada\n"
        "    When they are greeted\n"
        "    Then the greeting is Hello, Ada!\n"
    )
    (folder / "plan.yaml").write_text(
        "add:\n  - id: app.greet\n    responsibility: Greets people by name\n    boundary: app\n"
        "    may_depend_on: []\n    key_items: [greet]\n"
    )
    (folder / "grilling.md").write_text("- Greeting format? 'Hello, <name>!'\n")
elif role == "outside-test-writer":
    tests = Path("tests/acceptance")
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_greeting.py").write_text(
        "from pytest_bdd import given, parsers, scenarios, then, when\n\n"
        "from app.greet import greet\n\n"
        'scenarios("greeting.feature")\n\n\n'
        '@given("a person called Ada", target_fixture="name")\n'
        "def name() -> str:\n    return 'Ada'\n\n\n"
        '@when("they are greeted", target_fixture="greeting")\n'
        "def greeting(name: str) -> str:\n    return greet(name)\n\n\n"
        '@then(parsers.parse("the greeting is {text}"))\n'
        "def check(greeting: str, text: str) -> None:\n    assert greeting == text\n"
    )
elif role == "builder":
    Path("src/app/greet.py").write_text("def greet(name: str) -> str:\n    return f'Hello, {name}!'\n")
    data = {"id": "app.greet", "responsibility": "Greets people by name", "boundary": "app", "key_items": ["greet"]}
    Path("loop/components/app.greet.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    (folder / "build.yaml").write_text("status: done\ndecisions:\n  - Used an f-string\nassumptions: []\n")
elif role == "reviewer":
    out = Path(".loop") / demand
    out.mkdir(parents=True, exist_ok=True)
    (out / "review.yaml").write_text("captions:\n  plan_vs_actual: The greeting was built as planned.\nfindings: []\n")
