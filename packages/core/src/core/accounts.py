"""GitHub accounts named in settings: `GITHUB_ALLOWED_ACCOUNTS` (whose
installations CodeLens works for, D119) and `API_ADMIN_LOGINS` (who sees
every repo on the dashboard, D111).

A login is a name GitHub lets its account change; a login given up can then
be registered by someone else, who would match an entry that names only the
login. So an entry can be pinned to the account's id, which never changes:
`RAIF-KARANI:123456` matches only the account with that id, whatever it is
called (D136). `codelens setup` writes the App owner's entry pinned. A bare
login still works, matched by name, and `codelens doctor` points it out.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Accounts:
    # Lowercased login -> the id it is pinned to, or None (matched by name).
    # GitHub logins are case-insensitive.
    pins: Mapping[str, int | None]

    @classmethod
    def parse(cls, text: str) -> "Accounts":
        """`login` or `login:id` entries, comma-separated. A bad id is a
        ValueError, so a typo fails at startup instead of matching nobody."""
        pins: dict[str, int | None] = {}
        for entry in text.split(","):
            login, _, pin = (part.strip() for part in entry.partition(":"))
            if not login:
                continue
            if pin and not pin.isdigit():
                raise ValueError(f"{entry.strip()!r}: an account id is a number (login:id)")
            pins[login.lower()] = int(pin) if pin else None
        return cls(pins)

    def allows(self, login: str | None, account_id: int | None) -> bool:
        if login is None or login.lower() not in self.pins:
            return False
        pin = self.pins[login.lower()]
        return pin is None or pin == account_id

    @property
    def unpinned(self) -> list[str]:
        return sorted(login for login, pin in self.pins.items() if pin is None)

    def __bool__(self) -> bool:
        return bool(self.pins)

    def __str__(self) -> str:
        return ", ".join(
            login if pin is None else f"{login}:{pin}" for login, pin in sorted(self.pins.items())
        )
