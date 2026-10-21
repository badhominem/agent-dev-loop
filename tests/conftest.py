import shutil
import subprocess
from pathlib import Path

import pytest

SKELETON = Path(__file__).parents[1] / "src" / "devloop" / "templates" / "skeleton"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo on main with the loop skeleton and a tiny src/ package, committed."""
    root = tmp_path / "proj"
    shutil.copytree(SKELETON, root)
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def hello() -> str:\n    return 'hi'\n")
    (root / ".gitignore").write_text(".loop/\n__pycache__/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root
