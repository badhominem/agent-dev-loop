"""A small diagram model drawn as plain HTML (no diagram library).

A diagram is groups of boxes plus arrows between boxes. Text wraps inside the boxes
and long ids break at the dots, so no word is ever cut. Boxes and groups are laid out
by CSS, in rank order (what depends on what, left to right, then wrapping). The arrows
are drawn on top by the few lines of script in templates/report.html. Without script,
every diagram still shows its boxes and a "Connections" list with the same arrows.

The same model is written as JSON for agents and as a short outline for pull requests.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

# How arrows are drawn, by kind. The report template reads this table, so it lives here once.
EDGE_STYLES: dict[str, dict[str, object]] = {
    "default": {"color": "#5d6b68", "width": 1.6, "dash": ""},
    "uses": {"color": "#8e9a98", "width": 1.2, "dash": "2 4"},
    "added": {"color": "#2f62a3", "width": 2.5, "dash": ""},
    "removed": {"color": "#7d8584", "width": 1.5, "dash": "5 4"},
    "missing": {"color": "#2f62a3", "width": 2, "dash": "5 4"},
    "unplanned": {"color": "#6a4fa3", "width": 2.5, "dash": ""},
    "broken": {"color": "#b53a3a", "width": 3, "dash": ""},
}


# How flagged boxes and arrows are named in text summaries, in the order they are listed.
KIND_WORDS = {
    "broken": "Breaks the approved contract",
    "failing": "Failing",
    "unplanned": "Built, not planned",
    "missing": "Planned, not built",
    "added": "Added",
    "changed": "Changed",
    "removed": "Removed",
    "uncovered": "No test yet",
    "passing": "Passing",
    "uncertain": "Draft to confirm",
}
LINK_WORDS = {
    "broken": "Breaks the contract",
    "unplanned": "Built, not planned",
    "missing": "Planned, not built",
    "added": "New connection",
    "removed": "Connection removed",
}


# Box colours for Mermaid, which can't read the report's stylesheet: fill, border, extra style.
NODE_STYLES: dict[str, str] = {
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


def _wrap(text: str, width: int = 26) -> list[str]:
    """Lines of at most `width` characters where possible, broken at spaces and after dots or slashes only."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        for i, piece in enumerate(re.findall(r"[^./]+[./]?|[./]", word)):
            gap = " " if i == 0 and line else ""
            if line and len(line) + len(gap) + len(piece) > width:
                lines.append(line)
                line, gap = "", ""
            line += gap + piece
    return [*lines, line] if line else lines


def _mm(text: str) -> str:
    """Safe inside a quoted Mermaid label: no markup, quotes or pipes of our own."""
    text = text.replace("`", "'").replace("|", "/")  # backticks would start Mermaid markdown, pipes end edge labels
    for char, code in (
        ("&", "amp"),
        ("<", "lt"),
        (">", "gt"),
        ('"', "quot"),
    ):
        text = text.replace(char, f"#{code};")
    for char in "_*":  # a zero-width space keeps __main__ and *x* from being read as bold or italic
        text = text.replace(char, char + "\u200b")
    return text


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    body: str = ""  # one or two plain sentences, e.g. the responsibility
    items: str = ""  # shown in italics, e.g. key items
    note: str = ""  # small print, e.g. "changes: boundary"
    kind: str | None = None  # added, changed, removed, missing, unplanned, broken, uncertain, passing...
    shape: str = "box"  # "box" or "table"


@dataclass(frozen=True)
class Link:
    a: str
    b: str
    label: str = ""
    kind: str | None = None
    dotted: bool = False


@dataclass
class Group:
    title: str | None  # None: boxes sit in an untitled row
    nodes: list[Node] = field(default_factory=list)


def _text(value: str) -> str:
    """Escaped, with a place to break after each dot or slash so ids wrap between words."""
    return html.escape(value).replace(".", ".<wbr>").replace("/", "/<wbr>")


def _prose(value: str) -> str:
    return html.escape(value)


def _ranks(ids: list[str], links: list[Link]) -> dict[str, int]:
    """Depth along the arrows, ignoring the arrows that close a cycle."""
    known = set(ids)
    out: dict[str, list[str]] = {i: [] for i in ids}
    for link in links:
        if link.a in known and link.b in known and link.a != link.b:
            out[link.a].append(link.b)
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()
    finished: list[str] = []
    for root in sorted(ids):
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(out[root]))]
        while stack:
            node, rest = stack[-1]
            for nxt in rest:
                if nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out[nxt])))
                    break
                if state[nxt] == 1:
                    back.add((node, nxt))
            else:
                state[node] = 2
                finished.append(node)
                stack.pop()
    rank = dict.fromkeys(ids, 0)
    for node in reversed(finished):
        for nxt in out[node]:
            if (node, nxt) not in back:
                rank[nxt] = max(rank[nxt], rank[node] + 1)
    return rank


class Diagram:
    def __init__(self, ordered: bool = False) -> None:
        self.ordered = ordered  # True: keep groups in the order they were added
        self.groups: list[Group] = []
        self.links: list[Link] = []

    # ---- building

    def add(self, node: Node, group: str | None = None) -> None:
        for g in self.groups:
            if g.title == group:
                g.nodes.append(node)
                return
        self.groups.append(Group(group, [node]))

    def link(self, a: str, b: str, label: str = "", kind: str | None = None, dotted: bool = False) -> None:
        self.links.append(Link(a, b, label, kind, dotted))

    def nodes(self) -> list[Node]:
        return [n for g in self.groups for n in g.nodes]

    def _layout(self) -> list[Group]:
        if self.ordered:
            return self.groups
        rank = _ranks([n.id for n in self.nodes()], self.links)
        groups = [Group(g.title, sorted(g.nodes, key=lambda n: (rank[n.id], n.id))) for g in self.groups]
        return sorted(groups, key=lambda g: (min(rank[n.id] for n in g.nodes), g.title or "", g.nodes[0].id))

    def _label(self, node_id: str) -> str:
        return next((n.title for n in self.nodes() if n.id == node_id), node_id)

    # ---- output

    def to_dict(self) -> dict[str, object]:
        return {
            "groups": [
                {
                    "title": g.title,
                    "nodes": [
                        {k: v for k, v in n.__dict__.items() if v not in ("", None, "box") or k in ("id", "title")}
                        for n in g.nodes
                    ],
                }
                for g in self._layout()
            ],
            "links": [{k: v for k, v in link.__dict__.items() if v not in ("", None, False)} for link in self.links],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_html(self) -> str:
        groups = self._layout()
        ids = {n.id for n in self.nodes()}
        links = [link for link in self.links if link.a in ids and link.b in ids]
        rank = {} if self.ordered else _ranks(sorted(ids), self.links)
        parts = []
        for g in groups:
            columns: dict[int, list[Node]] = {}
            for n in g.nodes:
                columns.setdefault(rank.get(n.id, 0), []).append(n)  # one column per depth, so arrows run sideways
            boxes = "".join(
                f'<div class="col">{"".join(self._node_html(n) for n in columns[r])}</div>' for r in sorted(columns)
            )
            if g.title is None:
                parts.append(f'<div class="grp flat"><div class="nodes">{boxes}</div></div>')
            else:
                parts.append(
                    f'<section class="grp"><h3>{_text(g.title)}</h3><div class="nodes">{boxes}</div></section>'
                )
        data = [{"a": x.a, "b": x.b, "label": x.label, "kind": x.kind or "default", "dotted": x.dotted} for x in links]
        listing = "".join(
            f"<li>{_text(self._label(x.a))} → {_text(self._label(x.b))}"
            + (f" <em>({html.escape(x.label)})</em>" if x.label else "")
            + "</li>"
            for x in links
        )
        more = (
            f'<details class="conn"><summary>Connections ({len(links)})</summary><ul>{listing}</ul></details>'
            if links
            else ""
        )
        return (
            f'<div class="dg" data-links="{html.escape(json.dumps(data), quote=True)}">'
            f'<div class="dg-body">{"".join(parts)}</div>'
            f'<svg class="dg-edges" aria-hidden="true"></svg>{more}</div>'
        )

    @staticmethod
    def _node_html(n: Node) -> str:
        shape = " table" if n.shape == "table" else ""
        kind = f" k-{n.kind}" if n.kind else ""
        inner = f'<b class="t">{_text(n.title)}</b>'
        for css, text in (("b", n.body), ("i", n.items), ("n", n.note)):
            if text:
                inner += f'<span class="{css}">{_prose(text)}</span>'
        return f'<div class="node{shape}{kind}" data-id="{html.escape(n.id, quote=True)}" tabindex="0">{inner}</div>'

    def to_mermaid(self) -> str:
        """Mermaid text, which GitHub draws in a pull request. Lines are broken here so Mermaid never has to wrap."""
        out: list[str] = []
        names = {n.id: f"n{i}" for i, n in enumerate(self.nodes())}
        used: dict[str, list[str]] = {}
        for gi, g in enumerate(self._layout()):
            if g.title is not None:
                title = "<br/>".join(_mm(x) for x in _wrap(g.title))
                out.append(f'  subgraph g{gi}["{title}"]')
            for n in g.nodes:
                label = ["<b>" + "<br/>".join(_mm(x) for x in _wrap(n.title)) + "</b>"]
                label += [_mm(x) for x in _wrap(n.body)]
                label += ["<i>" + "<br/>".join(_mm(x) for x in _wrap(n.items)) + "</i>"] if n.items else []
                label += ["<small>" + "<br/>".join(_mm(x) for x in _wrap(n.note)) + "</small>"] if n.note else []
                text = "<br/>".join(label)
                out.append(f'  {names[n.id]}[("{text}")]' if n.shape == "table" else f'  {names[n.id]}["{text}"]')
                if n.kind in NODE_STYLES:
                    used.setdefault(n.kind, []).append(names[n.id])
            if g.title is not None:
                out += ["  end", f"  style g{gi} fill:#f1f4f3,stroke:#c9d3d0,color:#16202a"]
        out.insert(0, "flowchart LR" if self.links or len(names) <= 6 else "flowchart TB")  # lone boxes stack
        styles: list[str] = []
        shown = 0
        for x in self.links:
            if x.a not in names or x.b not in names:
                continue
            arrow = "-.->" if x.dotted else "-->"
            text = f"|{_mm(x.label)}|" if x.label else ""
            out.append(f"  {names[x.a]} {arrow}{text} {names[x.b]}")
            look = EDGE_STYLES.get(x.kind or "default", EDGE_STYLES["default"])
            dash = f",stroke-dasharray:{look['dash']}" if look["dash"] else ""
            styles.append(f"  linkStyle {shown} stroke:{look['color']},stroke-width:{look['width']}px{dash}")
            shown += 1
        out += styles
        for kind, ids in sorted(used.items()):
            out += [f"  classDef {kind} {NODE_STYLES[kind]}", f"  class {','.join(ids)} {kind}"]
        return "\n".join(out) + "\n"

    def to_markdown(self) -> str:
        """A text summary for places that can't draw HTML (a pull request): what is flagged, then the rest counted."""
        lines: list[str] = []
        for kind, words in KIND_WORDS.items():
            flagged = [n for g in self._layout() for n in g.nodes if n.kind == kind]
            if flagged:
                lines.append(f"- **{words}:**")
                for n in flagged:
                    detail = " — ".join(x for x in (n.body, n.note) if x)
                    lines.append(f"  - `{n.title}`" + (f": {detail}" if detail else ""))
        for kind, words in LINK_WORDS.items():
            for x in (link for link in self.links if link.kind == kind):
                label = f" ({x.label})" if x.label else ""
                lines.append(f"- **{words}:** `{self._label(x.a)}` → `{self._label(x.b)}`{label}")
        plain_nodes = sum(1 for n in self.nodes() if n.kind is None)
        plain_links = sum(1 for x in self.links if x.kind in (None, "uses"))
        rest = []
        if plain_nodes:
            rest.append(f"{plain_nodes} unchanged {'box' if plain_nodes == 1 else 'boxes'}")
        if plain_links:
            rest.append(f"{plain_links} ordinary {'connection' if plain_links == 1 else 'connections'}")
        if rest:
            lines.append(f"- Not flagged: {' and '.join(rest)}.")
        return "\n".join(lines or ["- Nothing to show."]) + "\n"
