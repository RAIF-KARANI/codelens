"""Cheap token estimates for budgeting.

The exact count depends on the model's tokenizer, which Ollama doesn't expose
over its API. For code, ~3.5 characters per token is a conservative average
(it overestimates slightly), which is what a budget wants: the error is on
the safe side.
"""

import math

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def chars_for_tokens(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)
