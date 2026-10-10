"""Which files we parse, and with which tree-sitter grammar."""

from functools import cache
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language as Grammar
from tree_sitter import Parser

from core.enums import Language

# The JavaScript grammar also covers JSX. TypeScript ships two grammars because
# `<T>x` means a type cast in .ts but a JSX element in .tsx.
_EXTENSIONS: dict[str, tuple[Language, str]] = {
    ".py": (Language.PYTHON, "python"),
    ".js": (Language.JAVASCRIPT, "javascript"),
    ".jsx": (Language.JAVASCRIPT, "javascript"),
    ".mjs": (Language.JAVASCRIPT, "javascript"),
    ".cjs": (Language.JAVASCRIPT, "javascript"),
    ".ts": (Language.TYPESCRIPT, "typescript"),
    ".tsx": (Language.TYPESCRIPT, "tsx"),
}

JS_TS_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def detect(path: str) -> tuple[Language, str] | None:
    """(language, grammar name) for a path, or None if we don't parse it."""
    return _EXTENSIONS.get(PurePosixPath(path).suffix.lower())


@cache
def get_parser(grammar: str) -> Parser:
    # Parsers are reusable and cheap to keep; building one per file would reload
    # the grammar each time. Not thread-safe; the parser runs single-threaded.
    loaders = {
        "python": tree_sitter_python.language,
        "javascript": tree_sitter_javascript.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
    }
    return Parser(Grammar(loaders[grammar]()))
