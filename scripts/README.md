# scripts

Measurement tools. Their results are recorded in `planning.md` (§1.3, §5.7, §6.4).
Run them with nothing else loaded on the GPU (`ollama ps` should be empty), or the
numbers measure contention instead of the setting.

| Script | Measures |
|---|---|
| `bench_embed.py [repo] [--texts 512]` | Embedding throughput (chunks/s, tokens/s) for every batch size × concurrency, on real code chunks |
| `bench_llm.py --num-ctx 8192 [--prompt-tokens 5500]` | LLM load, prompt and decode rates, and how much of the model is in VRAM, for one context window |

Both use the configured Ollama (`OLLAMA_BASE_URL`). To compare Ollama *server* settings
(flash attention, KV cache type, parallel slots) without changing your system, start a
second server on another port with the settings in its environment, point
`OLLAMA_BASE_URL` at it, and **stop its runners afterwards**. Killing `ollama serve` leaves
`llama-server` processes holding VRAM, which silently skews every later run
(planning.md D67). Check with:

```bash
ollama ps
nvidia-smi --query-compute-apps=pid,process_name --format=csv
```

For an end-to-end timing breakdown of a real review, run `python -m core.review` and read
the `chunk_done` events (estimated vs real prompt tokens, output tokens, LLM time per chunk)
in `review_events`.
