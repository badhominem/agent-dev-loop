import json

from devloop.diagram import Diagram, Link, Node, _ranks


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
    md = d.to_markdown()
    assert "- `alpha` _(added)_: does things" in md
    assert "- `alpha` → `beta` calls _(added)_" in md
    assert "mermaid" not in md
