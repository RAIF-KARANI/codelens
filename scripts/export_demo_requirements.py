"""Write deploy/api/pyproject.toml and its uv.lock: the demo API's
dependencies for Vercel's Python builder (deploy/api/README.md).

Vercel installs deploy/api as its own uv project (`uv sync` with its
uv.lock), not as part of our workspace. It depends on services/api by path,
and every third-party package is pinned (as a constraint) to the version in
the workspace's uv.lock, so the demo runs exactly what the tests ran.

Why not a requirements.txt: Vercel converts one to a pyproject.toml, rebasing
`../../` paths to the repository root but writing the result into
deploy/api, so the local packages are then looked for in deploy/api/packages
(@vercel/python 25, 2026-10-15).

    python scripts/export_demo_requirements.py           # write both files
    python scripts/export_demo_requirements.py --check   # exit 1 if stale
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "api"
TARGET = DEPLOY / "pyproject.toml"
HEADER = """\
# The demo API as Vercel installs it (deploy/api/README.md): services/api by
# path, every other package pinned to the workspace's uv.lock. Generated;
# regenerate (with deploy/api/uv.lock) after changing dependencies:
#   python scripts/export_demo_requirements.py
[project]
name = "codelens-demo-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["codelens-api"]

[tool.uv]
package = false
constraint-dependencies = [
"""
FOOTER = """\
]

[tool.uv.sources]
codelens-api = { path = "../../services/api" }
"""


def uv() -> list[str]:
    # uv as a program (CI), or as a module of this Python (no uv on PATH).
    return ["uv"] if shutil.which("uv") else [sys.executable, "-m", "uv"]


def render() -> str:
    pinned = subprocess.run(
        [
            *uv(),
            "export",
            "--package",
            "codelens-api",
            "--no-hashes",
            "--no-dev",
            "--no-emit-workspace",
            "--no-header",
            "--frozen",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [line for line in pinned.splitlines() if line and not line.lstrip().startswith("#")]
    return HEADER + "".join(f'    "{line}",\n' for line in lines) + FOOTER


def lock(*extra: str) -> int:
    return subprocess.run([*uv(), "lock", "--directory", str(DEPLOY), *extra], cwd=ROOT).returncode


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is out of date: run {Path(__file__).name}")
            return 1
        return lock("--check")
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return lock()


if __name__ == "__main__":
    sys.exit(main())
