import json
import re

from devloop.diagram import Diagram, Link, Node, _ranks, _wrap


def test_long_ids_can_break_at_dots_and_text_is_escaped() -> None:
    d = Diagram()
    d.add(Node("a", "src.shop.orders.dedupe", "Keeps <b>latest</b> & drops rest", note="changes: boundary"), "orders")
    out = d.to_html()
    assert "src.<wbr>shop.<wbr>orders.<wbr>dedupe" in out
    assert "&lt;b&gt;latest&lt;/b&gt; &amp; drops rest" in out
    assert "<b>latest</b>" not in out


def test_groups_follow_the_arrows_left_to_right() -> None:
    d = Diagram()
    d.add(Node("db", "db"), "data")
    d.add(Node("api", "api"), "web")
    d.add(Node("svc", "svc"), "core")
    d.link("api", "svc")
    d.link("svc", "db")
    assert [g["title"] for g in d.to_dict()["groups"]] == ["web", "core", "data"]  # type: ignore[union-attr]


def test_ordered_diagrams_keep_the_order_they_were_built_in() -> None:
    d = Diagram(ordered=True)
    d.add(Node("z", "z"), "Before")
    d.add(Node("a", "a"), "After")
    d.link("a", "z")
    assert [g["title"] for g in d.to_dict()["groups"]] == ["Before", "After"]  # type: ignore[union-attr]


def test_cycles_and_self_links_do_not_break_ranking() -> None:
    links = [Link("a", "b"), Link("b", "c"), Link("c", "a"), Link("a", "a")]
    ranks = _ranks(["a", "b", "c"], links)
    assert sorted(ranks.values()) == [0, 1, 2]


def test_html_carries_links_and_a_connections_list_without_script() -> None:
    d = Diagram()
    d.add(Node("a", "alpha"), "g")
    d.add(Node("t", "raw.orders", shape="table"), "Tables")
    d.link("a", "t", "writes", "added")
    d.link("a", "missing-node")
    out = d.to_html()
    assert 'class="node table"' in out
    assert "Connections (1)" in out and "alpha → raw.<wbr>orders" in out
    data = out.split('data-links="', 1)[1].split('"', 1)[0]
    assert "&quot;kind&quot;: &quot;added&quot;" in data


def test_json_and_markdown_outputs() -> None:
    d = Diagram()
    d.add(Node("a", "alpha", "does things", kind="added"), "g")
    d.add(Node("b", "beta"), "g")
    d.link("a", "b", "calls", "added")
    data = json.loads(d.to_json())
    assert data["groups"][0]["nodes"][0] == {"id": "a", "title": "alpha", "body": "does things", "kind": "added"}
    assert data["links"] == [{"a": "a", "b": "b", "label": "calls", "kind": "added"}]
    d.add(Node("c", "gamma"), "g")
    d.link("b", "c")
    md = d.to_markdown()
    assert "- **Added:**\n  - `alpha`: does things" in md
    assert "- **New connection:** `alpha` → `beta` (calls)" in md
    assert "- Not flagged: 2 unchanged boxes and 1 ordinary connection." in md
    assert "mermaid" not in md
    assert Diagram().to_markdown() == "- Nothing to show.\n"


def test_mermaid_breaks_lines_itself_and_never_passes_markup_through() -> None:
    d = Diagram()
    d.add(
        Node(
            "a", "src.shop.orders.dedupe.something.long", 'Keeps <b>latest</b> and drops "the rest" | now', kind="added"
        ),
        "orders",
    )
    d.add(Node("t", "raw.orders", shape="table"), "Tables")
    d.link("a", "t", "writes", "added")
    d.link("a", "gone")
    text = d.to_mermaid()
    assert text.startswith("flowchart LR\n")
    assert "subgraph g" in text and "[(" in text and "n0 --> |" not in text
    assert "n0 -->|writes| n1" in text and "gone" not in text
    assert "linkStyle 0 stroke:#2f62a3" in text
    assert "classDef added" in text and "class n0 added" in text
    d.add(Node("c", "uses `loop` here", "x"), "a group with `ticks`")
    assert "`" not in d.to_mermaid()
    assert "#lt;b#gt;latest#lt;/b#gt;" in text and "#quot;the rest#quot;" in text and "| now" not in text
    assert all(len(x) <= 40 for x in re.findall(r"<b>(.*?)</b>", text)[0].split("<br/>"))  # no long unbroken line


def test_wrap_breaks_at_spaces_and_after_dots_not_inside_words() -> None:
    lines = _wrap("src.shop.orders.dedupe responsibility to write", 16)
    assert all(len(x) <= 16 for x in lines) and "".join(lines).replace(
        " ", ""
    ) == "src.shop.orders.dedupe responsibility to write".replace(" ", "")
    assert _wrap("supercalifragilisticexpialidocious", 10) == ["supercalifragilisticexpialidocious"]


def test_mermaid_group_titles_keep_their_line_breaks_and_lone_boxes_stack() -> None:
    d = Diagram()
    for i in range(8):
        d.add(Node(f"n{i}", f"box {i}"), "A rather long group title that has to wrap")
    text = d.to_mermaid()
    assert text.startswith("flowchart TB\n")
    assert '["A rather long group title<br/>that has to wrap"]' in text and "#lt;br" not in text
