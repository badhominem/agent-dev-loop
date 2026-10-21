"""Facts read straight from source code. Nothing here is ever stored (P3, P7).

A component is a package or a module, set by conventions.component_level:
"auto" (default) makes each module directly in the top package a component and
each sub-package one component; "package" groups modules by their folder;
"module" makes every module a component. Facts
per component: public classes (and which are interfaces), public and private
functions, dependencies on other components, and tables read or written.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from devloop.config import Conventions, TableRule

SQL_HINT = re.compile(r"\b(SELECT|INSERT|UPDATE|MERGE|CREATE|DELETE)\b", re.I)
SQL_READS = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.I)
SQL_WRITES = re.compile(
    r"\b(?:INSERT\s+(?:INTO|OVERWRITE)(?:\s+TABLE)?|UPDATE|MERGE\s+INTO|"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|DELETE\s+FROM)"
    r"\s+([A-Za-z_][\w.]*)",
    re.I,
)


@dataclass(frozen=True)
class ClassFact:
    name: str
    is_abc: bool
    public_methods: tuple[str, ...]


@dataclass
class ComponentFacts:
    id: str
    modules: list[str] = field(default_factory=list)
    classes: list[ClassFact] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    private: list[str] = field(default_factory=list)
    imports: dict[str, set[str]] = field(default_factory=dict)  # component id -> imported names
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    lines: int = 0
    is_package: bool = False

    def default_boundary(self) -> str:
        """Sub-packages are their own boundary; modules belong to their package."""
        parts = self.id.split(".")
        if self.is_package:
            return parts[1] if len(parts) >= 2 else parts[0]
        return parts[-2] if len(parts) >= 2 else parts[0]

    def public_names(self) -> set[str]:
        names = {c.name for c in self.classes} | set(self.functions)
        return names | {f"{c.name}.{m}" for c in self.classes for m in c.public_methods}

    def interfaces(self) -> list[str]:
        return [c.name for c in self.classes if c.is_abc]


@dataclass
class _Module:
    id: str
    path: Path
    is_package_init: bool


class FactExtractor:
    def __init__(self, repo: Path, conventions: Conventions) -> None:
        self.repo = repo
        self.conventions = conventions
        self.warnings: list[str] = []

    # ---- discovery

    def _modules(self) -> dict[str, _Module]:
        found: dict[str, _Module] = {}
        for root_name in self.conventions.source_roots:
            root = (self.repo / root_name).resolve()
            if not root.is_dir():
                continue
            base = root.parent if (root / "__init__.py").exists() else root
            for path in sorted(root.rglob("*.py")):
                if any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts):
                    continue
                rel = path.relative_to(base).with_suffix("")
                parts = list(rel.parts)
                is_init = parts[-1] == "__init__"
                if is_init:
                    parts = parts[:-1]
                if parts:
                    found[".".join(parts)] = _Module(".".join(parts), path, is_init)
        return found

    def component_of(self, module: _Module) -> str:
        parts = module.id.split(".")
        level = self.conventions.component_level
        if level == "module":
            return module.id
        if level == "package":
            if module.is_package_init:
                return module.id
            return ".".join(parts[:-1]) if len(parts) > 1 else module.id
        return ".".join(parts[:2]) if len(parts) >= 3 else module.id  # auto

    # ---- extraction

    def extract(self) -> dict[str, ComponentFacts]:
        modules = self._modules()
        components: dict[str, ComponentFacts] = {}
        owner = {m.id: self.component_of(m) for m in modules.values()}
        for module in modules.values():
            try:
                tree = ast.parse(module.path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as error:
                self.warnings.append(f"Skipped {module.path}: {error}")
                continue
            if module.is_package_init and not tree.body:
                continue  # an empty __init__.py is not a component of its own
            comp = components.setdefault(owner[module.id], ComponentFacts(owner[module.id]))
            comp.modules.append(module.id)
            comp.is_package = comp.is_package or module.is_package_init or owner[module.id] != module.id
            comp.lines += len(module.path.read_text(encoding="utf-8").splitlines())
            self._read_definitions(comp, tree)
            for target, names in self._imports(module, tree, set(modules)).items():
                dep = owner[target]
                if dep != comp.id:
                    comp.imports.setdefault(dep, set()).update(names)
            self._read_tables(comp, tree)
        return components

    @staticmethod
    def _read_definitions(comp: ComponentFacts, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    comp.private.append(node.name)
                    continue
                bases = {
                    b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
                    for b in node.bases
                }
                methods = [n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
                abstract = any(
                    (isinstance(d, ast.Name) and d.id == "abstractmethod")
                    or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
                    for m in methods
                    for d in m.decorator_list
                )
                public = tuple(m.name for m in methods if not m.name.startswith("_"))
                comp.classes.append(ClassFact(node.name, "ABC" in bases or abstract, public))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                (comp.private if node.name.startswith("_") else comp.functions).append(node.name)

    @staticmethod
    def _imports(module: _Module, tree: ast.Module, ids: set[str]) -> dict[str, set[str]]:
        def longest(name: str) -> str | None:
            parts = name.split(".")
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                if candidate in ids:
                    return candidate
            return None

        package = module.id.split(".") if module.is_package_init else module.id.split(".")[:-1]
        found: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                    base = ".".join([*anchor, node.module] if node.module else anchor)
                else:
                    base = node.module or ""
                for alias in node.names:
                    target = f"{base}.{alias.name}" if f"{base}.{alias.name}" in ids else longest(base)
                    if target:
                        found.setdefault(target, set()).add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = longest(alias.name)
                    if target:
                        found.setdefault(target, set()).add(alias.name)
        return found

    def _read_tables(self, comp: ComponentFacts, tree: ast.Module) -> None:
        rules: tuple[TableRule, ...] = self.conventions.table_rules
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and rules:
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                for rule in rules:
                    if name == rule.call and len(node.args) > rule.arg:
                        arg = node.args[rule.arg]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            (comp.reads if rule.mode == "reads" else comp.writes).add(arg.value)
            if (
                self.conventions.sql_tables
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_HINT.search(node.value)
            ):
                writes = set(SQL_WRITES.findall(node.value))
                comp.writes |= writes
                comp.reads |= set(SQL_READS.findall(node.value)) - writes
