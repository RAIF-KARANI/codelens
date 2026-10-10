from textwrap import dedent

from core.enums import EdgeKind, NodeKind
from core.parser.extraction import Extraction
from core.parser.javascript import extract_javascript
from core.parser.types import Binding, ReExport


def extract(source: str, module: str = "src/app", grammar: str = "typescript") -> Extraction:
    code = dedent(source).lstrip("\n").encode()
    return extract_javascript(code, module_name=module, grammar=grammar)


def refs(extraction: Extraction) -> set[tuple[str, EdgeKind, str]]:
    return {(str(r.source), r.kind, r.target) for r in extraction.references}


SAMPLE = """
import Def, { a as b, c } from './m';
import * as ns from "../lib";
import './side-effect';
const fs = require('fs');
const { join, resolve: res } = require('./path');
export * from './re';
export { x as y } from './re2';
export { local as alias };

/** Adds numbers. */
export async function add(p: number): Promise<number> {
  return helper(p) + ns.f();
}

export const arrow = async (q) => { this.go(); };

export default class Svc extends Base implements I {
  private field = (z) => z;
  constructor() { super(); new Other(); }
  async run(): Promise<void> { super.run(); this.field?.(1); console.log(1); }
  abstract stop(): void;
}

function overload(a: string): void;
const obj = { m() { inner(); } };
"""


def test_nodes_from_declarations_arrows_classes_and_fields() -> None:
    nodes = extract(SAMPLE).nodes

    # Not nodes: overload signatures, abstract signatures, object-literal methods.
    assert [(n.kind, str(n.ref)) for n in nodes] == [
        (NodeKind.MODULE, "src/app"),
        (NodeKind.FUNCTION, "src/app:add"),
        (NodeKind.FUNCTION, "src/app:arrow"),
        (NodeKind.CLASS, "src/app:Svc"),
        (NodeKind.METHOD, "src/app:Svc.field"),
        (NodeKind.METHOD, "src/app:Svc.constructor"),
        (NodeKind.METHOD, "src/app:Svc.run"),
    ]


def test_signatures_jsdoc_and_export_ranges() -> None:
    nodes = {str(n.ref): n for n in extract(SAMPLE).nodes}

    add = nodes["src/app:add"]
    assert add.signature == "export async function add(p: number): Promise<number>"
    assert add.docstring == "Adds numbers."
    assert add.code.startswith("export async function add")
    assert (add.start_line, add.end_line) == (11, 13)
    assert nodes["src/app:arrow"].signature == "export const arrow = async (q) =>"
    assert nodes["src/app:Svc"].signature == "export default class Svc extends Base implements I"
    assert nodes["src/app:Svc.run"].signature == "async run(): Promise<void>"


def test_calls_new_super_and_this_are_recorded() -> None:
    assert refs(extract(SAMPLE)) == {
        ("src/app:add", EdgeKind.CALLS, "helper"),
        ("src/app:add", EdgeKind.CALLS, "ns.f"),
        ("src/app:arrow", EdgeKind.CALLS, "this.go"),
        ("src/app:Svc", EdgeKind.INHERITS, "Base"),  # `implements` is not inheritance
        ("src/app:Svc.constructor", EdgeKind.CALLS, "super"),
        ("src/app:Svc.constructor", EdgeKind.CALLS, "Other"),  # new Other()
        ("src/app:Svc.run", EdgeKind.CALLS, "super.run"),
        ("src/app:Svc.run", EdgeKind.CALLS, "this.field"),  # optional call this.field?.()
        ("src/app:Svc.run", EdgeKind.CALLS, "console.log"),
        ("src/app", EdgeKind.CALLS, "inner"),  # object-literal method -> enclosing scope
    }


def test_imports_requires_and_reexports() -> None:
    extraction = extract(SAMPLE)

    assert [(i.module, i.name) for i in extraction.imports] == [
        ("./m", "default"),
        ("./m", "a"),
        ("./m", "c"),
        ("../lib", None),
        ("./side-effect", None),
        ("fs", None),
        ("./path", "join"),
        ("./path", "resolve"),
        ("./re", None),
        ("./re2", "x"),
    ]
    assert list(extraction.bindings) == [
        Binding("Def", "./m", "default"),
        Binding("b", "./m", "a"),
        Binding("c", "./m", "c"),
        Binding("ns", "../lib", None),
        Binding("fs", "fs", None),
        Binding("join", "./path", "join"),
        Binding("res", "./path", "resolve"),
    ]
    assert list(extraction.reexports) == [
        ReExport("*", "./re", None),
        ReExport("y", "./re2", "x"),
        ReExport("alias", None, "local"),
        ReExport("default", None, "Svc"),
    ]


def test_anonymous_default_export_and_jsx() -> None:
    extraction = extract(
        """
        export default function () { run(); }
        const App = () => <div onClick={() => handle()} />;
        """,
        module="src/view",
        grammar="javascript",
    )

    nodes = {str(n.ref): n for n in extraction.nodes}
    assert nodes["src/view:default"].signature == "export default function ()"
    assert nodes["src/view:App"].kind is NodeKind.FUNCTION
    assert refs(extraction) == {
        ("src/view:default", EdgeKind.CALLS, "run"),
        ("src/view:App", EdgeKind.CALLS, "handle"),  # callback inside JSX -> App
    }


def test_jsdoc_separated_by_a_blank_line_is_not_attached() -> None:
    nodes = extract(
        """
        /** Stray comment. */

        function f() {}
        """
    ).nodes

    assert nodes[1].docstring is None


def test_methods_inside_nested_functions_get_locals_names() -> None:
    nodes = extract(
        """
        function outer() {
          const inner = () => 1;
          class Local { m() {} }
        }
        """
    ).nodes

    assert [str(n.ref) for n in nodes[1:]] == [
        "src/app:outer",
        "src/app:outer.<locals>.inner",
        "src/app:outer.<locals>.Local",
        "src/app:outer.<locals>.Local.m",
    ]
