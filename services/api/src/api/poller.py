"""The poller service: `python -m api.poller` (planning-v2.md D145).

Looks at GitHub every GITHUB_POLL_INTERVAL_S seconds (core.github.poll) and
queues what it finds through the API's own job queue, so it lives in the API's
package and image. CodeLens Desktop runs it instead of a webhook relay; the
development stack can run either, or both (`docker compose --profile poller`).

After each look it also queues again the pull request reviews that failed only
because Ollama didn't answer, once it does (core.github.retry, D181).
"""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncEngine

from api.queue import CeleryJobQueue, JobQueue, QueueUnavailable
from core.config import Settings, get_settings
from core.db import create_engine
from core.db.models import Review
from core.enums import ReviewStatus
from core.github.auth import InstallationTokens, app_jwt, load_private_key
from core.github.client import GitHubClient
from core.github.poll import Poller
from core.github.retry import RetryRefused, model_outages, retry_review
from core.github.webhooks import Policy
from core.logs import configure_logging
from core.providers.base import ProviderUnavailableError
from core.providers.ollama import OllamaClient

logger = logging.getLogger("api.poller")

# After a failed look (GitHub or the network down), wait this long first,
# doubling up to the cap, so an outage isn't met with a request a second.
RETRY_S = 30
RETRY_CAP_S = 15 * 60


async def ollama_answers(base_url: str) -> bool:
    """One quick look, no retries."""
    try:
        async with OllamaClient(base_url, timeout_s=5, max_retries=0) as client:
            await client.version()
    except ProviderUnavailableError:
        return False
    return True


async def requeue_model_outages(
    engine: AsyncEngine, queue: JobQueue, answers: Callable[[], Awaitable[bool]]
) -> list[int]:
    """The reviews that failed because Ollama didn't answer, queued again now
    that it does. Ollama is asked only when there is something to queue."""
    async with engine.connect() as conn:
        failed = await model_outages(conn)
    if not failed or not await answers():
        return []
    queued: list[int] = []
    for review_id in failed:
        try:
            async with engine.begin() as conn:
                again = await retry_review(conn, review_id)
        except RetryRefused:
            continue  # a push got there first
        try:
            await queue.review(again.review_id, again.base_sha, title=again.title)
        except QueueUnavailable:
            async with engine.begin() as conn:
                await conn.execute(
                    update(Review)
                    .where(Review.id == again.review_id)
                    .values(
                        status=ReviewStatus.FAILED,
                        finished_at=func.now(),
                        error="could not enqueue: the job queue is unavailable",
                    )
                )
            continue
        logger.info(
            "review %s failed while Ollama was down: queued again as %s", review_id, again.review_id
        )
        queued.append(again.review_id)
    return queued


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
                try:
                    await requeue_model_outages(
                        engine, queue, lambda: ollama_answers(settings.ollama_base_url)
                    )
                except Exception:
                    logger.exception("queuing reviews again after an Ollama outage failed")
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


def _stop(signum: int, frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    # In a container this is process 1, which ignores SIGTERM unless it says
    # otherwise: `docker compose stop` would wait 10 s, then kill it (exit 137,
    # seen in step 4's tests). asyncio.run cancels the loop on the way out.
    signal.signal(signal.SIGTERM, _stop)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    # A line per GitHub request, most of them 304s, every minute: the cycle's
    # own summary says what matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
