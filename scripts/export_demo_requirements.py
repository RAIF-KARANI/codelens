"""Write deploy/api/requirements.txt: the demo API's runtime dependencies,
pinned from uv.lock, for Vercel's Python builder (deploy/api/README.md).

Vercel installs from a requirements file, not from our uv workspace, so the
two local packages are listed by path (relative to deploy/api) after the
pinned third-party ones.

    python scripts/export_demo_requirements.py           # write it
    python scripts/export_demo_requirements.py --check   # exit 1 if stale
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "deploy" / "api" / "requirements.txt"
HEADER = (
    "# The demo API's runtime dependencies, for Vercel (deploy/api/README.md).\n"
    "# Generated from uv.lock; regenerate after changing dependencies:\n"
    "#   python scripts/export_demo_requirements.py\n"
)
LOCAL = ("../../packages/core", "../../services/api")


def render() -> str:
    # uv as a program (CI), or as a module of this Python (no uv on PATH).
    uv = ["uv"] if shutil.which("uv") else [sys.executable, "-m", "uv"]
    pinned = subprocess.run(
        [
            *uv,
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
    return HEADER + "\n".join([*lines, *LOCAL]) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is out of date: run {Path(__file__).name}")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
