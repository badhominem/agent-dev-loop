"""The six views, drawn as Mermaid by code from the indexes (P3). No AI here."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from devloop.facts import ComponentFacts
from devloop.index import Opinion, Scenario, changed_fields, test_name

INIT = (
    "%%{init: {'theme':'base','themeVariables':{'primaryColor':'#ffffff','primaryBorderColor':'#7d8584',"
    "'primaryTextColor':'#16202a','lineColor':'#5d6b68','clusterBkg':'#f1f4f3','clusterBorder':'#c9d3d0',"
    "'edgeLabelBackground':'#ffffff','fontFamily':'system-ui, -apple-system, Segoe UI, Roboto, sans-serif'}}}%%"
)

NODE_STYLE = {
    "added": "fill:#dce9f7,stroke:#2f62a3,color:#16202a",
    "changed": "fill:#f6ebdd,stroke:#a8641f,color:#16202a",
    "removed": "fill:#eceeee,stroke:#7d8584,color:#5a6261,stroke-dasharray:4 3",
    "missing": "fill:#ffffff,stroke:#2f62a3,color:#16202a,stroke-dasharray:4 3",
    "unplanned": "fill:#ece5f7,stroke:#6a4fa3,color:#16202a",
    "broken": "fill:#f7dede,stroke:#b53a3a,color:#16202a,stroke-width:3px",
    "uncertain": "fill:#fffbe8,stroke:#a8641f,color:#16202a,stroke-dasharray:2 2",
    "passing": "fill:#dceee3,stroke:#2f7a4f,color:#16202a",
    "failing": "fill:#f7dede,stroke:#b53a3a,color:#16202a",
    "uncovered": "fill:#ffffff,stroke:#a8641f,color:#16202a,stroke-dasharray:4 3",
}
EDGE_STYLE = {
    "added": "stroke:#2f62a3,stroke-width:2.5px",
    "removed": "stroke:#7d8584,stroke-width:1.5px,stroke-dasharray:4 3",
    "missing": "stroke:#2f62a3,stroke-width:2px,stroke-dasharray:4 3",
    "unplanned": "stroke:#6a4fa3,stroke-width:2.5px",
    "broken": "stroke:#b53a3a,stroke-width:3px",
    "uses": "stroke:#9aa5a3,stroke-width:1px",
}


def nid(name: str) -> str:
    return "n_" + re.sub(r"\W", "_", name)


def short(component: str) -> str:
    return component.split(".", 1)[-1] if "." in component else component


def boundary(component: str) -> str:
    bits = component.split(".")
    return bits[1] if len(bits) >= 2 else bits[0]


def group_for(component: str, opinion: Opinion | None) -> str:
    """The boundary from the component file, or a guess from the id."""
    return opinion.boundary if opinion is not None and opinion.boundary else boundary(component)


def clean(text: str) -> str:
    """Keep labels safe inside Mermaid's quoted strings."""
    return text.replace('"', "'").replace("|", "/")


class Diagram:
    def __init__(self, direction: str = "TB") -> None:
        self._lines = [INIT, f"flowchart {direction}"]
        self._edges = 0
        self._edge_styles: list[str] = []
        self._classes: dict[str, list[str]] = {}

    def line(self, text: str) -> None:
        self._lines.append("  " + text)

    def node(self, node_id: str, label: str, kind: str | None = None, table: bool = False) -> None:
        text = clean(label)
        self.line(f'{node_id}[("{text}")]' if table else f'{node_id}["{text}"]')
        if kind:
            self._classes.setdefault(kind, []).append(node_id)

    def edge(self, a: str, b: str, label: str = "", kind: str | None = None, dotted: bool = False) -> None:
        arrow = "-.->" if dotted else "-->"
        self.line(f"{a} {arrow}|{clean(label)}| {b}" if label else f"{a} {arrow} {b}")
        if kind:
            self._edge_styles.append(f"linkStyle {self._edges} {EDGE_STYLE[kind]}")
        self._edges += 1

    def render(self) -> str:
        out = [*self._lines, *("  " + s for s in self._edge_styles)]
        for kind, ids in sorted(self._classes.items()):
            out.append(f"  classDef {kind} {NODE_STYLE[kind]}")
            out.append(f"  class {','.join(ids)} {kind}")
        return "\n".join(out) + "\n"


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


def label(comp: str, opinion: Opinion | None, note: str = "") -> str:
    parts = [f"<b>{short(comp)}</b>"]
    if opinion and opinion.responsibility:
        parts.append(opinion.responsibility)
    elif opinion is not None:
        parts.append("<small>responsibility to write</small>")
    if opinion and opinion.key_items:
        parts.append("<i>" + " · ".join(opinion.key_items[:4]) + "</i>")
    if note:
        parts.append(note)
    return "<br/>".join(parts)


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


def fact_edges(facts: dict[str, ComponentFacts], uses: bool) -> set[Edge]:
    edges: set[Edge] = set()
    for f in facts.values():
        edges |= {Edge(f"table:{t}", f.id, "reads") for t in f.reads}
        edges |= {Edge(f.id, f"table:{t}", "writes") for t in f.writes}
        if uses:
            edges |= {Edge(f.id, d, "uses") for d in f.imports}
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
) -> str:
    comps = sorted(n for n in nodes if not n.startswith("table:"))
    if len(comps) > max_nodes:
        return draw_boundaries(nodes, list(edges))
    d = Diagram(direction)
    groups = {c: group_for(c, nodes[c].opinion) for c in comps}
    for group in sorted(set(groups.values())):
        d.line(f'subgraph {nid("b_" + group)}["{group}"]')
        for c in (c for c in comps if groups[c] == group):
            info = nodes[c]
            d.node(nid(c), label(c, info.opinion, info.note), info.kind)
        d.line("end")
    for t in sorted(n for n in nodes if n.startswith("table:")):
        info = nodes[t]
        name = t.removeprefix("table:")
        d.node(nid(t), f"{name}<br/>{info.note}" if info.note else name, info.kind, table=True)
    order = {"writes": 0, "reads": 1, "flow": 2, "uses": 3}
    for e, kind in sorted(edges, key=lambda x: (order[x[0].kind], x[0].a, x[0].b, x[1] or "")):
        if e.a in nodes and e.b in nodes:
            style = kind or ("uses" if e.kind == "uses" else None)
            d.edge(nid(e.a), nid(e.b), e.what, style, dotted=e.kind == "uses" and kind != "broken")
    return d.render()


def draw_boundaries(nodes: dict[str, NodeInfo], edges: list[tuple[Edge, str | None]]) -> str:
    """Too many components to read: one box per boundary, with counts."""
    d = Diagram("LR")
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
        note = count + extra
        d.node(nid("g_" + group), f"<b>{group}</b><br/><small>{note}</small>", kind)
    links: dict[tuple[str, str], bool] = {}
    for e, k in edges:
        if e.kind == "uses" and not e.a.startswith("table:") and not e.b.startswith("table:"):
            ga = group_for(e.a, nodes[e.a].opinion if e.a in nodes else None)
            gb = group_for(e.b, nodes[e.b].opinion if e.b in nodes else None)
            if ga != gb:
                links[(ga, gb)] = links.get((ga, gb), False) or k == "broken"
    for (ga, gb), broken in sorted(links.items()):
        d.edge(nid("g_" + ga), nid("g_" + gb), "", "broken" if broken else "uses", dotted=not broken)
    return d.render()


# ---------------------------------------------------------------- the six views


def system_view(facts: dict[str, ComponentFacts], ops: dict[str, Opinion], max_nodes: int) -> str:
    nodes = {c: NodeInfo(ops.get(c), "uncertain" if ops.get(c) and ops[c].uncertain else None) for c in facts}
    edges = fact_edges(facts, uses=True)
    for e in edges:
        for x in (e.a, e.b):
            if x.startswith("table:"):
                nodes.setdefault(x, NodeInfo(None))
    return draw(nodes, [(e, None) for e in edges], max_nodes)


def change_view(
    before: dict[str, Opinion], planned: dict[str, Opinion], max_nodes: int, data_only: bool = False
) -> tuple[str, dict[str, list[str]]]:
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
            nodes[c] = NodeInfo(
                planned[c], "changed" if diff else None, f"<small>changes: {', '.join(diff)}</small>" if diff else ""
            )
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
) -> tuple[str, dict[str, str]]:
    status: dict[str, str] = {}
    d = Diagram("TB")
    ids = sorted(set(before) | set(after))
    if only_changed:
        ids = [i for i in ids if i not in before or i not in after or before[i].body != after[i].body]
    for feature in sorted({(after.get(i) or before[i]).feature for i in ids}):
        d.line(f'subgraph {nid("f_" + feature)}["{clean(feature)}"]')
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
            d.node(nid(sid), f"<b>{sid}</b><br/>{s.name}<br/><small>{change} · {kind}</small>", kind)
        d.line("end")
    return d.render(), status


def plan_vs_actual_view(
    planned: dict[str, Opinion], head_ops: dict[str, Opinion], head: dict[str, ComponentFacts], max_nodes: int
) -> tuple[str, list[Edge]]:
    nodes: dict[str, NodeInfo] = {}
    for c in sorted(set(planned) | set(head)):
        if c not in head:
            nodes[c] = NodeInfo(planned[c], "missing", "<small>planned, not built</small>")
        elif c not in planned:
            nodes[c] = NodeInfo(head_ops.get(c), "unplanned", "<small>built, not planned</small>")
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
) -> str:
    d = Diagram("LR")
    for stage in ("Before", "Planned", "Actual"):
        d.line(f'subgraph {stage}["{stage}"]')
        d.line("direction TB")
        a_id, b_id = nid(f"{stage}_{e.a}"), nid(f"{stage}_{e.b}")
        if stage == "Before" and e.a not in before_facts:
            d.node(a_id, f"<b>{short(e.a)}</b><br/><small>doesn't exist yet</small>", "removed")
        else:
            d.node(
                a_id,
                label(e.a, planned.get(e.a) if stage != "Before" else before_ops.get(e.a)),
                "broken" if stage == "Actual" else None,
            )
        d.node(b_id, label(e.b, before_ops.get(e.b) or planned.get(e.b)))
        d.line("end")
    if e.b in (before_facts[e.a].imports if e.a in before_facts else {}):
        d.edge(nid(f"Before_{e.a}"), nid(f"Before_{e.b}"), "already used", None)
    d.edge(nid(f"Planned_{e.a}"), nid(f"Planned_{e.b}"), "not allowed", "removed", dotted=True)
    d.edge(nid(f"Actual_{e.a}"), nid(f"Actual_{e.b}"), e.what, "broken")
    d.line("Before ~~~ Planned ~~~ Actual")
    return d.render()
