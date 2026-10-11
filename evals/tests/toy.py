"""A toy repo (Python + TypeScript) with exactly one obvious site per operator."""

import subprocess
from pathlib import Path
from textwrap import dedent

from evals.generate_seeded import (
    assign_splits,
    build_case,
    find_candidates,
    select_clean,
    select_per_operator,
    write_dataset,
)
from evals.graph import CodeGraph, Target
from evals.operators import CLEAN, COUNTERFACTUAL, SEEDED
from evals.sources import Source, checkout

FILES = {
    "pkg/__init__.py": "",
    "pkg/store.py": '''
        class NotFound(Exception):
            pass


        def find(items, key, strict=True):
            """Return the item with this key."""
            for item in items:
                if item.key == key:
                    return item
            raise NotFound(key)


        def label(prefix, name):
            return prefix + ":" + name


        def save(db, name, value):
            db.execute("insert into t (name, value) values (?, ?)", [name, value])


        async def fetch(db, key):
            return await db.get(key)


        async def load(db, key):
            row = await fetch(db, key)
            return row


        def check(value):
            if value < 0:
                raise ValueError(value)


        def render(
            template,
            context,
            escape=True,
        ):
            """Fill the template."""
            return template.format(**context)


        def _slug(text, sep="-", lower=True):
            out = text.replace(" ", sep)
            return out.lower() if lower else out


        def _span(start, end):
            return end - start


        def _lookup(table, key):
            if key not in table:
                raise KeyError(key)
            return table[key]
        ''',
    "pkg/api.py": """
        from pkg.store import NotFound, _lookup, _slug, _span, check, find, label, render


        def show(items, key):
            try:
                item = find(items, key, False)
            except NotFound:
                return None
            return label("item", item.name)


        def first_name(items, key):
            item = find(items, key)
            if item is None:
                return ""
            return item.name


        def describe(items, key):
            value = first_name(items, key)
            check(len(key))
            return label(value, key)


        def page(context):
            return render("<p>{body}</p>", context, escape=False)


        def help_text(item):
            help_ = item.help or "_"
            return help_.strip()


        def slug_page(title):
            return _slug(title, "_")


        def width(box):
            return _span(box.left, box.right)


        def safe_get(table, key):
            try:
                return _lookup(table, key)
            except KeyError:
                return None
        """,
    "src/store.ts": """
        export class NotFound extends Error {}

        export function find(items: Item[], key: string, strict = true): Item {
          for (const item of items) {
            if (item.key === key) return item;
          }
          throw new NotFound(key);
        }

        export function label(prefix: string, name: string): string {
          return `${prefix}:${name}`;
        }

        export async function fetchRow(db: Db, key: string): Promise<Row> {
          return db.get(key);
        }

        export async function loadRow(db: Db, key: string): Promise<Row> {
          const row = await fetchRow(db, key);
          return row;
        }
        """,
    "src/api.ts": """
        import { NotFound, find, label } from "./store";

        export function show(items: Item[], key: string): string | null {
          try {
            const item = find(items, key, false);
            return label("item", item.name);
          } catch (error) {
            if (error instanceof NotFound) return null;
            throw error;
          }
        }

        export function firstName(items: Item[], key: string): string {
          const item = find(items, key);
          if (!item) return "";
          return item.name;
        }
        """,
    "tests/test_store.py": """
        from pkg.store import find


        def test_find():
            assert find([], "k", True) is None
        """,
}


def write_toy_repo(root: Path) -> Path:
    for path, code in FILES.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(code).lstrip("\n"), encoding="utf-8", newline="\n")
    return root


def get_target(graph: CodeGraph, qualified_name: str) -> Target:
    [target] = [
        t for t in graph.targets(["pkg/", "src/"]) if t.node.qualified_name == qualified_name
    ]
    return target


def make_toy_source(root: Path) -> Source:
    """The toy repo committed to a git repo at `root`, as a pinned Source."""
    write_toy_repo(root)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "toy")
    return Source(
        name="toy",
        url=root.as_uri(),
        sha=git(root, "rev-parse", "HEAD"),
        license="MIT",
        language="python",
        include=["pkg/", "src/"],
    )


def generate(source: Source, checkouts: Path, out: Path) -> None:
    """A small dataset (2 cases per operator, counterfactuals included; 4 clean)."""
    root = checkout(source, checkouts)
    graph = CodeGraph.from_path(root)
    seeded = {source.name: find_candidates(source, root, graph, SEEDED, "seeded")}
    counterfactual = {
        source.name: find_candidates(source, root, graph, COUNTERFACTUAL, "counterfactual")
    }
    clean = {source.name: find_candidates(source, root, graph, CLEAN, "clean")}
    cases = select_per_operator(seeded, SEEDED, 2, build_case)
    cases += select_per_operator(counterfactual, COUNTERFACTUAL, 2, build_case)
    cases += select_clean(clean, 4, build_case)
    write_dataset(out, assign_splits(cases, 0.3))


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout.strip()
