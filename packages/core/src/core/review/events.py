"""Live review events: the contract between the worker, which rings, and the
API, which streams (Phase 5 step 4).

Postgres is the mailbox, Redis the doorbell (D105). Every event is a
`review_events` row, written before anyone is told. The worker then
publishes a bare notification on the review's channel. A streamer reads the
new rows from the database whenever the doorbell rings, and also every few
seconds anyway. So a lost notification (pub/sub keeps nothing) delays an
event, but never loses it.
"""

# The last event a review ever gets: whoever streams it can stop (D106).
END = "end"


def channel(review_id: int) -> str:
    return f"review:{review_id}"
