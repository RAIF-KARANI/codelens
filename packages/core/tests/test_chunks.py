from textwrap import dedent

from core.enums import NodeKind
from core.text import CHUNK_FORMAT, ChunkSource, NodeInfo, build_chunk, estimate_tokens, skeleton
from core.text.chunks import TRUNCATION_MARKER

CLASS_CODE = dedent(
    '''
    class Order(Entity):
        """An order."""

        currency = "EUR"

        def total(self):
            return sum(self.items)

        @property
        def empty(self):
            return not self.items
    '''
).strip("\n")

ORDER = NodeInfo(NodeKind.CLASS, "Order", "shop.Order", "class Order(Entity)", 10, 20)
TOTAL = NodeInfo(NodeKind.METHOD, "total", "shop.Order.total", "def total(self)", 15, 16)
EMPTY = NodeInfo(NodeKind.METHOD, "empty", "shop.Order.empty", "def empty(self)", 18, 20)


def test_class_chunk_is_a_skeleton_with_member_signatures() -> None:
    chunk = build_chunk(
        ChunkSource(ORDER, "shop.py", CLASS_CODE, parent=None, children=(TOTAL, EMPTY)),
        max_tokens=1024,
    )

    assert chunk.text == dedent(
        f'''
        # {CHUNK_FORMAT} path: shop.py
        # symbol: shop.Order (class)
        class Order(Entity):
            """An order."""

            currency = "EUR"

            def total(self) ...

            def empty(self) ...
        '''
    ).strip("\n")
    assert not chunk.truncated


def test_method_chunk_has_full_code_and_its_class_in_the_header() -> None:
    code = "def total(self):\n    return sum(self.items)"

    chunk = build_chunk(
        ChunkSource(TOTAL, "shop.py", code, parent=ORDER, children=()), max_tokens=1024
    )

    assert chunk.text.splitlines() == [
        f"# {CHUNK_FORMAT} path: shop.py",
        "# symbol: shop.Order.total (method)",
        "# in class: class Order(Entity)",
        "def total(self):",
        "    return sum(self.items)",
    ]


def test_top_level_function_has_no_parent_line() -> None:
    node = NodeInfo(NodeKind.FUNCTION, "f", "m.f", "def f()", 1, 2)
    module = NodeInfo(NodeKind.MODULE, "m", "m", None, 1, 2)

    chunk = build_chunk(
        ChunkSource(node, "m.py", "def f():\n    pass", parent=module, children=()), max_tokens=1024
    )

    assert "# in " not in chunk.text


def test_long_code_is_cut_at_a_line_and_marked() -> None:
    node = NodeInfo(NodeKind.FUNCTION, "big", "m.big", "def big()", 1, 500)
    code = "def big():\n" + "\n".join(f"    x{i} = {i}" for i in range(500))

    chunk = build_chunk(ChunkSource(node, "m.py", code, parent=None, children=()), max_tokens=200)

    assert chunk.truncated
    assert chunk.text.endswith("\n" + TRUNCATION_MARKER)
    assert chunk.tokens <= 200
    body_lines = chunk.text.splitlines()[2:-1]
    assert body_lines[0] == "def big():"
    assert all(line.startswith("    x") for line in body_lines[1:])  # whole lines only


def test_skeleton_uses_the_name_when_a_child_has_no_signature() -> None:
    child = NodeInfo(NodeKind.FUNCTION, "g", "m.g", None, 2, 3)

    assert skeleton("x = 1\ndef g():\n    pass\ny = 2", 1, (child,)) == "x = 1\ng ...\ny = 2"


def test_estimate_tokens_is_conservative_for_code() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("x" * 350) == 100
