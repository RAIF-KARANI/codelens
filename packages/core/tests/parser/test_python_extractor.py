from textwrap import dedent

from core.enums import EdgeKind, NodeKind
from core.parser.extraction import Extraction
from core.parser.python import extract_python, string_literal_value
from core.parser.types import Binding, Import, NodeRef


def extract(source: str, module: str = "pkg.mod", *, is_package: bool = False) -> Extraction:
    code = dedent(source).lstrip("\n").encode()
    return extract_python(code, module_name=module, is_package=is_package)


def refs(extraction: Extraction) -> set[tuple[str, EdgeKind, str]]:
    return {(str(r.source), r.kind, r.target) for r in extraction.references}


SAMPLE = '''
"""Module doc."""
import os

def top(a, b=1):
    """Top doc."""
    return a

class Service(Base):
    """Svc."""

    def run(self):
        def helper():
            pass
        return helper()

    @property
    def value(self):
        return 1

    @value.setter
    def value(self, v):
        pass
'''


def test_nodes_kinds_qualified_names_and_line_ranges() -> None:
    nodes = extract(SAMPLE).nodes

    assert [(n.kind, n.ref, n.start_line, n.end_line) for n in nodes] == [
        (NodeKind.MODULE, NodeRef("pkg.mod"), 1, 22),
        (NodeKind.FUNCTION, NodeRef("pkg.mod.top"), 4, 6),
        (NodeKind.CLASS, NodeRef("pkg.mod.Service"), 8, 22),
        (NodeKind.METHOD, NodeRef("pkg.mod.Service.run"), 11, 14),
        (NodeKind.FUNCTION, NodeRef("pkg.mod.Service.run.<locals>.helper"), 12, 13),
        # getter and setter share a name: told apart by ordinal
        (NodeKind.METHOD, NodeRef("pkg.mod.Service.value", 0), 16, 18),
        (NodeKind.METHOD, NodeRef("pkg.mod.Service.value", 1), 20, 22),
    ]
    assert [str(n.parent) for n in nodes[1:]] == [
        "pkg.mod",
        "pkg.mod",
        "pkg.mod.Service",
        "pkg.mod.Service.run",
        "pkg.mod.Service",
        "pkg.mod.Service",
    ]


def test_signatures_docstrings_and_code() -> None:
    nodes = {str(n.ref): n for n in extract(SAMPLE).nodes}

    assert nodes["pkg.mod"].docstring == "Module doc."
    assert nodes["pkg.mod.top"].signature == "def top(a, b=1)"
    assert nodes["pkg.mod.top"].docstring == "Top doc."
    assert nodes["pkg.mod.Service"].signature == "class Service(Base)"
    assert nodes["pkg.mod.Service"].docstring == "Svc."
    assert nodes["pkg.mod.Service.run"].docstring is None
    # Decorated nodes include their decorators, and nested code is dedented:
    # a method reads like top-level code, body one level in.
    assert nodes["pkg.mod.Service.value"].code == "@property\ndef value(self):\n    return 1"
    assert nodes["pkg.mod.Service.value#1"].code.startswith("@value.setter")
    assert nodes["pkg.mod.Service.run"].code.splitlines()[1:3] == [
        "    def helper():",
        "        pass",
    ]
    # same code -> same hash; it is what lets the indexer skip unchanged nodes
    assert nodes["pkg.mod.top"].content_hash == extract(SAMPLE).nodes[1].content_hash


def test_multiline_async_signature_is_normalized() -> None:
    [_, fetch] = extract(
        """
        async def fetch(
            url: str,
            *,
            timeout: float = 1.0,
        ) -> bytes:
            ...
        """
    ).nodes

    assert fetch.signature == "async def fetch(url: str, *, timeout: float = 1.0,) -> bytes"


def test_calls_and_bases_are_recorded_as_written() -> None:
    extraction = extract(
        """
        @app.route("/x")
        def handler(req):
            helpers.clean(req)
            helpers.clean(req)
            req.json()
            len(req)
            make()()
            items[0]()
            run = lambda: inner_call()

        class Child(Base, mixins.Mixin, Generic[T], metaclass=Meta):
            def save(self):
                super().save()
                self.validate()
        """
    )

    assert refs(extraction) == {
        # decorator calls belong to the decorated function
        ("pkg.mod.handler", EdgeKind.CALLS, "app.route"),
        # deduplicated: two calls, one reference
        ("pkg.mod.handler", EdgeKind.CALLS, "helpers.clean"),
        ("pkg.mod.handler", EdgeKind.CALLS, "req.json"),
        ("pkg.mod.handler", EdgeKind.CALLS, "len"),  # builtins are dropped later, by the resolver
        ("pkg.mod.handler", EdgeKind.CALLS, "make"),  # make()() keeps only the nameable inner call
        ("pkg.mod.handler", EdgeKind.CALLS, "inner_call"),  # lambdas belong to the enclosing def
        ("pkg.mod.Child", EdgeKind.INHERITS, "Base"),
        ("pkg.mod.Child", EdgeKind.INHERITS, "mixins.Mixin"),
        ("pkg.mod.Child", EdgeKind.INHERITS, "Generic"),
        ("pkg.mod.Child.save", EdgeKind.CALLS, "super().save"),
        ("pkg.mod.Child.save", EdgeKind.CALLS, "self.validate"),
    }


def test_imports_and_bindings_resolve_relative_modules() -> None:
    extraction = extract(
        """
        import os.path
        import numpy as np
        from . import sibling
        from ..core import util as u, other
        from .helpers import *
        """,
        module="pkg.sub.mod",
    )

    assert [(i.module, i.name) for i in extraction.imports] == [
        ("os.path", None),
        ("numpy", None),
        ("pkg.sub", "sibling"),
        ("pkg.core", "util"),
        ("pkg.core", "other"),
        ("pkg.sub.helpers", "*"),
    ]
    assert list(extraction.bindings) == [
        Binding("os", "os", None),  # `import os.path` binds only `os`
        Binding("np", "numpy", None),
        Binding("sibling", "pkg.sub", "sibling"),
        Binding("u", "pkg.core", "util"),
        Binding("other", "pkg.core", "other"),
        Binding("*", "pkg.sub.helpers", None),
    ]
    assert extraction.imports[0] == Import("os.path", None, 1)


def test_relative_imports_inside_a_package_init() -> None:
    extraction = extract(
        """
        from . import a
        from .. import b
        """,
        module="pkg.sub",
        is_package=True,
    )

    assert [(i.module, i.name) for i in extraction.imports] == [("pkg.sub", "a"), ("pkg", "b")]


def test_syntax_errors_are_counted_but_parsing_continues() -> None:
    extraction = extract(
        """
        def broken(:
            pass

        def ok():
            pass
        """
    )

    assert extraction.syntax_errors >= 1
    assert "pkg.mod.ok" in {n.qualified_name for n in extraction.nodes}


def test_byte_offsets_survive_non_ascii_text() -> None:
    nodes = extract('def f():\n    return "héllo wörld"\n\ndef g(): pass\n').nodes

    assert nodes[2].code == "def g(): pass"


def test_deeply_nested_code_does_not_hit_the_recursion_limit() -> None:
    source = "x = " + " + ".join(["1"] * 5000) + "\n\ndef after():\n    pass\n"

    nodes = extract(source).nodes

    assert nodes[-1].qualified_name == "pkg.mod.after"


def test_string_literal_value() -> None:
    assert string_literal_value('"""  Doc.\n\n    More.\n    """') == "Doc.\n\nMore."
    assert string_literal_value("r'raw'") == "raw"
    assert string_literal_value('""') is None
