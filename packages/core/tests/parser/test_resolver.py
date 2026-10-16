"""Resolution across files, exercised through parse_repository on tiny repos."""

from pathlib import Path
from textwrap import dedent

import pytest

from core.enums import EdgeKind
from core.parser import ParseResult, parse_repository, summarize
from core.parser.resolver import _Resolver
from core.parser.types import ParsedEdge


def make_repo(root: Path, files: dict[str, str]) -> ParseResult:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return parse_repository(root)


def edges_from(result: ParseResult, source: str, kind: EdgeKind) -> dict[str, ParsedEdge]:
    return {e.target_name: e for e in result.edges if str(e.source) == source and e.kind is kind}


def resolved(edge: ParsedEdge) -> str | None:
    return str(edge.target) if edge.target else None


PYTHON_REPO = {
    "pkg/__init__.py": "from .utils import helper\n",
    "pkg/utils.py": """
        def helper():
            return _private()

        def _private():
            return 1
        """,
    "pkg/models.py": """
        class Base:
            def save(self):
                return self.validate()

            def validate(self):
                return True

        class User(Base):
            def save(self):
                super().save()
                return self.validate()
        """,
    "pkg/service.py": """
        import os
        import pkg.utils
        from pkg import helper
        from pkg.models import User as U
        from . import models

        def run():
            helper()
            pkg.utils.helper()
            u = U()
            models.User.save(u)
            os.path.join("a", "b")
            print("x")
            undefined_thing()

            def nested():
                return run()

            return nested()
        """,
    "pkg/star.py": """
        from pkg.utils import *

        def go():
            return helper()
        """,
}


def test_python_call_resolution(tmp_path: Path) -> None:
    result = make_repo(tmp_path, PYTHON_REPO)
    calls = edges_from(result, "pkg.service.run", EdgeKind.CALLS)

    assert {name: resolved(edge) for name, edge in calls.items()} == {
        # helper() via `from pkg import helper`, re-exported by pkg/__init__.py.
        # pkg.utils.helper() resolves to the same node, so it is one edge.
        "pkg.utils.helper": "pkg.utils.helper",
        "pkg.models.User": "pkg.models.User",  # U() -> the class (constructor)
        "pkg.models.User.save": "pkg.models.User.save",  # models.User.save via `from . import`
        "os.path.join": None,  # external
        "undefined_thing": None,  # unknown name, kept as written
        "pkg.service.run.<locals>.nested": "pkg.service.run.<locals>.nested",
        # print() is a builtin: no edge at all
    }
    assert calls["os.path.join"].external is True
    assert calls["undefined_thing"].external is False

    nested = edges_from(result, "pkg.service.run.<locals>.nested", EdgeKind.CALLS)
    assert resolved(nested["pkg.service.run"]) == "pkg.service.run"
    star = edges_from(result, "pkg.star.go", EdgeKind.CALLS)
    assert resolved(star["pkg.utils.helper"]) == "pkg.utils.helper"


def test_python_self_super_and_inherited_methods(tmp_path: Path) -> None:
    result = make_repo(tmp_path, PYTHON_REPO)

    assert {
        n: resolved(e)
        for n, e in edges_from(result, "pkg.models.User.save", EdgeKind.CALLS).items()
    } == {
        "pkg.models.Base.save": "pkg.models.Base.save",  # super().save()
        "pkg.models.Base.validate": "pkg.models.Base.validate",  # self.validate(), inherited
    }
    base_save = edges_from(result, "pkg.models.Base.save", EdgeKind.CALLS)
    assert resolved(base_save["pkg.models.Base.validate"]) == "pkg.models.Base.validate"
    inherits = edges_from(result, "pkg.models.User", EdgeKind.INHERITS)
    assert resolved(inherits["pkg.models.Base"]) == "pkg.models.Base"


def test_python_imports_and_contains_edges(tmp_path: Path) -> None:
    result = make_repo(tmp_path, PYTHON_REPO)
    imports = edges_from(result, "pkg.service", EdgeKind.IMPORTS)

    assert {n: (resolved(e), e.external) for n, e in imports.items()} == {
        "os": (None, True),
        "pkg.utils": ("pkg.utils", False),
        "pkg.utils.helper": ("pkg.utils.helper", False),  # from pkg import helper
        "pkg.models.User": ("pkg.models.User", False),
        "pkg.models": ("pkg.models", False),
    }
    contains = edges_from(result, "pkg.models.User", EdgeKind.CONTAINS)
    assert resolved(contains["pkg.models.User.save"]) == "pkg.models.User.save"


JS_REPO = {
    "src/lib/math.ts": """
        export function add(a: number, b: number) { return a + b; }
        export default function multiply(a, b) { return a * b; }
        """,
    "src/lib/index.ts": """
        export * from './math';
        export { default as mul } from './math';
        """,
    "src/base.ts": """
        export class Base {
          save() { return this.validate(); }
          validate() { return true; }
        }
        """,
    "src/app.tsx": """
        import { add, mul } from './lib';
        import * as math from './lib/math';
        import React from 'react';
        import { Base } from './base';
        const { readFile } = require('fs');

        export class Repo extends Base {
          save() { super.save(); return this.validate(); }
        }

        export const App = () => {
          add(1, 2);
          mul(2, 3);
          math.add(1, 1);
          React.useState(0);
          readFile('x');
          console.log('hi');
          new Repo();
          return null;
        };
        """,
    "src/esm.ts": """
        import { add } from './lib/math.js';
        export const twice = (x) => add(x, x);
        """,
}


def test_js_resolution_through_barrels_defaults_and_namespaces(tmp_path: Path) -> None:
    result = make_repo(tmp_path, JS_REPO)
    calls = edges_from(result, "src/app:App", EdgeKind.CALLS)

    assert {n: (resolved(e), e.external) for n, e in calls.items()} == {
        "src/lib/math:add": ("src/lib/math:add", False),  # via `export *` barrel; math.add too
        "src/lib/math:multiply": ("src/lib/math:multiply", False),  # mul -> default -> multiply
        "react:useState": (None, True),
        "fs:readFile": (None, True),
        "src/app:Repo": ("src/app:Repo", False),  # new Repo()
        # console.log: JS global, no edge
    }
    esm = edges_from(result, "src/esm:twice", EdgeKind.CALLS)
    assert resolved(esm["src/lib/math:add"]) == "src/lib/math:add"  # './x.js' -> x.ts


def test_js_classes_super_and_this(tmp_path: Path) -> None:
    result = make_repo(tmp_path, JS_REPO)

    assert (
        resolved(edges_from(result, "src/app:Repo", EdgeKind.INHERITS)["src/base:Base"])
        == "src/base:Base"
    )
    assert {
        n: resolved(e) for n, e in edges_from(result, "src/app:Repo.save", EdgeKind.CALLS).items()
    } == {
        "src/base:Base.save": "src/base:Base.save",
        "src/base:Base.validate": "src/base:Base.validate",
    }


def test_js_import_edges(tmp_path: Path) -> None:
    result = make_repo(tmp_path, JS_REPO)
    imports = edges_from(result, "src/app", EdgeKind.IMPORTS)

    assert {n: (resolved(e), e.external) for n, e in imports.items()} == {
        "src/lib/math:add": ("src/lib/math:add", False),
        "src/lib/math:multiply": ("src/lib/math:multiply", False),
        "src/lib/math": ("src/lib/math", False),
        "react:default": (None, True),
        "src/base:Base": ("src/base:Base", False),
        "fs:readFile": (None, True),  # const { readFile } = require('fs')
    }


def test_summary_counts_resolution(tmp_path: Path) -> None:
    stats = summarize(make_repo(tmp_path, PYTHON_REPO))

    assert stats.files == 5
    assert stats.nodes_by_kind == {"module": 5, "function": 5, "class": 2, "method": 3}
    assert stats.calls_external == 1
    assert 0 < stats.call_resolution < stats.internal_call_resolution <= 1


# --- traversal cost (Phase 9 step 5, D131): each class / export followed once


def lattice(layers: int, method: str) -> str:
    """X_i and Y_i both inherit X_{i-1} and Y_{i-1}: 2^layers paths to the top."""
    lines = ["class X0:\n    def found(self):\n        return 1\n", "class Y0:\n    pass\n"]
    for i in range(1, layers + 1):
        lines += [f"class X{i}(X{i - 1}, Y{i - 1}):\n    pass\n"]
        lines += [f"class Y{i}(X{i - 1}, Y{i - 1}):\n    pass\n"]
    lines += [f"class Z(X{layers}, Y{layers}):\n    def m(self):\n        return self.{method}()\n"]
    return "\n".join(lines)


def counting(monkeypatch: pytest.MonkeyPatch, method: str) -> list[int]:
    calls = [0]
    original = getattr(_Resolver, method)

    def wrapper(self: _Resolver, *args: object, **kwargs: object) -> object:
        calls[0] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_Resolver, method, wrapper)
    return calls


def test_a_diamond_lattice_costs_each_class_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Following every path cost 2^(n+2) lookups per unresolved `self.x`: ~1 KB
    # of classes held a shared index worker for hours.
    calls = counting(monkeypatch, "_target")
    result = make_repo(
        tmp_path, {"lost.py": lattice(20, "missing"), "kept.py": lattice(20, "found")}
    )

    assert calls[0] < 4_000  # was over 4 million
    # Behaviour is unchanged: the deep inherited method still resolves, and
    # the missing one is still named after the class it was looked up on.
    called = {
        str(e.source): (e.target_name, resolved(e))
        for e in result.edges
        if e.kind is EdgeKind.CALLS
    }
    assert called["kept.Z.m"] == ("kept.X0.found", "kept.X0.found")
    assert called["lost.Z.m"] == ("lost.Z.missing", None)


def test_repeated_export_star_costs_each_module_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # k copies of `export * from "./a"` cost k^9 lookups: 165 bytes took 10 s.
    calls = counting(monkeypatch, "_export")
    barrel = "export * from './a';\n" * 4
    make_repo(
        tmp_path,
        {"a.ts": barrel + "import { nope } from './a';\nexport function f() { nope(); }\n"},
    )

    assert calls[0] < 50  # was 699,050 for 4 copies
