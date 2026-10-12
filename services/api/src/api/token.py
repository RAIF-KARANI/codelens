"""Mint an API token for local development (until the dashboard signs them).

    python -m api.token [--sub 1] [--login dev] [--minutes 60]

Uses API_JWT_SECRET from the environment / .env, like the API itself.
"""

import argparse
import sys
from datetime import timedelta

from api.auth import issue_token
from core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m api.token", description=__doc__.split("\n")[0])
    parser.add_argument("--sub", default="1", help="GitHub user id (default 1)")
    parser.add_argument("--login", default="dev", help="GitHub login (default dev)")
    parser.add_argument("--minutes", type=int, default=60, help="lifetime (default 60)")
    args = parser.parse_args()

    secret = get_settings().api_jwt_secret.get_secret_value()
    if not secret:
        print("error: API_JWT_SECRET is not set", file=sys.stderr)
        raise SystemExit(1)
    print(issue_token(secret, args.sub, args.login, ttl=timedelta(minutes=args.minutes)))


if __name__ == "__main__":
    main()
