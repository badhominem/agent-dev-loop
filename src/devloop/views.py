"""The six views, drawn as HTML diagrams by code from the indexes (P3). No AI here."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from devloop.config import DEFAULT_CONTAINERS
from devloop.diagram import Diagram, Node
from devloop.facts import ComponentFacts
from devloop.index import Opinion, Scenario, changed_fields, test_name


def nid(name: str) -> str:
    return "n_" + re.sub(r"\W", "_", name)


def short(component: str) -> str:
    """Components are named by their location, so the id is already as short as it can be."""
    return component


def boundary(component: str) -> str:
    bits = component.split(".")
    if len(bits) >= 3 and bits[0] in DEFAULT_CONTAINERS:
        bits = bits[1:]
    return bits[1] if len(bits) >= 2 else bits[0]


def group_for(component: str, opinion: Opinion | None) -> str:
    """The boundary from the component file, or a guess from the id."""
    return opinion.boundary if opinion is not None and opinion.boundary else boundary(component)


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    kind: str  # reads, writes, flow, uses
    what: str = ""


@dataclass
class NodeInfo:
    opinion: Opinion | None
    kind: str | None = None
    note: str = ""


def node_for(comp: str, opinion: Opinion | None, kind: str | None = None, note: str = "", node_id: str = "") -> Node:
    body = ""
    if opinion and opinion.responsibility:
        body = opinion.responsibility
    elif opinion is not None:
        body = "responsibility to write"
    items = " · ".join(opinion.key_items[:4]) if opinion and opinion.key_items else ""
    return Node(node_id or nid(comp), short(comp), body, items, note, kind)


def tagged(edges: Iterable[Edge], kind: str | None) -> list[tuple[Edge, str | None]]:
    return [(e, kind) for e in edges]


def opinion_edges(ops: dict[str, Opinion], uses: bool) -> set[Edge]:
    edges: set[Edge] = set()
    for o in ops.values():
        edges |= {Edge(f"table:{t}", o.id, "reads") for t in o.reads}
        edges |= {Edge(o.id, f"table:{t}", "writes") for t in o.writes}
        edges |= {Edge(o.id, f.to, "flow", f.what) for f in o.flows}
        if uses:
            edges |= {Edge(o.id, d, "uses") for d in o.may_depend_on}
    return edges


def fact_edges(facts: dict[str, ComponentFacts], uses: bool, files: bool = False) -> set[Edge]:
    """Edges read from code. With files, a component that names another's data or config file gets one too."""
    edges: set[Edge] = set()
    for f in facts.values():
        edges |= {Edge(f"table:{t}", f.id, "reads") for t in f.reads}
        edges |= {Edge(f.id, f"table:{t}", "writes") for t in f.writes}
        if uses:
            edges |= {Edge(f.id, d, "uses") for d in f.imports}
        if files:
            for dep, paths in f.uses.items():
                if dep != f.id and dep not in f.imports and dep in facts:
                    edges.add(Edge(f.id, dep, "uses", ", ".join(sorted(p.rsplit("/", 1)[-1] for p in paths))))
    return edges


def scope(
    nodes: dict[str, NodeInfo], edges: list[tuple[Edge, str | None]]
) -> tuple[dict[str, NodeInfo], list[tuple[Edge, str | None]], int]:
    """Keep touched nodes and their direct neighbours. Returns (nodes, edges, hidden count)."""
    touched = {n for n, info in nodes.items() if info.kind}
    touched |= {x for e, k in edges if k for x in (e.a, e.b)}
    if not touched:
        return nodes, edges, 0
    keep = set(touched)
    keep |= {x for e, _ in edges if e.a in touched or e.b in touched for x in (e.a, e.b)}
    kept = {n: v for n, v in nodes.items() if n in keep}
    return kept, [(e, k) for e, k in edges if e.a in kept and e.b in kept], len(nodes) - len(kept)


def draw(
    nodes: dict[str, NodeInfo], edges: Iterable[tuple[Edge, str | None]], max_nodes: int, direction: str = "TB"
) -> Diagram:
    comps = sorted(n for n in nodes if not n.startswith("table:"))
    if len(comps) > max_nodes:
        return draw_boundaries(nodes, list(edges))
    d = Diagram()
    for c in comps:
        info = nodes[c]
        d.add(node_for(c, info.opinion, info.kind, info.note), group_for(c, info.opinion))
    for t in sorted(n for n in nodes if n.startswith("table:")):
        info = nodes[t]
        name = t.removeprefix("table:")
        d.add(Node(nid(t), name, note=info.note, kind=info.kind, shape="table"), "Tables")
    order = {"writes": 0, "reads": 1, "flow": 2, "uses": 3}
    for e, kind in sorted(edges, key=lambda x: (order[x[0].kind], x[0].a, x[0].b, x[1] or "")):
        if e.a in nodes and e.b in nodes:
            style = kind or ("uses" if e.kind == "uses" else None)
            d.link(nid(e.a), nid(e.b), e.what, style, dotted=e.kind == "uses" and kind != "broken")
    return d


def draw_boundaries(nodes: dict[str, NodeInfo], edges: list[tuple[Edge, str | None]]) -> Diagram:
    """Too many components to read: one box per boundary, with counts."""
    d = Diagram()
    groups: dict[str, list[str]] = {}
    for c in (n for n in nodes if not n.startswith("table:")):
        groups.setdefault(group_for(c, nodes[c].opinion), []).append(c)
    for group, members in sorted(groups.items()):
        flagged = [m for m in members if nodes[m].kind and nodes[m].kind != "uncertain"]
        drafts = [m for m in members if nodes[m].kind == "uncertain"]
        kind = "changed" if flagged else "uncertain" if drafts else None
        count = f"{len(members)} component{'s' if len(members) != 1 else ''}"
        extra = (
            f", {len(flagged)} affected"
            if flagged
            else f", {len(drafts)} draft{'s' if len(drafts) != 1 else ''}"
            if drafts
            else ""
        )
        d.add(Node(nid("g_" + group), group, note=count + extra, kind=kind))
    links: dict[tuple[str, str], bool] = {}
    for e, k in edges:
        if e.kind == "uses" and not e.a.startswith("table:") and not e.b.startswith("table:"):
            ga = group_for(e.a, nodes[e.a].opinion if e.a in nodes else None)
            gb = group_for(e.b, nodes[e.b].opinion if e.b in nodes else None)
            if ga != gb:
                links[(ga, gb)] = links.get((ga, gb), False) or k == "broken"
    for (ga, gb), broken in sorted(links.items()):
        d.link(nid("g_" + ga), nid("g_" + gb), "", "broken" if broken else "uses", dotted=not broken)
    return d


# ---------------------------------------------------------------- the six views


def system_view(facts: dict[str, ComponentFacts], ops: dict[str, Opinion], max_nodes: int) -> Diagram:
    nodes = {c: NodeInfo(ops.get(c), "uncertain" if ops.get(c) and ops[c].uncertain else None) for c in facts}
    edges = fact_edges(facts, uses=True, files=True)
    for e in edges:
        for x in (e.a, e.b):
            if x.startswith("table:"):
                nodes.setdefault(x, NodeInfo(None))
    return draw(nodes, [(e, None) for e in edges], max_nodes)


def change_view(
    before: dict[str, Opinion], planned: dict[str, Opinion], max_nodes: int, data_only: bool = False
) -> tuple[Diagram, dict[str, list[str]]]:
    nodes: dict[str, NodeInfo] = {}
    summary: dict[str, list[str]] = {
        "added": [],
        "changed": [],
        "removed": [],
        "tables added": [],
        "tables retired": [],
    }
    for c in sorted(set(before) | set(planned)):
        if c not in before:
            nodes[c] = NodeInfo(planned[c], "added")
            summary["added"].append(short(c))
        elif c not in planned:
            nodes[c] = NodeInfo(before[c], "removed")
            summary["removed"].append(short(c))
        else:
            diff = changed_fields(before[c], planned[c])
            nodes[c] = NodeInfo(planned[c], "changed" if diff else None, f"changes: {', '.join(diff)}" if diff else "")
            if diff:
                summary["changed"].append(short(c))
    b_e, a_e = opinion_edges(before, not data_only), opinion_edges(planned, not data_only)
    edges = tagged(b_e & a_e, None) + tagged(a_e - b_e, "added") + tagged(b_e - a_e, "removed")
    if data_only:
        edges = [(e, k) for e, k in edges if e.kind in ("reads", "writes")]
    t_b = {x for e in b_e for x in (e.a, e.b) if x.startswith("table:")}
    t_a = {x for e in a_e for x in (e.a, e.b) if x.startswith("table:")}
    for t in t_b | t_a:
        kind = "added" if t not in t_b else "removed" if t not in t_a else None
        nodes[t] = NodeInfo(None, kind)
        if kind == "added":
            summary["tables added"].append(t.removeprefix("table:"))
        elif kind == "removed":
            summary["tables retired"].append(t.removeprefix("table:"))
    if data_only:
        touching = {x for e, _ in edges for x in (e.a, e.b)}
        nodes = {n: v for n, v in nodes.items() if n in touching}
    nodes, edges, _ = scope(nodes, edges)
    return draw(nodes, edges, max_nodes), summary


def behaviour_view(
    before: dict[str, Scenario], after: dict[str, Scenario], results: dict[str, str], only_changed: bool
) -> tuple[Diagram, dict[str, str]]:
    status: dict[str, str] = {}
    d = Diagram(ordered=True)
    ids = sorted(set(before) | set(after))
    if only_changed:
        ids = [i for i in ids if i not in before or i not in after or before[i].body != after[i].body]
    for feature in sorted({(after.get(i) or before[i]).feature for i in ids}):
        for sid in ids:
            s = after.get(sid) or before[sid]
            if s.feature != feature:
                continue
            if sid not in after:
                change, kind = "retired", "removed"
            else:
                change = "new" if sid not in before else "changed" if before[sid].body != s.body else "unchanged"
                kind = results.get(test_name(s.name), "uncovered")
                status[sid] = kind
            d.add(Node(nid(sid), sid, s.name, note=f"{change} · {kind}", kind=kind), feature)
    return d, status


def plan_vs_actual_view(
    planned: dict[str, Opinion], head_ops: dict[str, Opinion], head: dict[str, ComponentFacts], max_nodes: int
) -> tuple[Diagram, list[Edge]]:
    nodes: dict[str, NodeInfo] = {}
    for c in sorted(set(planned) | set(head)):
        if c not in head:
            nodes[c] = NodeInfo(planned[c], "missing", "planned, not built")
        elif c not in planned:
            nodes[c] = NodeInfo(head_ops.get(c), "unplanned", "built, not planned")
        else:
            nodes[c] = NodeInfo(head_ops.get(c) or planned[c])
    p_e, a_e = opinion_edges(planned, False), fact_edges(head, False)
    flows = {e for e in p_e if e.kind == "flow"}
    edges = tagged((p_e & a_e) | flows, None) + tagged(p_e - a_e - flows, "missing") + tagged(a_e - p_e, "unplanned")
    breaks: list[Edge] = []
    for comp, facts in head.items():
        allowed = set(planned[comp].may_depend_on) if comp in planned else set()
        for dep, names in sorted(facts.imports.items()):
            if dep not in allowed:
                e = Edge(comp, dep, "uses", "imports " + ", ".join(sorted(names)[:3]))
                edges.append((e, "broken"))
                breaks.append(e)
    for e, _ in edges:
        for x in (e.a, e.b):
            if x.startswith("table:"):
                nodes.setdefault(x, NodeInfo(None))
    nodes, edges, _ = scope(nodes, edges)
    return draw(nodes, edges, max_nodes), breaks


def contract_break_view(
    e: Edge, before_facts: dict[str, ComponentFacts], before_ops: dict[str, Opinion], planned: dict[str, Opinion]
) -> Diagram:
    d = Diagram(ordered=True)
    for stage in ("Before", "Planned", "Actual"):
        a_id, b_id = nid(f"{stage}_{e.a}"), nid(f"{stage}_{e.b}")
        if stage == "Before" and e.a not in before_facts:
            d.add(Node(a_id, short(e.a), note="doesn't exist yet", kind="removed"), stage)
        else:
            opinion = planned.get(e.a) if stage != "Before" else before_ops.get(e.a)
            d.add(node_for(e.a, opinion, "broken" if stage == "Actual" else None, node_id=a_id), stage)
        d.add(node_for(e.b, before_ops.get(e.b) or planned.get(e.b), node_id=b_id), stage)
    if e.b in (before_facts[e.a].imports if e.a in before_facts else {}):
        d.link(nid(f"Before_{e.a}"), nid(f"Before_{e.b}"), "already used", None)
    d.link(nid(f"Planned_{e.a}"), nid(f"Planned_{e.b}"), "not allowed", "removed", dotted=True)
    d.link(nid(f"Actual_{e.a}"), nid(f"Actual_{e.b}"), e.what, "broken")
    return d
