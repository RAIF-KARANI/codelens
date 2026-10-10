import os
from pathlib import Path

import pytest

from core.enums import Language
from core.parser.walker import MAX_FILE_BYTES, discover_sources, is_test_path


def write(root: Path, rel: str, content: str | bytes = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_discovery_applies_skip_rules(tmp_path: Path) -> None:
    write(tmp_path, ".gitignore", "ignored/\ngenerated.py\n")
    write(tmp_path, "src/app.py")
    write(tmp_path, "src/ui.tsx", "export const A = () => null;\n")
    write(tmp_path, "src/legacy.js", "function f() {}\n")
    write(tmp_path, "tests/test_app.py")
    write(tmp_path, "README.md", "# not code\n")
    # pruned directories and gitignored paths: silently absent
    write(tmp_path, "node_modules/lib/index.js")
    write(tmp_path, ".venv/lib/site.py")
    write(tmp_path, "build/out.js")
    write(tmp_path, "pkg.egg-info/x.py")
    write(tmp_path, "ignored/secret.py")
    write(tmp_path, "generated.py")
    # parseable extensions we refuse, with a reason
    write(tmp_path, "src/types.d.ts", "export type T = string;\n")
    write(tmp_path, "src/bundle.min.js", "var a=1;\n")
    write(tmp_path, "src/dense.js", "var x = 1;" * 400)  # one 4 KB line: minified
    write(tmp_path, "src/blob.py", b"\x00\x01binary")
    write(tmp_path, "src/huge.py", "#" * (MAX_FILE_BYTES + 1))

    sources, skipped = discover_sources(tmp_path)

    assert [(s.path, s.language, s.is_test) for s in sources] == [
        ("src/app.py", Language.PYTHON, False),
        ("src/legacy.js", Language.JAVASCRIPT, False),
        ("src/ui.tsx", Language.TYPESCRIPT, False),
        ("tests/test_app.py", Language.PYTHON, True),
    ]
    assert {(s.path, s.reason) for s in skipped} == {
        ("src/types.d.ts", "type_declarations"),
        ("src/bundle.min.js", "minified"),
        ("src/dense.js", "minified"),
        ("src/blob.py", "binary"),
        ("src/huge.py", "too_large"),
    }
    assert next(s for s in sources if s.path == "src/ui.tsx").grammar == "tsx"


def test_symlinks_are_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside, "secret.py")
    repo = tmp_path / "repo"
    write(repo, "app.py")
    try:
        os.symlink(outside / "secret.py", repo / "link.py")
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this OS")

    sources, skipped = discover_sources(repo)

    assert [s.path for s in sources] == ["app.py"]
    assert [(s.path, s.reason) for s in skipped] == [("link.py", "symlink")]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_x.py", True),
        ("pkg/test_x.py", True),
        ("pkg/x_test.py", True),
        ("src/__tests__/a.js", True),
        ("src/a.test.ts", True),
        ("src/a.spec.tsx", True),
        ("src/contest.py", False),
        ("src/latest.ts", False),
        ("testing_utils.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected
