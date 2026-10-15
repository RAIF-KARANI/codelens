"""Reading and updating a `.env` file in place.

`codelens setup` fills values into the user's `.env`, which is also their
documentation (it starts as a copy of `.env.example`, comments included), so
a value is replaced on its own line and every other line is left as it was.
New keys go at the end.
"""

from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    """`KEY=value` lines; comments, blanks and `export` prefixes ignored."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = _parse(line)
        if key:
            values[key] = value
    return values


def set_env(path: Path, updates: dict[str, str]) -> None:
    """Set each key's value: on its existing line if there is one, else at the end."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key, _ = _parse(line)
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{key}={value}" for key, value in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _parse(line: str) -> tuple[str | None, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, ""
    key, value = stripped.removeprefix("export ").split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key.strip(), value
