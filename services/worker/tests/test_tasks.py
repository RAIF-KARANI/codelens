"""The worker's tasks, run in-process with `.apply()` (eager: no broker). The
integration tests use the test database and fake models (services/conftest.py)."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

import worker.tasks
from core import jobs
from core.config import Settings
from core.db import create_engine
from core.indexing import IndexingError, IndexLocked, indexer
from core.review.pipeline import ReviewError
from worker.celery_app import TASK_SOFT_TIME_LIMIT_S, TASK_TIME_LIMIT_S, celery_app
from worker.tasks import LOCK_MAX_RETRIES, embed_repo, index_repo, ping, review

Sql = Callable[..., list[Any]]


def test_ping_runs_eagerly() -> None:
    # .apply() executes in-process without a broker.
    assert ping.apply().get() == "pong"


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        (jobs.PING, jobs.INDEX_QUEUE),
        (jobs.INDEX_REPO, jobs.INDEX_QUEUE),
        (jobs.EMBED_REPO, jobs.GPU_QUEUE),
        (jobs.REVIEW, jobs.GPU_QUEUE),
    ],
)
def test_every_job_is_registered_and_routed(task: str, queue: str) -> None:
    assert task in celery_app.tasks
    assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def test_every_task_has_a_ceiling() -> None:
    # Phase 9 step 5 (D131): nothing set one, so a stuck job held one of the
    # few shared worker slots for ever.
    soft, hard = celery_app.conf.task_soft_time_limit, celery_app.conf.task_time_limit
    assert (soft, hard) == (TASK_SOFT_TIME_LIMIT_S, TASK_TIME_LIMIT_S)
    assert 0 < soft < hard


def queued_review(sql: Sql, repo_id: int, head_sha: str, status: str = "queued") -> int:
    return sql(
        "INSERT INTO reviews (repo_id, source, head_sha, status)"
        " VALUES (:repo, 'cli', :head, :status) RETURNING id",
        repo=repo_id,
        head=head_sha,
        status=status,
    )[0].id


@pytest.mark.integration
def test_review_claims_the_queued_row(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    summary = review.apply(args=(review_id, shop.base_sha)).get()

    assert summary == {"review_id": review_id, "status": "completed", "posted": 1}
    [row] = sql("SELECT status, base_sha, model, started_at FROM reviews")  # no second row
    assert (row.status, row.base_sha, row.model) == ("completed", shop.base_sha, "fake-llm")
    assert row.started_at is not None


@pytest.mark.integration
def test_review_restarts_a_crashed_attempt(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    # A worker died mid-review: the row is `running`, with part of a timeline
    # and a finding. The redelivered task must start over, not append.
    review_id = queued_review(sql, shop.repo_id, shop.head_sha, status="running")
    sql(
        "INSERT INTO review_events (review_id, seq, type)"
        " VALUES (:id, 1, 'stage'), (:id, 2, 'stage')",
        id=review_id,
    )
    sql(
        "INSERT INTO findings (review_id, file_path, line_start, line_end, severity, category,"
        " title, explanation) VALUES (:id, 'stale.py', 1, 1, 'warning', 'bug', 'stale', '')",
        id=review_id,
    )

    review.apply(args=(review_id, shop.base_sha)).get()

    seqs = [r.seq for r in sql("SELECT seq FROM review_events ORDER BY seq")]
    assert seqs == list(range(1, len(seqs) + 1))
    last_two = sql("SELECT type FROM review_events ORDER BY seq DESC LIMIT 2")
    assert [r.type for r in last_two] == ["end", "done"]
    assert [r.file_path for r in sql("SELECT file_path FROM findings")] == [
        "shop/pricing.py",
        "shop/orders.py",
    ]


@pytest.mark.integration
def test_review_skips_a_finished_review(shop: Any, sql: Sql, worker_on_fakes: Any) -> None:
    # Acks are late, so a finished review's message can be delivered again.
    review_id = queued_review(sql, shop.repo_id, shop.head_sha, status="completed")

    summary = review.apply(args=(review_id, shop.base_sha)).get()

    assert summary["skipped"] is True
    assert worker_on_fakes.calls == []  # the model was never asked


@pytest.mark.integration
def test_review_failing_before_the_pipeline_marks_the_row(
    shop: Any, sql: Sql, worker_on_fakes: Any
) -> None:
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    result = review.apply(args=(review_id, "no-such-branch"))

    assert isinstance(result.result, ReviewError)
    [row] = sql("SELECT status, error, finished_at FROM reviews")
    assert row.status == "failed"
    assert "no-such-branch" in row.error
    assert row.finished_at is not None
    # A streamer is told why, and that the stream is over.
    events = sql("SELECT type, payload FROM review_events ORDER BY seq")
    assert [e.type for e in events] == ["error", "end"]
    assert "no-such-branch" in events[0].payload["message"]
    assert events[1].payload == {"status": "failed"}


@pytest.mark.integration
def test_every_event_rings_the_doorbell(
    shop: Any, sql: Sql, worker_on_fakes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rings: list[int] = []

    @asynccontextmanager
    async def recording_doorbell(settings: Any) -> AsyncIterator[Any]:
        async def notify(review_id: int) -> None:
            rings.append(review_id)

        yield notify

    monkeypatch.setattr(worker.tasks, "doorbell", recording_doorbell)
    review_id = queued_review(sql, shop.repo_id, shop.head_sha)

    review.apply(args=(review_id, shop.base_sha)).get()

    events = sql("SELECT type FROM review_events ORDER BY seq")
    assert events[-1].type == "end"
    # One ring per event, each after its row was committed.
    assert rings == [review_id] * len(events)


@pytest.mark.integration
def test_index_refuses_a_repo_it_cannot_reach(sql: Sql, worker_on_fakes: Any) -> None:
    [github] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('acme/app', 'github') RETURNING id"
    )
    [local] = sql(
        "INSERT INTO repos (full_name, source) VALUES ('local/gone', 'local') RETURNING id"
    )

    uninstalled = index_repo.apply(args=(github.id,))
    no_checkout = index_repo.apply(args=(local.id,))

    assert isinstance(uninstalled.result, IndexingError)
    assert "not covered by any installation" in str(uninstalled.result)
    assert isinstance(no_checkout.result, IndexingError)
    assert "no local checkout" in str(no_checkout.result)
    # Each repo is left 'failed', with a failed run saying why (D163).
    rows = sql(
        "SELECT r.status, i.status AS run_status, i.error FROM repos r"
        " JOIN index_runs i ON i.repo_id = r.id WHERE r.id IN (:a, :b) ORDER BY r.id",
        a=github.id,
        b=local.id,
    )
    assert [(r.status, r.run_status) for r in rows] == [("failed", "failed")] * 2
    assert [r.error for r in rows] == [str(uninstalled.result), str(no_checkout.result)]


@pytest.mark.integration
def test_a_failure_before_the_run_fails_the_claimed_repo(
    sql: Sql, tmp_path: Path, worker_on_fakes: Any
) -> None:
    # D163: a re-index claims the repo ('pending'); a failure before the
    # indexer opened a run used to leave it there, and the dashboard's
    # Re-index button disabled for good.
    gone = tmp_path / "none"
    [repo] = sql(
        "INSERT INTO repos (full_name, source, local_path, status)"
        " VALUES ('local/none', 'local', :path, 'pending') RETURNING id",
        path=str(gone),
    )

    result = index_repo.apply(args=(repo.id,))

    assert isinstance(result.result, IndexingError)
    [row] = sql("SELECT status FROM repos WHERE id = :id", id=repo.id)
    assert row.status == "failed"
    [run] = sql("SELECT status, finished_at, error FROM index_runs WHERE repo_id = :id", id=repo.id)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.error == f"not a directory: {gone.resolve()}"


@pytest.mark.integration
def test_a_failure_inside_the_run_is_recorded_once(
    shop: Any, sql: Sql, worker_on_fakes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Once a run exists the indexer records its own failure: the task adds
    # no second failed run.
    async def broken(*args: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(indexer, "_write_graph", broken)
    sql("UPDATE repos SET status = 'pending' WHERE id = :id", id=shop.repo_id)

    result = index_repo.apply(args=(shop.repo_id,))

    assert isinstance(result.result, RuntimeError)
    [row] = sql("SELECT status FROM repos WHERE id = :id", id=shop.repo_id)
    assert row.status == "failed"
    runs = sql(
        "SELECT status, error FROM index_runs WHERE repo_id = :id ORDER BY id", id=shop.repo_id
    )
    # The fixture's run, then this one's.
    assert [r.status for r in runs] == ["succeeded", "failed"]
    assert "disk full" in runs[-1].error


@pytest.mark.integration
def test_jobs_for_an_account_no_longer_covered_mint_no_token(
    sql: Sql, worker_on_fakes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Phase 9 step 4 (D130): jobs queued before an account left the allowlist,
    # or for a suspended installation, or put on the queue some other way, used
    # to mint a token and fetch anyway. The worker checks the policy first.
    async def no_token(*args: Any) -> str:
        raise AssertionError("minted a token for an installation that isn't covered")

    monkeypatch.setattr(worker.tasks, "_installation_token", no_token)

    def github_repo(github_id: int, login: str, *, suspended: bool = False) -> int:
        [installation] = sql(
            "INSERT INTO installations"
            " (github_installation_id, account_id, account_login, account_type, suspended_at)"
            " VALUES (:id, :id, :login, 'User', CASE WHEN :suspended THEN now() END)"
            " RETURNING id",
            id=github_id,
            login=login,
            suspended=suspended,
        )
        return sql(
            "INSERT INTO repos (full_name, source, github_id, installation_id)"
            " VALUES (:name, 'github', :id, :installation) RETURNING id",
            name=f"{login}/app{github_id}",
            id=github_id,
            installation=installation.id,
        )[0].id

    removed = github_repo(902, "mallory")  # not in GITHUB_ALLOWED_ACCOUNTS=octo
    suspended = github_repo(903, "octo", suspended=True)
    [queued] = sql(
        "INSERT INTO reviews (repo_id, source, pr_number, head_sha, status)"
        " VALUES (:repo, 'github', 7, 'abc', 'queued') RETURNING id",
        repo=removed,
    )

    for repo in (removed, suspended):
        indexed = index_repo.apply(args=(repo,))
        assert isinstance(indexed.result, IndexingError)
        assert "no longer allowed, or the App's installation is suspended" in str(indexed.result)
    reviewed = review.apply(args=(queued.id, "main"))

    assert isinstance(reviewed.result, ReviewError)
    [row] = sql("SELECT status, error FROM reviews WHERE id = :id", id=queued.id)
    assert row.status == "failed"
    assert "no longer allowed" in row.error


@pytest.mark.integration
def test_index_retries_while_another_run_holds_the_lock(
    shop: Any,
    sql: Sql,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    worker_on_fakes: Any,
) -> None:
    attempts = 0
    original = worker.tasks._index

    async def counting(*args: Any) -> Any:
        nonlocal attempts
        attempts += 1
        return await original(*args)

    monkeypatch.setattr(worker.tasks, "_index", counting)

    async def go() -> Any:
        engine = create_engine(settings.database_url, pooled=False)
        try:
            async with engine.connect() as conn:
                # Hold the same advisory lock a running index takes (D41).
                await conn.execute(text("SELECT pg_advisory_lock(hashtext('index:local/shop'))"))
                # One retry left: eager .apply() runs the retry at once, which
                # hits the lock again and gives up.
                return await asyncio.to_thread(
                    index_repo.apply, args=(shop.repo_id,), retries=LOCK_MAX_RETRIES - 1
                )
        finally:
            await engine.dispose()

    result = asyncio.run(go())

    assert attempts == 2
    assert isinstance(result.result, IndexLocked)
    # The run holding the lock owns the repo's status: a locked-out attempt
    # records no failure of its own (D163).
    [row] = sql("SELECT status FROM repos WHERE id = :id", id=shop.repo_id)
    assert row.status == "ready"
    runs = sql("SELECT status FROM index_runs WHERE repo_id = :id", id=shop.repo_id)
    assert [r.status for r in runs] == ["succeeded"]


@pytest.mark.integration
def test_embedding_yields_the_gpu_between_slices(
    shop: Any, sql: Sql, worker_on_fakes: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Q17/D107: a long embedding job re-queues itself after each slice, so a
    # review queued meanwhile runs before the next slice.
    sql("UPDATE code_nodes SET embedding = NULL WHERE repo_id = :id", id=shop.repo_id)
    total = sql("SELECT count(*) FROM code_nodes WHERE repo_id = :id", id=shop.repo_id)[0][0]
    requeued: list[int] = []
    monkeypatch.setattr(worker.tasks, "EMBED_SLICE_NODES", 10)
    monkeypatch.setattr(worker.tasks, "requeue_embedding", requeued.append)

    slices = [embed_repo.apply(args=(shop.repo_id,)).get()]
    while slices[-1]["remaining"]:
        slices.append(embed_repo.apply(args=(shop.repo_id,)).get())

    assert all(s["embedded"] <= 10 for s in slices)
    assert sum(s["embedded"] for s in slices) == total
    assert len(slices) == -(-total // 10)  # ceil: every slice but the last is full
    assert requeued == [shop.repo_id] * (len(slices) - 1)  # the last one doesn't
    assert (
        sql(
            "SELECT count(*) FROM code_nodes WHERE repo_id = :id AND embedding IS NULL",
            id=shop.repo_id,
        )[0][0]
        == 0
    )
