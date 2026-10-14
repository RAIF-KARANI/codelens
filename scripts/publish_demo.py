"""Publish the demo's data: migrate the demo's database, then load the demo
repos into it (docs/demo.md).

    python scripts/publish_demo.py                       # export + migrate + import
    python scripts/publish_demo.py --skip-export         # reuse data/demo/*.json.gz

The demo database's address comes from DEMO_DATABASE_URL, in the environment
or in `.env.demo` (gitignored), and is never printed. Run it after new reviews on the demo
repo to refresh the public demo; an import replaces those repos.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.demo"
REPOS = ["RAIF-KARANI/codelens-demo"]
EXPORT = ROOT / "data" / "demo" / "codelens-demo.json.gz"


def demo_database_url() -> str:
    if os.environ.get("DEMO_DATABASE_URL"):  # e.g. a local database, to try it
        return os.environ["DEMO_DATABASE_URL"]
    if not ENV_FILE.exists():
        sys.exit(f"{ENV_FILE.name} is missing (docs/demo.md, 'Host it')")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "DEMO_DATABASE_URL" and value.strip():
            return value.strip()
    sys.exit(f"DEMO_DATABASE_URL is not set in {ENV_FILE.name}")


def run(*args: str, env: dict[str, str] | None = None) -> None:
    # The URL travels in the environment or arguments, never in what's printed.
    print("$", " ".join(a if "://" not in a else "<demo database>" for a in args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()
    url = demo_database_url()
    python = sys.executable

    if not args.skip_export:
        repos = [part for repo in REPOS for part in ("--repo", repo)]
        run(python, "-m", "core.demo", "export", *repos, "--out", str(EXPORT))
    run(
        python,
        "-m",
        "alembic",
        "-c",
        "packages/core/alembic.ini",
        "upgrade",
        "head",
        env={**os.environ, "DATABASE_URL": url},
    )
    run(python, "-m", "core.demo", "import", str(EXPORT), "--database-url", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
