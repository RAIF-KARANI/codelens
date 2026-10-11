"""One eval case on disk: evals/datasets/<kind>/<id>/{case.yaml, patch.diff}.

case.yaml says what the patch does and what a good review finds; patch.diff
applies with `git apply` on a checkout of the source at `sha`.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from core.enums import Language, Severity
from core.review.schemas import Category

Kind = Literal["seeded", "clean"]
Split = Literal["dev", "test"]


class ExpectedFinding(BaseModel):
    file: str
    line_start: int  # new side of the diff
    line_end: int
    category: Category
    severity: Severity


class OracleNode(BaseModel):
    """A node the reviewer needs in context to see the bug."""

    qualified_name: str
    ordinal: int = 0
    role: Literal["caller", "callee"]


class Case(BaseModel):
    id: str
    kind: Kind
    split: Split
    source: str
    sha: str
    language: Language
    operator: str
    file: str
    function: str  # qualified name (with #ordinal when > 0) of the mutated node
    description: str
    expected: list[ExpectedFinding]  # empty for clean cases
    oracle: list[OracleNode]


def write_case(root: Path, case: Case, patch: str) -> Path:
    directory = root / case.kind / case.id
    directory.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    (directory / "case.yaml").write_text(data, encoding="utf-8", newline="\n")
    # Bytes as generated: .gitattributes keeps git from normalising these.
    (directory / "patch.diff").write_bytes(patch.encode("utf-8"))
    return directory


def load_cases(root: Path, kind: Kind | None = None) -> list[tuple[Case, str]]:
    """Every case under root (one kind, or both), sorted by id."""
    found = []
    for kind_dir in [root / kind] if kind else [root / "seeded", root / "clean"]:
        for path in sorted(kind_dir.glob("*/case.yaml")):
            case = Case.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            patch = (path.parent / "patch.diff").read_bytes().decode("utf-8")
            found.append((case, patch))
    return sorted(found, key=lambda item: item[0].id)
