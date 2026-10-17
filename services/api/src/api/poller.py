"""The poller service: `python -m api.poller` (planning-v2.md D145).

Looks at GitHub every GITHUB_POLL_INTERVAL_S seconds (core.github.poll) and
queues what it finds through the API's own job queue, so it lives in the API's
package and image. CodeLens Desktop runs it instead of a webhook relay; the
development stack can run either, or both (`docker compose --profile poller`).
"""

import asyncio
import logging

from api.queue import CeleryJobQueue
from core.config import Settings, get_settings
from core.db import create_engine
from core.github.auth import InstallationTokens, app_jwt, load_private_key
from core.github.client import GitHubClient
from core.github.poll import Poller
from core.github.webhooks import Policy
from core.logs import configure_logging

logger = logging.getLogger("api.poller")

# After a failed look (GitHub or the network down), wait this long first,
# doubling up to the cap, so an outage isn't met with a request a second.
RETRY_S = 30
RETRY_CAP_S = 15 * 60


async def run(settings: Settings) -> None:
    if settings.github_app_id is None:
        # Before the desktop's wizard has made the App there is nothing to
        # ask GitHub. Waiting quietly beats exiting into a restart loop.
        logger.warning("GITHUB_APP_ID is not set: nothing to poll until the App exists")
        await asyncio.Event().wait()
    key = load_private_key(settings.github_app_private_key_path)
    app_id = settings.github_app_id
    engine = create_engine(settings.database_url)
    queue = CeleryJobQueue(settings.redis_url)
    try:
        async with GitHubClient(settings.github_api_url) as gh:
            poller = Poller(
                engine,
                gh,
                lambda: app_jwt(app_id, key),
                InstallationTokens(gh, app_id, key),
                Policy(settings.allowed_accounts, settings.github_reviews_per_hour),
                queue,
                interval_s=settings.github_poll_interval_s,
            )
            failures = 0
            while True:
                try:
                    cycle = await poller.poll_once()
                except Exception:
                    failures += 1
                    wait = min(RETRY_S * 2 ** (failures - 1), RETRY_CAP_S)
                    logger.exception("polling GitHub failed; trying again in %d s", wait)
                    await asyncio.sleep(wait)
                    continue
                failures = 0
                for event in cycle.events:
                    logger.info("%s", event)
                logger.info(
                    "polled: %d requests (%d not modified), %d indexed, %d reviews queued;"
                    " rate limit left %s; next in %d s",
                    cycle.requests,
                    cycle.not_modified,
                    len(cycle.indexed),
                    len(cycle.reviews),
                    cycle.rate_remaining or "n/a",
                    poller.interval_s,
                )
                await asyncio.sleep(poller.interval_s)
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    # A line per GitHub request, most of them 304s, every minute: the cycle's
    # own summary says what matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
