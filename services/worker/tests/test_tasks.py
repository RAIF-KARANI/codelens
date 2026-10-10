from worker.tasks import ping


def test_ping_runs_eagerly() -> None:
    # .apply() executes in-process without a broker.
    assert ping.apply().get() == "pong"
