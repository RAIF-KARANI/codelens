"""Cheap token estimates for budgeting.

The exact count depends on the model's tokenizer, which Ollama doesn't expose
over its API. Measured against qwen3's real counts (planning.md §6.4, D63):

    plain code              4.2 chars/token
    line-numbered code      3.3 chars/token
    line-numbered diff      3.0 chars/token   <- what review prompts mostly are

3.0 errs on the safe side for all of them. An earlier 3.5 under-counted diffs
by 16%, enough to push a "fitted" prompt past an 8k window.
"""

import math

CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def chars_for_tokens(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)
