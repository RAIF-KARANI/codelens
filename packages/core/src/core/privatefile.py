"""Files only their owner may read: the App's private key, its env file, `.env`.

They used to be written at the process umask (0644 under the usual 022) and
narrowed with `chmod` afterwards, or not at all for `.env`, so another local
user could read them: in that window for the key, for good for `.env`, which
holds API_JWT_SECRET and the webhook secret (D132).
"""

import os
import tempfile
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, readable only by its owner from the first byte.

    A temporary file next to it is created 0600 (mkstemp), filled, then
    renamed over `path`: the content never exists at a wider mode, and a rename
    is atomic, so a reader sees the old file or the new one, never half of it.
    Windows ignores the mode bits; there the file inherits the user profile's
    ACLs.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def private_dir(path: Path) -> None:
    """Create `path` as a directory only its owner can enter (0700), or narrow
    an existing one: `mkdir(mode=...)` applies only when it creates it."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
