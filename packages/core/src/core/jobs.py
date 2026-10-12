"""The contract between the API and the worker: task names and queues.

The API enqueues tasks by name and never imports worker code (its image
doesn't contain it), so both sides read the names from here. Plain constants:
core does not depend on Celery.
"""

# Two queues (planning.md §5.10, D88). `index` is CPU work (parse, persist,
# re-link) and may run two at a time. `gpu` holds everything that loads a
# model: embedding, and whole reviews (query embeddings + generation). One
# worker process consumes it, so two jobs never compete for 8 GB of VRAM.
INDEX_QUEUE = "index"
GPU_QUEUE = "gpu"
DEFAULT_QUEUE = INDEX_QUEUE  # where unrouted tasks (ping) go

PING = "worker.ping"
INDEX_REPO = "worker.index_repo"
EMBED_REPO = "worker.embed_repo"
REVIEW = "worker.review"

TASK_ROUTES: dict[str, dict[str, str]] = {
    INDEX_REPO: {"queue": INDEX_QUEUE},
    EMBED_REPO: {"queue": GPU_QUEUE},
    REVIEW: {"queue": GPU_QUEUE},
}
