"""collect_changes end to end, against a real (throwaway) git repository."""

import subprocess
from pathlib import Path

import pytest

from core.diff import FileStatus
from core.diff.git import GitError
from core.review import ChangeKind, collect_changes


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def write(repo: Path, rel: str, content: str | bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    write(repo, "shop/__init__.py", "")
    write(
        repo,
        "shop/pricing.py",
        "def discount(a, p):\n    return a * (1 - p / 100)\n\n\ndef tax(a):\n    return a * 1.2\n",
    )
    write(repo, "shop/legacy.py", "def old():\n    return 1\n")
    write(
        repo,
        "shop/util.py",
        "def slugify(s):\n    return s.lower().replace(' ', '-')\n" + "# padding\n" * 20,
    )
    write(repo, "README.md", "# Shop\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    git(repo, "checkout", "-q", "-b", "feature")
    write(
        repo,
        "shop/pricing.py",
        "def discount(a, p):\n"
        "    return round(a * (1 - p / 100), 2)\n\n\n"
        "def tax(a):\n    return a * 1.2\n",
    )
    (repo / "shop/legacy.py").unlink()
    git(repo, "mv", "shop/util.py", "shop/text.py")
    text = (repo / "shop/text.py").read_text()
    write(repo, "shop/text.py", text.replace("s.lower()", "s.strip().lower()"))
    write(
        repo,
        "shop/cart.py",
        "from shop.pricing import tax\n\n\ndef total(items):\n    return tax(sum(items))\n",
    )
    write(repo, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00binary")
    write(repo, "README.md", "# Shop\n\nNow with carts.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature")

    # A later commit on main must NOT show up: PRs diff against the merge-base.
    git(repo, "checkout", "-q", "main")
    write(
        repo,
        "shop/pricing.py",
        "def discount(a, p):\n    return a * (1 - p / 100)\n\n\ndef tax(a):\n    return a * 1.25\n",
    )
    git(repo, "commit", "-q", "-am", "main moves on")
    git(repo, "checkout", "-q", "feature")
    return repo


def test_collects_files_statuses_and_seeds(repo: Path) -> None:
    changes = collect_changes(repo, "main", "feature")
    files = {c.diff.path: c for c in changes.files}

    assert {path: c.diff.status for path, c in files.items()} == {
        "README.md": FileStatus.MODIFIED,
        "logo.png": FileStatus.ADDED,
        "shop/cart.py": FileStatus.ADDED,
        "shop/legacy.py": FileStatus.DELETED,
        "shop/pricing.py": FileStatus.MODIFIED,
        "shop/text.py": FileStatus.RENAMED,
    }
    assert files["shop/text.py"].diff.old_path == "shop/util.py"
    assert (files["README.md"].skipped, files["logo.png"].skipped) == ("not_source", "binary")

    seeds = {(s.change, s.node.qualified_name) for s in changes.seeds}
    assert seeds == {
        # discount() changed; tax() didn't (main's later tax edit is excluded)
        (ChangeKind.MODIFIED, "shop.pricing.discount"),
        # rename with an edit: matched across paths, so MODIFIED
        (ChangeKind.MODIFIED, "shop.text.slugify"),
        (ChangeKind.DELETED, "shop.legacy.old"),
        (ChangeKind.ADDED, "shop.cart"),
        (ChangeKind.ADDED, "shop.cart.total"),
    }


def test_head_and_base_sides_are_parsed_from_git_objects(repo: Path) -> None:
    changes = collect_changes(repo, "main", "feature")
    pricing = next(c for c in changes.files if c.diff.path == "shop/pricing.py")

    assert pricing.head is not None and pricing.base is not None
    head_code = next(n.code for n in pricing.head.nodes if n.name == "discount")
    base_code = next(n.code for n in pricing.base.nodes if n.name == "discount")
    assert "round(" in head_code and "round(" not in base_code
    # base is the merge-base, not main's tip: tax is still 1.2 there
    assert "1.2\n" in pricing.base.module_node.code + "\n"


def test_rejects_option_like_revisions(repo: Path) -> None:
    with pytest.raises(GitError, match="invalid revision"):
        collect_changes(repo, "--output=/tmp/x", "feature")
