import pytest

from core.parser.modules import js_module_name, python_module_name, python_package_dirs

PACKAGE_DIRS = python_package_dirs(
    [
        "pkg/__init__.py",
        "pkg/sub/__init__.py",
        "packages/core/src/core/__init__.py",
        "packages/core/src/core/db/__init__.py",
    ]
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("pkg/sub/mod.py", ("pkg.sub.mod", False)),
        ("pkg/sub/__init__.py", ("pkg.sub", True)),
        ("pkg/__init__.py", ("pkg", True)),
        # src layout: named from the top-most package, the way it is imported
        ("packages/core/src/core/db/base.py", ("core.db.base", False)),
        # outside any package: path-based
        ("scripts/run.py", ("scripts.run", False)),
        ("setup.py", ("setup", False)),
    ],
)
def test_python_module_name(path: str, expected: tuple[str, bool]) -> None:
    assert python_module_name(path, PACKAGE_DIRS) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/utils/date.ts", "src/utils/date"),
        ("src/utils/index.tsx", "src/utils/index"),
        ("next.config.mjs", "next.config"),
    ],
)
def test_js_module_name(path: str, expected: str) -> None:
    assert js_module_name(path) == expected
