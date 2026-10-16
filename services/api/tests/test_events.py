"""GET /api/v1/reviews/{id}/events: replay, live delivery through the Redis
doorbell, and the end of a stream. Real Postgres and Redis."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text

from api.main import create_app
from api.sse import review_stream
from core.config import Settings
from core.db import create_engine
from core.review.events import channel

pytestmark = pytest.mark.integration

Sql = Callable[..., list[Any]]


def frames(body: str) -> list[dict[str, Any]]:
    """Parse an SSE body into messages (comments, i.e. heartbeats, skipped)."""
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            out.append({**fields, "data": json.loads(fields["data"])})
    return out


@pytest.fixture
def review_id(sql: Sql) -> int:
    [repo] = sql("INSERT INTO repos (full_name, source) VALUES ('local/x', 'local') RETURNING id")
    return sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', 'abc', 'running') RETURNING id",
        repo=repo.id,
    )[0].id


def add_events(sql: Sql, review_id: int, *types: str, start: int = 1) -> None:
    for seq, type_ in enumerate(types, start=start):
        sql(
            "INSERT INTO review_events (review_id, seq, type, payload)"
            " VALUES (:id, :seq, :type, :payload)",
            id=review_id,
            seq=seq,
            type=type_,
            payload=json.dumps({"n": seq}),
        )


def get(settings: Settings, auth: dict[str, str], url: str, **headers: str) -> Any:
    with TestClient(create_app(settings)) as c:
        return c.get(url, headers={**auth, **headers})


def test_a_finished_review_replays_then_ends(
    settings: Settings, sql: Sql, review_id: int, auth_headers: dict[str, str]
) -> None:
    add_events(sql, review_id, "stage", "done", "end")
    url = f"/api/v1/reviews/{review_id}/events"

    response = get(settings, auth_headers, url)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert [(f["id"], f["event"], f["data"]) for f in frames(response.text)] == [
        ("1", "stage", {"n": 1}),
        ("2", "done", {"n": 2}),
        ("3", "end", {"n": 3}),
    ]
    # Reconnecting: only what came after the last id the client saw.
    resumed = get(settings, auth_headers, url, **{"Last-Event-ID": "2"})
    assert [f["event"] for f in frames(resumed.text)] == ["end"]
    after = get(settings, auth_headers, f"{url}?after=1")
    assert [f["event"] for f in frames(after.text)] == ["done", "end"]


def test_an_old_review_without_an_end_event_still_ends(
    settings: Settings, sql: Sql, review_id: int, auth_headers: dict[str, str]
) -> None:
    # Finished before `end` events existed: quiet for longer than 30 s.
    add_events(sql, review_id, "stage", "done")
    sql(
        "UPDATE review_events SET created_at = now() - interval '1 hour' WHERE review_id = :id",
        id=review_id,
    )
    sql(
        "UPDATE reviews SET status = 'completed', created_at = now() - interval '1 hour',"
        " finished_at = now() - interval '1 hour' WHERE id = :id",
        id=review_id,
    )

    body = get(settings, auth_headers, f"/api/v1/reviews/{review_id}/events").text

    *_, last = frames(body)
    assert last == {"event": "end", "data": {"status": "completed"}}  # no id: synthetic


def test_unknown_reviews_and_missing_tokens(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    with TestClient(create_app(settings)) as c:
        missing = c.get("/api/v1/reviews/999999/events", headers=auth_headers)
        anonymous = c.get("/api/v1/reviews/1/events")
    assert missing.status_code == 404
    assert anonymous.status_code == 401


# --- live: the stream itself, against real Redis


async def _insert(url: str, review_id: int, seq: int, type_: str) -> None:
    engine = create_engine(url, pooled=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO review_events (review_id, seq, type) VALUES (:id, :seq, :type)"),
                {"id": review_id, "seq": seq, "type": type_},
            )
    finally:
        await engine.dispose()


async def _follow(
    settings: Settings, review_id: int, steps: Callable[[Redis], Any], heartbeat: float
) -> list[tuple[float, str]]:
    """Stream the review while `steps` writes events; (seconds, frame) pairs."""
    engine = create_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url)
    started = time.perf_counter()
    received: list[tuple[float, str]] = []

    async def consume() -> None:
        async for item in review_stream(engine, redis, review_id, 0, heartbeat=heartbeat):
            received.append((time.perf_counter() - started, item))

    try:
        task = asyncio.create_task(consume())
        await steps(redis)
        await asyncio.wait_for(task, timeout=10)
    finally:
        await redis.aclose()
        await engine.dispose()
    return received


def test_live_events_arrive_when_the_doorbell_rings(settings: Settings, review_id: int) -> None:
    async def steps(redis: Redis) -> None:
        await asyncio.sleep(0.5)  # the stream has subscribed and caught up (nothing yet)
        await _insert(settings.database_url, review_id, 1, "stage")
        await redis.publish(channel(review_id), "new")
        await asyncio.sleep(0.5)
        await _insert(settings.database_url, review_id, 2, "end")
        await redis.publish(channel(review_id), "new")

    # A 10 s heartbeat: anything faster than that came through the doorbell.
    received = asyncio.run(_follow(settings, review_id, steps, heartbeat=10))

    events = [(t, f) for t, f in received if not f.startswith(":")]
    assert [f.split("\n")[1] for _, f in events] == ["event: stage", "event: end"]
    assert events[0][0] < 2.0
    assert events[1][0] < 2.5


def test_a_lost_doorbell_costs_a_heartbeat_not_the_event(
    settings: Settings, review_id: int
) -> None:
    async def steps(redis: Redis) -> None:
        await asyncio.sleep(0.3)
        await _insert(settings.database_url, review_id, 1, "stage")  # nobody rings
        await asyncio.sleep(1.0)
        await _insert(settings.database_url, review_id, 2, "end")

    received = asyncio.run(_follow(settings, review_id, steps, heartbeat=0.4))

    assert any(f == ": keep-alive\n\n" for _, f in received)
    assert [f.split("\n")[1] for _, f in received if not f.startswith(":")] == [
        "event: stage",
        "event: end",
    ]


def test_no_heartbeat_before_one_is_due(settings: Settings, review_id: int) -> None:
    # Measured live: the subscription's own confirmation ended the first wait
    # at once, so every stream began with a heartbeat 0.3 s in.
    async def steps(redis: Redis) -> None:
        await asyncio.sleep(1.0)
        await _insert(settings.database_url, review_id, 1, "end")
        await redis.publish(channel(review_id), "new")

    received = asyncio.run(_follow(settings, review_id, steps, heartbeat=5))

    assert [f for _, f in received if f.startswith(":")] == []
    assert received[-1][1].split("\n")[1] == "event: end"


# --- the route's own database use, while a stream is open


async def _pool_in_use_while_streaming(
    settings: Settings, review_id: int, auth: dict[str, str]
) -> tuple[int, int]:
    """Open the route's stream on a running review through the real ASGI app,
    wait until it is idle between reads, and count the API pool's connections
    in use: (while streaming, after the client leaves).

    TestClient can't do this: it waits for the whole response, and a running
    review's stream doesn't end."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        started = asyncio.Event()
        gone = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                started.set()

        path = f"/api/v1/reviews/{review_id}/events"
        scope = {
            "type": "http",
            # 2.3: the server reports disconnects, as uvicorn does, so the
            # stream ends when the client leaves.
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in auth.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        request = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(started.wait(), timeout=10)
        await asyncio.sleep(1.0)  # past the first read; now waiting on the doorbell
        streaming = app.state.engine.pool.checkedout()
        gone.set()
        await asyncio.wait_for(request, timeout=10)
        return streaming, app.state.engine.pool.checkedout()


def test_an_open_stream_holds_no_database_connection(
    settings: Settings, review_id: int, auth_headers: dict[str, str]
) -> None:
    # The audit's finding (Phase 9 step 3, D129): the route's request session,
    # used for the access check, kept its pooled connection idle in a
    # transaction for the stream's whole life, so ~15 open streams exhausted
    # the API's pool (5 + 10) and every other request waited 30 s.
    streaming, after = asyncio.run(_pool_in_use_while_streaming(settings, review_id, auth_headers))

    assert streaming == 0
    assert after == 0
