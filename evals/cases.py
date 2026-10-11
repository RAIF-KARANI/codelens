"""One eval case on disk: evals/datasets/<kind>/<id>/{case.yaml, patch.diff[, prep.diff]}.

case.yaml says what the patch does and what a good review finds; patch.diff
applies with `git apply` on a checkout of the source at `sha`. A counterfactual
case also has prep.diff: a commit applied first, which the case is based on
(and which the stored graph must reflect).
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from core.enums import Language, Severity
from core.review.schemas import Category

# seeded: a planted bug. clean: a behaviour-preserving edit. counterfactual:
# a seeded bug's exact diff, made safe by its context (planning Q10).
Kind = Literal["seeded", "clean", "counterfactual"]
KINDS: tuple[Kind, ...] = ("seeded", "clean", "counterfactual")
Split = Literal["dev", "test"]


class ExpectedFinding(BaseModel):
    file: str
    line_start: int  # new side of the diff
    line_end: int
    category: Category
    severity: Severity


class OracleNode(BaseModel):
    """A node the reviewer needs in context: to see the bug (seeded), or to see
    that there is none (counterfactual)."""

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
    expected: list[ExpectedFinding]  # empty for clean and counterfactual cases
    oracle: list[OracleNode]
    # Counterfactuals: the prep commit's diff. Stored as prep.diff, not in the YAML.
    prep: str | None = Field(default=None, exclude=True)


def write_case(root: Path, case: Case, patch: str) -> Path:
    directory = root / case.kind / case.id
    directory.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump(case.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    (directory / "case.yaml").write_text(data, encoding="utf-8", newline="\n")
    # Bytes as generated: .gitattributes keeps git from normalising these.
    (directory / "patch.diff").write_bytes(patch.encode("utf-8"))
    if case.prep is not None:
        (directory / "prep.diff").write_bytes(case.prep.encode("utf-8"))
    return directory


def load_cases(root: Path, kind: Kind | None = None) -> list[tuple[Case, str]]:
    """Every case under root (one kind, or all), sorted by id."""
    found = []
    for kind_dir in [root / k for k in ([kind] if kind else KINDS)]:
        for path in sorted(kind_dir.glob("*/case.yaml")):
            case = Case.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            prep = path.parent / "prep.diff"
            if prep.exists():
                case.prep = prep.read_bytes().decode("utf-8")
            patch = (path.parent / "patch.diff").read_bytes().decode("utf-8")
            found.append((case, patch))
    return sorted(found, key=lambda item: item[0].id)
