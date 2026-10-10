"""Module names: how a file is addressed in qualified names and imports."""

from collections.abc import Iterable
from pathlib import PurePosixPath


def python_package_dirs(paths: Iterable[str]) -> frozenset[str]:
    """Directories that are regular Python packages (contain __init__.py)."""
    return frozenset(
        str(PurePosixPath(p).parent) for p in paths if PurePosixPath(p).name == "__init__.py"
    )


def python_module_name(path: str, package_dirs: frozenset[str]) -> tuple[str, bool]:
    """Dotted module name for a Python file, plus whether it is a package.

    Inside a package we climb to the top-most package directory, so
    `packages/core/src/core/db/base.py` becomes `core.db.base` (matching how it
    is imported) rather than a path-derived name. Files outside any package,
    like scripts or namespace-package tests, fall back to their repo path:
    `scripts/run.py` becomes `scripts.run`.
    """
    file = PurePosixPath(path)
    is_package = file.name == "__init__.py"
    directory = file.parent

    if str(directory) in package_dirs:
        top = directory
        while str(top.parent) in package_dirs and str(top.parent) != ".":
            top = top.parent
        relative = file.relative_to(top.parent) if str(top.parent) != "." else file
    else:
        relative = file

    parts = list(relative.with_suffix("").parts)
    if is_package:
        parts = parts[:-1]  # pkg/__init__.py -> "pkg"
    return ".".join(parts) or file.stem, is_package


def js_module_name(path: str) -> str:
    """JS/TS modules are addressed by path without extension:
    `src/utils/date.ts` -> `src/utils/date`. Members are joined with ':'
    (`src/utils/date:formatDate`) because paths may themselves contain dots."""
    return str(PurePosixPath(path).with_suffix(""))
