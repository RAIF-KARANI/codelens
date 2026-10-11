"""The generator end to end on the toy repo, checked out through git like a
real source: reproducible output, patches that apply, sound expectations."""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.diff import parse_unified_diff
from evals.cases import load_cases
from evals.generate_seeded import (
    assign_splits,
    build_case,
    find_candidates,
    select_clean,
    select_seeded,
    write_dataset,
)
from evals.graph import CodeGraph
from evals.operators import CLEAN, SEEDED
from evals.sources import Source, checkout, load_sources
from evals.tests.toy import write_toy_repo


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    upstream = write_toy_repo(tmp_path_factory.mktemp("upstream"))
    _git(upstream, "init", "-q")
    _git(upstream, "add", ".")
    _git(upstream, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "toy")
    return Source(
        name="toy",
        url=upstream.as_uri(),
        sha=_git(upstream, "rev-parse", "HEAD"),
        license="MIT",
        language="python",
        include=["pkg/", "src/"],
    )


def generate(source: Source, checkouts: Path, out: Path) -> None:
    root = checkout(source, checkouts)
    graph = CodeGraph.from_path(root)
    seeded = {source.name: find_candidates(source, root, graph, SEEDED, "seeded")}
    clean = {source.name: find_candidates(source, root, graph, CLEAN, "clean")}
    cases = select_seeded(seeded, 2, build_case) + select_clean(clean, 4, build_case)
    write_dataset(out, assign_splits(cases, 0.3))


def test_generation_is_reproducible_and_sound(source: Source, tmp_path: Path) -> None:
    generate(source, tmp_path / "checkouts", tmp_path / "a")
    generate(source, tmp_path / "checkouts", tmp_path / "b")  # reuses the checkout

    files_a = sorted(
        p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file()
    )
    files_b = sorted(
        p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*") if p.is_file()
    )
    assert files_a == files_b
    for rel in files_a:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel

    cases = load_cases(tmp_path / "a")
    by_operator = {}
    for case, _ in cases:
        by_operator.setdefault(case.operator, []).append(case)
    # Every operator has a site in the toy repo, in both languages where it applies.
    assert set(by_operator) == set(SEEDED) | set(CLEAN)
    assert {c.language for c in by_operator["raise_to_none"]} == {"python", "typescript"}

    checkout_dir = tmp_path / "checkouts" / "toy"
    for case, patch in cases:
        assert case.sha == source.sha and not case.file.startswith("tests/")
        done = subprocess.run(
            ["git", "apply", "--check", "-"], cwd=checkout_dir, input=patch.encode()
        )
        assert done.returncode == 0, case.id
        if case.kind == "clean":
            assert case.expected == [] and case.oracle == []
            continue
        [expected] = case.expected
        [diff] = parse_unified_diff(patch)
        touched = set(range(expected.line_start, expected.line_end + 1))
        if case.operator == "remove_guard":
            # A pure deletion adds no line; the expectation brackets the gap.
            assert not diff.added_lines() and expected.line_end - expected.line_start == 1
        else:
            assert touched & diff.added_lines(), case.id
        assert case.oracle or case.operator == "sql_format", case.id


def test_splits_are_stratified_by_operator(source: Source, tmp_path: Path) -> None:
    generate(source, tmp_path / "checkouts", tmp_path / "out")
    cases = [case for case, _ in load_cases(tmp_path / "out")]

    for operator in {c.operator for c in cases}:
        group = [c for c in cases if c.operator == operator]
        dev = sum(c.split == "dev" for c in group)
        assert dev == round(len(group) * 0.3), operator


def test_checkout_resets_local_changes(source: Source, tmp_path: Path) -> None:
    root = checkout(source, tmp_path)
    (root / "pkg" / "store.py").write_text("broken", encoding="utf-8")

    assert checkout(source, tmp_path) == root
    assert "def find" in (root / "pkg" / "store.py").read_text(encoding="utf-8")


def test_sources_file_is_valid_and_pinned() -> None:
    sources = load_sources(Path("evals/sources.yaml"))

    assert [s.name for s in sources] == ["sqlite-utils", "starlette", "click", "h3"]
    with pytest.raises(ValidationError):
        Source.model_validate({**sources[0].model_dump(), "sha": "main"})
