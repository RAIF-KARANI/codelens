"""Bare mirrors of GitHub repositories: what the worker indexes and reviews.

One bare repository per repo, at DATA_DIR/repos/<repo_id>.git, refreshed with
`git fetch`.
- **Bare: there is no working tree.** No file from the repo is ever written out
  as a file, and nothing in it (hooks, filters, LFS) runs. The code is read as
  git objects (parse_revision, collect_changes), as planning §5.1 requires.
- **The installation token goes to git through environment config
  (`GIT_CONFIG_*`):** never in the URL, the command line or the repo's config
  file. So it is in no file on disk and in no process list, and it expires
  within the hour anyway.
"""

import base64
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from core.diff.git import GitError

# Branches only. Pull request heads (refs/pull/N/head) are fetched one at a
# time when a PR is reviewed (step 3b).
BRANCHES = "+refs/heads/*:refs/heads/*"
# A first clone of a large repo takes a while; a hung network shouldn't hang
# a worker forever.
FETCH_TIMEOUT_S = 15 * 60


def mirror_path(data_dir: Path, repo_id: int) -> Path:
    return data_dir / "repos" / f"{repo_id}.git"


def clone_url(web_url: str, full_name: str) -> str:
    return f"{web_url.rstrip('/')}/{full_name}.git"


def sync_mirror(
    path: Path, url: str, token: str | None, refspecs: Sequence[str] = (BRANCHES,)
) -> None:
    """Create the mirror if needed, then fetch `refspecs` from `url`.

    --prune drops branches deleted upstream; the "+" in each refspec accepts
    force-pushes. Blocking (it runs git): call it with asyncio.to_thread.
    """
    if not (path / "HEAD").exists():
        path.mkdir(parents=True, exist_ok=True)
        # --template= : no sample hooks or other template files are copied in.
        _run(["git", "init", "--quiet", "--bare", "--template=", str(path)], "init", token=None)
    _run(
        ["git", "-C", str(path), "fetch", "--quiet", "--prune", "--no-tags", url, *refspecs],
        "fetch",
        token=token,
    )


def _run(args: list[str], label: str, *, token: str | None) -> None:
    env = {
        **os.environ,
        # Fail instead of waiting for a password nobody will type.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "2",
        # An empty helper resets the list: no credential manager pops up a
        # login window or stores anything.
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.extraHeader",
        "GIT_CONFIG_VALUE_1": _auth_header(token) if token else "",
    }
    try:
        subprocess.run(args, env=env, capture_output=True, check=True, timeout=FETCH_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {label}: {message}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {label} timed out") from exc


def _auth_header(token: str) -> str:
    # GitHub's documented form for an installation token over HTTPS.
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"Authorization: Basic {basic}"
