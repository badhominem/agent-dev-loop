"""Facts read straight from source code. Nothing here is ever stored (P3, P7).

What is scanned is set by conventions.include: folders and single files, relative
to the repo root. A component's id is its location, as a dotted path without the
extension: src/app/orders.py is `src.app.orders`, src/app/__init__.py is `src.app`.
Names are kept as they are; each component also keeps its path parts, so a folder
called `v1.2` is never split apart.

A component is a package or a module, set by conventions.component_level:
"auto" (default) makes each module directly in the top package a component and
each sub-package one component; "package" groups modules by their folder;
"module" makes every module a component. The top package is the first folder
inside an included container folder (src, lib), or the included folder itself.
Facts per Python component: public classes (and which are interfaces), public and
private functions, dependencies on other components, tables read or written, and
which listed data or config files it refers to by name.

Files that aren't Python (program, data and config files) are only listed, with
name, kind and path, on the component that owns their folder. A folder with no
Python of its own, such as config/, becomes a component with nothing but its files.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from devloop.config import IGNORED_DIRS, Conventions, TableRule

ROOT_ID = "root"  # the component for files directly in the repo root

# What the scanner calls a file, by extension. Files with other extensions are skipped
# when found by walking a folder, and listed as "other" when included by name.
EXTENSIONS: dict[str, str] = {
    ".py": "python",
    **dict.fromkeys(
        (".sh", ".bash", ".ps1", ".bat", ".sql", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".java", ".kt", ".scala")
        + (".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".cs", ".r", ".lua", ".pl"),
        "program",
    ),
    **dict.fromkeys((".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".parquet", ".avro", ".xml", ".xlsx"), "data"),
    **dict.fromkeys((".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties"), "config"),
}
NAMES: dict[str, str] = {
    "Makefile": "program",
    "Dockerfile": "program",
    "package.json": "config",
    "tsconfig.json": "config",
}

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


@dataclass(frozen=True)
class FileFact:
    name: str
    kind: str  # python, program, data, config or other
    path: str  # relative to the repo root, with "/"


@dataclass
class ComponentFacts:
    id: str
    parts: tuple[str, ...] = ()  # the location, one entry per folder or file name
    top: int = 0  # index in parts of the top package
    modules: list[str] = field(default_factory=list)
    classes: list[ClassFact] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    private: list[str] = field(default_factory=list)
    imports: dict[str, set[str]] = field(default_factory=dict)  # component id -> imported names
    files: list[FileFact] = field(default_factory=list)  # data, config and program files in this component
    uses: dict[str, set[str]] = field(default_factory=dict)  # component id -> files of it referred to by name
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    lines: int = 0
    is_package: bool = False

    def default_boundary(self) -> str:
        """Sub-packages are their own boundary; modules belong to their package."""
        rel = self.parts[self.top :] or self.parts
        if not rel:
            return self.id
        if self.is_package:
            return rel[1] if len(rel) >= 2 else rel[0]
        return rel[-2] if len(rel) >= 2 else rel[0]

    def public_names(self) -> set[str]:
        names = {c.name for c in self.classes} | set(self.functions)
        return names | {f"{c.name}.{m}" for c in self.classes for m in c.public_methods}

    def interfaces(self) -> list[str]:
        return [c.name for c in self.classes if c.is_abc]


@dataclass
class _File:
    path: Path
    rel: str  # repo-relative path with "/"
    kind: str
    parts: tuple[str, ...]  # location without the extension; "__init__" dropped
    folder: tuple[str, ...]  # the folders the file sits in
    entry: tuple[str, ...]  # the include entry it was found under (its folder, for a file entry)
    top: int
    is_init: bool = False

    @property
    def id(self) -> str:
        return ".".join(self.parts)

    def import_names(self) -> set[str]:
        """Names Python code could import this module by, depending on where sys.path starts."""
        names = {self.id}
        if len(self.parts) > len(self.entry):
            names.add(".".join(self.parts[len(self.entry) :]))
        if self.entry:
            names.add(".".join(self.parts[len(self.entry) - 1 :]))
        return {n for n in names if n}


class FactExtractor:
    def __init__(self, repo: Path, conventions: Conventions) -> None:
        self.repo = repo.resolve()
        self.conventions = conventions
        self.warnings: list[str] = []

    # ---- discovery

    def _excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        return any(
            fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(parts[-1], g) or any(fnmatch.fnmatch(x, g) for x in parts[:-1])
            for g in (g.rstrip("/") for g in self.conventions.exclude)
        )

    def _ignored_dir(self, name: str) -> bool:
        return name.startswith(".") or name in IGNORED_DIRS or name.endswith(".egg-info")

    def _top(self, entry: tuple[str, ...]) -> int:
        container = bool(entry) and entry[-1] in self.conventions.containers
        return len(entry) if container else max(len(entry) - 1, 0)

    def _file(self, path: Path, entry: tuple[str, ...], explicit: bool) -> _File | None:
        rel = path.relative_to(self.repo)
        kind = NAMES.get(path.name) or EXTENSIONS.get(path.suffix.lower()) or ("other" if explicit else "")
        if not kind or self._excluded(rel.as_posix()):
            return None
        is_init = kind == "python" and path.stem == "__init__"
        stem = rel.with_suffix("").parts if kind == "python" else rel.parts
        parts = stem[:-1] if is_init else stem
        if not parts:
            return None
        return _File(path, rel.as_posix(), kind, tuple(parts), rel.parts[:-1], entry, self._top(entry), is_init)

    def _scan(self) -> list[_File]:
        found: dict[Path, _File] = {}
        for raw in self.conventions.include:
            target = (self.repo / raw.rstrip("/")).resolve()
            try:
                target.relative_to(self.repo)
            except ValueError:
                self.warnings.append(f"Skipped include {raw}: it is outside the repo.")
                continue
            if target.is_file():
                entry = target.relative_to(self.repo).parts[:-1]
                candidates = [(target, entry, True)]
            elif target.is_dir():
                entry = target.relative_to(self.repo).parts
                candidates = [(p, entry, False) for p in self._walk(target)]
            else:
                self.warnings.append(f"Skipped include {raw}: nothing there.")
                continue
            for path, entry, explicit in candidates:
                if path not in found and (item := self._file(path, entry, explicit)):
                    found[path] = item
        return sorted(found.values(), key=lambda f: f.rel)

    def _walk(self, folder: Path) -> list[Path]:
        out: list[Path] = []
        for child in sorted(folder.iterdir()):
            if child.is_dir():
                if not self._ignored_dir(child.name) and not child.is_symlink():
                    out += self._walk(child)
            elif child.is_file() and not child.name.startswith("."):
                out.append(child)
        return out

    def component_of(self, file: _File) -> tuple[str, ...]:
        parts, top = file.parts, file.top
        level = self.conventions.component_level
        if level == "module":
            return parts
        if level == "package":
            if file.is_init:
                return parts
            return parts[:-1] if len(parts) > top + 1 else parts
        return parts[: top + 2] if len(parts) >= top + 3 else parts  # auto

    # ---- extraction

    def extract(self) -> dict[str, ComponentFacts]:
        files = self._scan()
        modules: dict[str, _File] = {}
        for f in files:
            if f.kind != "python":
                continue
            if f.id in modules:
                self.warnings.append(f"Skipped {f.rel}: {modules[f.id].rel} already has the id {f.id}.")
            else:
                modules[f.id] = f
        names: dict[str, set[str]] = {}
        for m in modules.values():
            for n in m.import_names():
                names.setdefault(n, set()).add(m.id)
        owner = {m.id: self.component_of(m) for m in modules.values()}
        components: dict[str, ComponentFacts] = {}
        trees: dict[str, ast.Module] = {}
        for module in modules.values():
            try:
                text = module.path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (SyntaxError, UnicodeDecodeError) as error:
                self.warnings.append(f"Skipped {module.path}: {error}")
                continue
            if module.is_init and not tree.body:
                continue  # an empty __init__.py is not a component of its own
            comp = self._component(components, owner[module.id], module.top)
            if comp is None:
                continue
            trees[module.id] = tree
            comp.modules.append(module.id)
            comp.is_package = comp.is_package or module.is_init or comp.parts != module.parts
            comp.lines += len(text.splitlines())
            self._read_definitions(comp, tree)
            for target, imported in self._imports(module, tree, names, modules, self.warnings).items():
                dep = ".".join(owner[target])
                if dep != comp.id:
                    comp.imports.setdefault(dep, set()).update(imported)
            self._read_tables(comp, tree)
        self._attach_files(components, [f for f in files if f.kind != "python"])
        for comp in components.values():
            comp.imports = {d: n for d, n in comp.imports.items() if d in components}
        self._read_uses(components, modules, trees)
        return components

    def _component(
        self, components: dict[str, ComponentFacts], parts: tuple[str, ...], top: int
    ) -> ComponentFacts | None:
        cid = ".".join(parts) if parts else ROOT_ID
        found = components.get(cid)
        if found is None:
            found = components[cid] = ComponentFacts(cid, parts, top)
        elif found.parts != parts:
            self.warnings.append(f"Skipped a component at {'/'.join(parts)}: {cid} is already {'/'.join(found.parts)}.")
            return None
        return found

    # ---- files that aren't Python

    def _attach_files(self, components: dict[str, ComponentFacts], files: list[_File]) -> None:
        for f in files:
            candidates = [c for c in components.values() if c.is_package and f.folder[: len(c.parts)] == c.parts]
            owner = max(candidates, key=lambda c: len(c.parts), default=None)
            if owner is None:
                owner = self._component(components, f.folder, f.top)
                if owner is not None:
                    owner.is_package = True
            if owner is not None:
                owner.files.append(FileFact(f.path.name, f.kind, f.rel))

    def _read_uses(
        self, components: dict[str, ComponentFacts], modules: dict[str, _File], trees: dict[str, ast.Module]
    ) -> None:
        """A Python component uses a listed file when a string in its code names that file."""
        listed = {fact.path: c.id for c in components.values() for fact in c.files}
        if not listed:
            return
        by_name: dict[str, list[str]] = {}
        for path in listed:
            by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        owners = {m: ".".join(self.component_of(modules[m])) for m in trees}
        for mid, tree in sorted(trees.items()):
            comp = components.get(owners[mid])
            if comp is None:
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or len(node.value) > 260:
                    continue
                text = node.value.strip().replace("\\", "/").removeprefix("./")
                if not text or "\n" in text:
                    continue
                hits = self._match_file(text, listed, by_name)
                if len(hits) > 1:
                    self.warnings.append(
                        f"{comp.id} names {text!r}, which matches {len(hits)} files: {', '.join(hits)}."
                    )
                elif hits:
                    comp.uses.setdefault(listed[hits[0]], set()).add(hits[0])

    @staticmethod
    def _match_file(text: str, listed: dict[str, str], by_name: dict[str, list[str]]) -> list[str]:
        if text in listed:
            return [text]
        if "/" not in text:
            return by_name.get(text, [])
        return sorted(p for p in by_name.get(text.rsplit("/", 1)[-1], []) if p.endswith("/" + text))

    # ---- Python facts

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
    def _imports(
        module: _File,
        tree: ast.Module,
        names: dict[str, set[str]],
        modules: dict[str, _File],
        warnings: list[str],
    ) -> dict[str, set[str]]:
        """Modules this one imports, by id. Absolute names match by where Python could be started from."""

        def resolve(name: str) -> str | None:
            ids = names.get(name, set())
            if len(ids) > 1:
                warnings.append(f"{module.id} imports {name}, which matches {', '.join(sorted(ids))}; no edge drawn.")
            return next(iter(ids)) if len(ids) == 1 else None

        def longest(name: str) -> str | None:
            parts = name.split(".")
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                if candidate in names:
                    return resolve(candidate)
            return None

        package = list(module.parts) if module.is_init else list(module.parts[:-1])
        found: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                    base = ".".join([*anchor, *(node.module.split(".") if node.module else [])])
                    for alias in node.names:
                        sub = f"{base}.{alias.name}" if base else alias.name
                        target = sub if sub in modules else base if base in modules else None
                        if target:
                            found.setdefault(target, set()).add(alias.name)
                    continue
                base = node.module or ""
                for alias in node.names:
                    sub = f"{base}.{alias.name}"
                    target = resolve(sub) if sub in names else longest(base)
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
