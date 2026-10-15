# CodeLens: Engineering Plan

> **How this relates to `idea.md`:** `idea.md` explains *what* CodeLens is and *why*.
> This file is the plan of record for *how* and *in what order* we build it. Where the
> two disagree, **this file wins**. It supersedes `idea.md` §14 (roadmap) and adds
> decisions D18+.

**Last updated:** 2026-10-15 · **Current position:** Phases 1–4 complete. Phase 4 adopted anchoring (test F1 0.654 → 0.767); every
change aimed at making qwen3:8b reason over context was rejected (D83, D85–D87). Phase 5 (GitHub App) is done: PRs on a real repo get
inline comments in 9–49 s, and a re-push repeats nothing (#20–#26). Phase 6 (dashboard) is done
(#27–#32): a push to a PR watched live and clicked through to its evidence and graph. Phase 7 (public demo) in progress:
steps 1–2b are merged (#33–#35: demo mode, the open demo D117, the export/import, the public demo repo reviewed and
exported); step 3 is done (#36–#40): the demo is live at https://codelens.raifkarani.in (Vercel + Supabase, Mumbai,
D118). Step 4 is split in three (user's choice): 4a, the installation allowlist and a review cap, is merged (#41); 4b, structured logs and an Ollama health check, is merged (#42); 4c, rate limits for the demo, is merged (#43). Step 5 (the README, screenshots, the demo video script) is in review;
then the user records the video.

---

## Contents

0. [Summary](#0-summary)
1. [Environment](#1-environment)
2. [Architecture decision: "naive+", not naive](#2-architecture-decision-naive-not-naive)
3. [Tech stack](#3-tech-stack)
4. [Data model](#4-data-model)
5. [Indexing pipeline (offline)](#5-indexing-pipeline-offline)
6. [Query pipeline (review time)](#6-query-pipeline-review-time)
7. [API surface](#7-api-surface)
8. [Project layout](#8-project-layout)
9. [Build phases](#9-build-phases)
10. [Evaluation](#10-evaluation)
11. [Config sketch](#11-config-sketch)
12. [Testing strategy](#12-testing-strategy)
13. [Observability](#13-observability)
14. [Security and privacy](#14-security-and-privacy)
15. [Public demo plan](#15-public-demo-plan)
16. [Decisions](#16-decisions)
17. [Risks](#17-risks)
18. [Open questions](#18-open-questions)
19. [Conventions and definition of done](#19-conventions-and-definition-of-done)

---

## 0. Summary

CodeLens reviews pull requests using context from the whole repository. The
architecture is **naive+**: a single, deterministic retrieve → rerank → generate pass.
It starts from a plain vector-RAG baseline and adds an upgrade (hybrid full-text
search, graph expansion, reranking, …) **only when the evaluation harness shows that
upgrade helps**.

Everything runs **locally and for free**:

| Concern | Choice |
|---|---|
| Review LLM | `qwen3:8b` via native Ollama on the RTX 4060 (fits in 8 GB VRAM) |
| Embeddings | `qwen3-embedding:0.6b` via Ollama, **1024-dim**, matching `EMBED_DIM` |
| Reranker | `BAAI/bge-reranker-base` cross-encoder in a small CPU service |
| Storage | One Postgres 16: relational + pgvector + full-text + graph |
| Jobs | Celery + Redis |
| UI | Next.js dashboard |
| Demo | Hosted read-only dashboard showing reviews generated locally; live reviews when the laptop is online (§15) |

Order of work: **foundation → naive baseline end-to-end → eval harness → upgrades one
at a time, each gated by evals → GitHub App → dashboard → demo.** We measure before we
add anything.

---

## 1. Environment

### 1.1 Development machine (measured 2026-10-10)

| Resource | Value | Implication |
|---|---|---|
| OS | Windows 11 Insider build 26300 | Docker Desktop has a stale-socket startup bug (§1.5) |
| CPU | i7-13650HX, 14 cores / 20 threads | The CPU reranker and the CPU query-embedding path are viable |
| RAM | 15.6 GB total; Docker VM gets **8 GB** | Budget container memory (§1.3) |
| GPU | RTX 4060 Laptop, **8 GB VRAM** | 8B model at Q4 fits; 14B doesn't fully fit (it partially offloads to CPU and slows down) |
| Disk | ~208 GB free on C: | Plenty for models, clones and the DB |
| Ollama | 0.40.2, **native Windows**, listening on `127.0.0.1:11434` | Containers reach it via `http://host.docker.internal:11434` (verified) |
| Models already pulled | `qwen3:8b` (5.2 GB), `qwen3:14b` (9.3 GB), `qwen3-embedding:0.6b` (639 MB), `llama3.2` (2 GB) | No downloads needed to start Phase 2 |

### 1.2 Local runtime topology

```
Windows host
├── Ollama (native, GPU) :11434 ── qwen3:8b (LLM), qwen3-embedding:0.6b (embeddings)
├── pnpm dev → apps/web  :3000
└── Docker Desktop (WSL2 VM, 8 GB)
    ├── postgres (pgvector/pgvector:pg16)  host:5433 → 5432
    ├── redis    (redis:7-alpine)          host:6379
    ├── migrate  (one-shot alembic upgrade head)
    ├── api      (FastAPI)                 host:8000
    ├── worker   (Celery: queues index, review)
    └── reranker (FastAPI + sentence-transformers, CPU)  host:8081   ← Phase 4
```

Why Ollama runs natively rather than in Docker: GPU passthrough into Docker on
Windows/WSL2 works, but adds setup and fragility. Native Ollama already uses the GPU,
and containers can reach it. The URL is configurable (`OLLAMA_BASE_URL`), so on
Linux or in CI it can point anywhere.

### 1.3 Resource budget

**VRAM (8 GB), measured 2026-10-11** (`scripts/bench_llm.py`; 4.8k-token prompt with the
ReviewOut schema, cold runs, nothing else on the GPU):

| `qwen3:8b` setting | Model + KV | In VRAM | Prompt | Decode |
|---|---|---|---|---|
| **8k ctx, fp16 KV (default, no server settings)** | 5.8 GiB | 100% | ~1,720–1,970 tok/s | **~31–36 tok/s** |
| 8k ctx, flash attention, fp16 KV | 5.8 GiB | 100% | ~1,745 tok/s | ~31 tok/s |
| 8k ctx, flash attention, q8_0 KV | 5.2 GiB | 100% | ~1,680 tok/s | ~31 tok/s |
| **16k ctx, flash attention, q8_0 KV** | **5.8 GiB** | **100%** | ~1,700 tok/s | **~31 tok/s** |
| 16k ctx, fp16 KV | 7.3 GiB | 80% (spills to CPU) | ~1,490 tok/s | 15–18 tok/s |
| 16k ctx, q8_0, `OLLAMA_NUM_PARALLEL=4` | 10.1 GiB | 58% | 827 tok/s | 8 tok/s |

So: **8k works out of the box; 16k needs `OLLAMA_FLASH_ATTENTION=1` +
`OLLAMA_KV_CACHE_TYPE=q8_0`** on the Ollama server, and then costs nothing. Keep
`OLLAMA_NUM_PARALLEL=1`: every parallel slot gets its own full KV cache.

**Embedder beside the LLM:** `qwen3-embedding:0.6b` at Ollama's default 4096-token context
is 2.4 GiB, and with the LLM that overflows 8 GB (decode fell to 8 tok/s). At
`EMBED_NUM_CTX=2048` it's 2.0 GiB and both stay 100% in VRAM, with the LLM unaffected
(30.6 tok/s). Query embeddings therefore run **on the GPU**. On CPU, 24 review-sized
queries took 187 s against 9.6 s on GPU (D62, replacing D57's CPU choice).

Indexing and reviewing go to separate Celery queues, so they don't fight over the GPU
(§5.10).

**Docker RAM (8 GB):** postgres ~1 GB · redis ~100 MB · api ~300 MB · worker ~1 GB
(tree-sitter, NetworkX) · reranker ~1.5 GB · ~4 GB headroom.

### 1.4 Toolchain

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 (containers + local `.venv`) | The host also has 3.13; uv picks 3.12 via `.python-version` |
| uv | 0.13.0 (pinned in Dockerfiles) | **Not installed globally yet**: install it (README) |
| Node / pnpm | 22 / 9.15 | `apps/web` |
| Next.js | 16.4 | App Router |
| Postgres / pgvector | 16 / 0.8.7 | 0.8+ needed for HNSW iterative scans |
| Redis | 7 | |
| Ollama | 0.40.2 | Structured outputs (`format: <json schema>`), `think` toggle |
| Docker Compose | v5.5 | |

### 1.5 Known environment quirks

- **Docker Desktop won't start** ("rename … .sock.stale: The file cannot be accessed by
  the system"). This is an Insider-build AF_UNIX bug. Fix: Quit Docker (never "Reset to
  factory defaults"), rename `%LOCALAPPDATA%\Docker\run` and
  `%LOCALAPPDATA%\docker-secrets-engine` aside, then relaunch.
- **Native Postgres 18 owns port 5432**, which is why compose publishes 5433.
- **PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM.** Use editors or tools
  that write UTF-8 without one.

### 1.6 Environments

| Env | Purpose | LLM / embeddings |
|---|---|---|
| **local** | Development, evals, real reviews | Ollama on the GPU |
| **ci** (GitHub Actions) | Lint, unit + integration tests, parser/retrieval tests | **No models.** Deterministic fake embedder/LLM (§12) |
| **demo** (hosted) | Public read-only showcase | None. Serves reviews generated locally (§15) |

---

## 2. Architecture decision: "naive+", not naive

### 2.1 The spectrum

| Level | What it is | Example | Verdict |
|---|---|---|---|
| **Naive RAG** | Chunk → embed → top-k by cosine → stuff into prompt | "vector search over the repo, send top-5 to the LLM" | Baseline only. Misses callers (they rarely *look like* the change), and is weak on exact identifiers |
| **naive+** ✅ | Still **one fixed pass**, but each stage is better: structure-aware chunks, hybrid retrieval, graph expansion, rerank, budgeted packing, output validation | This project | Big quality gains for little complexity; deterministic and measurable |
| Advanced / modular RAG | LLM-driven query rewriting, HyDE, routing, multi-query, iterative retrieval | "LLM writes 5 search queries, then retrieves again" | Extra LLM calls are expensive on a local 8B model; nondeterministic; hard to attribute gains |
| Agentic | LLM in a loop with tools ("open file", "find callers") deciding what to read | Coding agents | Small local models are unreliable at tool use; latency is unbounded; hardest to evaluate |

### 2.2 Definition of naive+ for CodeLens

1. **Fixed stage order, no loops.** `seed → retrieve → expand → fuse → rerank → pack →
   generate → validate`. The LLM is called only in `generate`, once per chunk of the PR.
2. **Each stage is a typed function** (Pydantic in, Pydantic out) that can be tested and
   inspected on its own. Every stage's output is saved to the review trace.
3. **Every upgrade is a toggle** in the pipeline config. An ablation is just a different
   config file over the same code path, so ablations can't drift from production.
4. **Adoption rule:** an upgrade becomes default only if, on the eval set, it improves
   the primary metric (finding F1) with a 95% bootstrap CI above zero, doesn't raise
   the clean-PR noise rate, and stays within the latency budget (§10.5). Rejected
   upgrades stay in the code behind their flag, and their results are recorded.

### 2.3 The upgrades ("the +")

| ID | Upgrade | Hypothesis | Phase |
|---|---|---|---|
| B0 | **Baseline:** AST-node chunks, vector top-k, diff + changed nodes, structured output + validation | Already better than diff-only because chunks are whole functions | 2 |
| U1 | Full-text search over split identifiers + **RRF** fusion | Catches exact-name matches that embeddings blur | 4 |
| U2 | **Graph expansion** (callers/callees/parents, 1–2 hops, hub control) | Finds the dependants that diff-only and vector search miss; biggest expected gain | 4 |
| U3 | **Cross-encoder rerank** of ~100 candidates | Precision of the final top-k | 4 |
| U4 | Centrality prior (PageRank) in the final score | Important code first when the budget is tight | 4 |
| U5 | Anti-pattern retrieval | Team-specific findings | 4 |
| U6 | Qwen3 **thinking mode** in generation | Better reasoning vs. 3–5× more output tokens | 4 |
| U7 | Sub-chunking of long functions | Only if evals show misses on long functions | stretch |

Not planned (and why): query rewriting and HyDE (extra LLM calls, where the diff
already *is* a precise query), multi-agent review, self-critique loops (doubles
latency for uncertain gain), fine-tuning.

---

## 3. Tech stack

| Layer | Choice | Why | Alternative considered |
|---|---|---|---|
| Language (backend) | Python 3.12 | Ecosystem for tree-sitter, NetworkX, ML clients | — |
| API | FastAPI + Pydantic v2 | Typed I/O, OpenAPI for free, async | Litestar |
| SSE | sse-starlette | Mature SSE for Starlette/FastAPI | Raw `StreamingResponse` |
| ORM / DB access | SQLAlchemy 2 async + asyncpg | Typed models, raw-SQL escape hatch for retrieval queries | SQLModel; raw asyncpg |
| Migrations | Alembic | Standard with SQLAlchemy | — |
| Jobs | Celery + Redis | Retries, chains/groups, routing to separate queues | RQ, Arq, Dramatiq |
| Parsing | py-tree-sitter 0.25.x + official grammar wheels (`tree-sitter-python`, `-javascript`, `-typescript`) | Fast, error-tolerant, concrete syntax tree; grammars hash-pinned in `uv.lock` (D31, D32) | Python `ast` (Python only, fails on invalid code); tree-sitter-language-pack (downloads grammars at runtime) |
| Graph analysis | NetworkX | PageRank in one call; graph sizes are small | igraph (faster, less readable) |
| Database | PostgreSQL 16 + pgvector 0.8 (HNSW) + tsvector (GIN) | One store for everything (D1) | Qdrant + Neo4j |
| LLM runtime | **Ollama** (native, GPU) | Free, local, structured outputs, already installed | llama.cpp server, vLLM (Linux only) |
| LLM model | **qwen3:8b** (default), qwen3:14b (offline quality runs) | Strong code reasoning at 8B; fits VRAM; JSON-schema outputs | llama3.1:8b, qwen2.5-coder:7b |
| Embedding model | **qwen3-embedding:0.6b** (1024-d) | Code-capable, instruction-aware, 32k context, small, matches `EMBED_DIM` | bge-m3 (1024-d), jina-v2-base-code (768-d) |
| Reranker | **bge-reranker-base** (278M) via sentence-transformers `CrossEncoder`, CPU | Small, fast enough on CPU, a well-known cross-encoder; ~40 lines of service code we write ourselves | bge-reranker-v2-m3 (568M, stronger, slower); HF TEI server |
| HTTP clients | httpx (async) | Ollama, reranker, GitHub API | aiohttp |
| GitHub | GitHub App (REST v3) + PyJWT for the App JWT | Fine-grained permissions, installation tokens | OAuth app + PAT (weaker security story) |
| Frontend | Next.js 16 (App Router) + TypeScript + Tailwind + shadcn/ui | Standard, fast to build | — |
| Frontend extras | React Flow (`@xyflow/react`, graph; laid out by our own columns, not dagre); the diff view is our own parser (6.4) instead of react-diff-view; GitHub login is our own code with `jose` (D113, instead of Auth.js) | One library per job | Cytoscape.js (graph) |
| Tooling | uv, ruff, pytest, pnpm, pre-commit, Docker Compose, GitHub Actions | Fast, standard | — |
| Observability | Own trace tables first; Langfuse optional later (§13) | Keeps code local; the trace view is a feature | Langfuse from day 1 |

---

## 4. Data model

### 4.1 Current schema (migration `f9a05e4be254`, applied)

```
repos ─┬─< files ─< code_nodes >─< code_edges (src_node_id, dst_node_id → code_nodes)
       ├─< anti_patterns            (repo_id NULL = global)
       └─< reviews ─< findings      (evidence_node_ids BIGINT[])
```

| Table | Key columns |
|---|---|
| `repos` | `id`, `github_id?` (unique), `full_name` (unique), `default_branch`, `last_indexed_sha?`, `status` (pending/indexing/ready/failed), timestamps |
| `files` | `id`, `repo_id`, `path`, `language`, `content_hash`; `UNIQUE(repo_id, path)` |
| `code_nodes` | `id`, `repo_id`, `file_id`, `kind` (module/class/function/method), `name`, `qualified_name`, `signature?`, `start_line`, `end_line`, `code`, `docstring?`, `content_hash`, `centrality?`, `embedding vector(1024)?`, `tsv?`. Indexes: HNSW cosine on `embedding`, GIN on `tsv`, btree `(repo_id, qualified_name)`, btree `file_id` |
| `code_edges` | `id`, `repo_id`, `src_node_id`, `dst_node_id?` (`ON DELETE SET NULL`), `kind` (calls/imports/inherits/contains), `target_name`. Indexes: `src_node_id`, `dst_node_id`, partial `(repo_id, target_name) WHERE dst_node_id IS NULL` |
| `anti_patterns` | `id`, `repo_id?`, `title`, `description`, `example_code?`, `severity`, `embedding?`, `occurrences`, timestamps; HNSW on `embedding` |
| `reviews` | `id`, `repo_id`, `pr_number`, `head_sha`, `status`, `started_at?`, `finished_at?`, `model?`, `token_usage` JSONB, `created_at` |
| `findings` | `id`, `review_id`, `file_path`, `line_start`, `line_end`, `severity`, `category`, `title`, `explanation`, `suggested_fix?`, `evidence_node_ids BIGINT[]` |

### 4.2 Planned changes (one migration per phase; never edit an applied migration)

| Phase | Table | Change | Why |
|---|---|---|---|
| 1 (step 4) ✅ | `code_nodes` | + `ordinal SMALLINT NOT NULL DEFAULT 0`; `UNIQUE(file_id, qualified_name, ordinal)` (replaces the `file_id` index) | Identity key for node-level diffing when names repeat in a file. (`kind` is not part of the key: ordinals are counted per qualified name) |
| 1 (step 4) ✅ | `files` | + `is_test BOOLEAN NOT NULL DEFAULT false`, + `size_bytes INT` | Down-weight tests in retrieval; skip-rule auditing |
| 1 (step 4) ✅ | `repos` | + `source` (`github`/`local`), + `local_path TEXT?` | Local CLI repos |
| 1 (step 4) ✅ | **`index_runs`** | `id, repo_id, commit_sha?, started_at, finished_at?, status, stats JSONB` (files scanned/changed, nodes/edges by kind, % resolved, durations) | Index history; the CLI summary is persisted too |
| 2 ✅ | `code_nodes`, `anti_patterns` | + `embedding_model TEXT?`, + `embedded_hash TEXT?` (migration `a3a340858867`) | Re-embed exactly when the model or the chunk text changes. The review/trace rows below land in Phase 2 step 5, where they're first used |
| 2 | `reviews` | + `base_sha`, + `source` (`github`/`cli`/`eval`), + `pipeline_config JSONB`, `pr_number` nullable | CLI and eval runs are reviews too; exact config is recorded for reproducibility |
| 2 | `findings` | + `confidence REAL`, + `fingerprint TEXT`, + `evidence_refs JSONB` (node IDs + anti-pattern IDs), + `status` (`posted`/`dropped`), + `drop_reason?` | Validation output, dedupe across pushes, a trace of dropped findings |
| 2 | **`review_context_items`** | `review_id, chunk_index, item_type (node/anti_pattern), item_id, stage_scores JSONB (vector_rank, fts_rank, rrf, graph_depth, rerank, final), included BOOL, token_cost INT` | The "why was this context used?" trace; eval analysis |
| 2 | **`review_events`** | `id, review_id, seq, type, payload JSONB, created_at` | SSE replay and the progress timeline |
| 4 | `anti_patterns` | + `slug TEXT` (unique per repo/global), + `language?`, + `category` | Upsert from YAML; filtering |
| 5 ✅ | **`installations`** | `id, github_installation_id, account_id, account_login, account_type, repository_selection?, suspended_at?`, timestamps + `repos.installation_id` (`SET NULL`) (migration `f93b1ce2b1ca`) | GitHub App |
| 5 ✅ | **`webhook_deliveries`** | `delivery_id PK, event, action?, installation_id?, received_at, processed_at?` | Idempotent webhook handling (D95) |
| 6 ✅ | **`finding_feedback`** | `finding_id, github_user_id, vote (+1/-1)`, timestamps; unique per user (migration `b474f5043e49`) | 👍/👎 and suppression |
| 6 ✅ | `reviews` | + `diff TEXT`, + `seeds JSONB` (same migration) | What was reviewed, kept with the review (D110) |

### 4.3 Invariants (enforced by constraints or tests)

- Every `code_nodes.repo_id` equals its `file.repo_id`. The indexer guarantees this,
  and a test checks it.
- `code_edges.target_name` is always set. `dst_node_id IS NULL` ⇔ unresolved.
- `contains` edges are always resolved (parent and child come from the same parse).
- Findings with `status = posted` always have line ranges inside the PR diff.

---

## 5. Indexing pipeline (offline)

```
source ─► walk & filter ─► parse (tree-sitter) ─► resolve ─► persist (incremental)
                                                                │
          ┌──────────────────────────┬──────────────────────────┼─────────────────┐
          ▼                          ▼                          ▼                 ▼
    re-link edges             build tsv (lexical)        embed chunks       PageRank
                                                         (Ollama)           (NetworkX)
```

### 5.1 Source acquisition
- **Local (Phase 1–4):** `python -m core.index <path>` reads a working tree. With
  `--rev <sha|branch>`, it reads files via `git show <rev>:<path>`, so it can index a
  revision without checking it out.
- **GitHub (Phase 5):** the worker keeps a bare mirror per repo under
  `DATA_DIR/repos/<repo_id>.git`. It runs `git fetch` with an installation token, then
  reads blobs at a SHA (no working tree, so no hooks or checkout side effects). Clone
  flags: `--mirror --filter=blob:none`, no submodules, `core.hooksPath=/dev/null`.
  *As built (5.3a, D99):* `git init --bare --template=` once, then
  `git fetch --prune --no-tags <url> +refs/heads/*:refs/heads/*` with all objects (no
  partial clone); the token travels in `GIT_CONFIG_*` environment variables only.

### 5.2 Walk and filter
- Respect `.gitignore` (use `git ls-files` when it's a git repo; otherwise apply
  `pathspec` with the gitignore rules).
- Always skip: `node_modules/ .venv/ venv/ env/ dist/ build/ .git/ __pycache__/ .next/
  coverage/ vendor/ third_party/ site-packages/ .tox/ .mypy_cache/`.
- Skip files over **512 KB**, binary files (NUL byte in the first 8 KB), minified files
  (average line length > 300 or `*.min.js`), lockfiles, and `*.d.ts` (by default).
- `is_test` = the path matches `tests/`, `test_*.py`, `*_test.py`, `*.test.ts(x)`,
  `*.spec.ts(x)`, or `__tests__/`.
- Language by extension: Python `.py`; JavaScript `.js .jsx .mjs .cjs`; TypeScript
  `.ts .tsx`.

### 5.3 Parse (tree-sitter)
Output is plain frozen dataclasses with **no DB dependency** (`core/parser/types.py`):
`ParsedFile(path, language, content_hash, is_test, nodes, raw_edges, imports)`.

What is extracted, per language, is specified in `idea.md` §6.1 (nodes:
module/class/function/method; edges: contains/inherits/imports/calls; docstrings and
signatures). Additional rules:
- Line numbers are **1-based and inclusive**. A node's `code` is its exact source
  slice, including decorators (Python) or the `export` keyword (JS/TS).
- Nested functions are nodes (kind `function`, qualified as
  `outer.<locals>.inner`, the same convention Python uses).
- Lambdas and anonymous callbacks are **not** nodes. Their calls are attributed to the
  enclosing named node.
- Syntax errors: tree-sitter still produces a tree. We index what parses and log the
  error count per file.

### 5.4 Resolve
Best-effort, deterministic resolution order (local scope → `self`/`this` + bases →
same module → imports → unresolved), as specified in `idea.md` §6.2. The import table
maps local aliases to qualified targets per file. JS/TS module specifiers resolve
`./x` → `x.ts | x.tsx | x.js | x.jsx | x/index.*`. Cross-file resolution needs every
file's symbol table, so it runs **after** all changed files are parsed, against
the union of new nodes and existing DB nodes.

### 5.5 Persist (incremental and idempotent)
For each file, in **its own transaction**:

| File state | Action |
|---|---|
| `content_hash` unchanged | Skip. **Zero writes.** |
| New | Insert file, nodes, outgoing edges |
| Changed | Node-level diff by `(qualified_name, kind, ordinal)`: same `content_hash` → keep the row; changed → update it (embedding/tsv become stale); missing → delete (incoming edges → `SET NULL`); new → insert. Then replace all outgoing edges of the file's nodes |
| Deleted from the repo | Delete the file row (cascades to nodes; incoming edges → `SET NULL`) |

Then, once per run: the **re-link pass**
(`UPDATE code_edges SET dst_node_id = n.id FROM code_nodes n WHERE dst_node_id IS NULL
AND n.repo_id = … AND n.qualified_name = target_name`, taking the lowest `ordinal` on
duplicates). Then write an `index_runs` row with stats and print the summary.

### 5.6 Chunking
**A chunk is a code-graph node.** Chunks follow the AST, not fixed-size windows.

| Node kind | Chunk text used for embedding (and FTS) | Notes |
|---|---|---|
| function / method | Context header + full source (signature, docstring, body) | The workhorse |
| class | Context header + class header + docstring + class-level assignments + **member signatures only** (no method bodies) | Method bodies are their own chunks, so they aren't duplicated |
| module | Context header + module docstring + import list + top-level names + top-level statements outside any def (first ~60 lines) | Captures config/constants/route registrations |

**Context header** (a cheap version of "contextual chunk embeddings"): it gives the
vector the "where" and "what", not just the body:

```
# file: billing/invoice.py
# symbol: billing.invoice.InvoiceService.charge (method of InvoiceService)
# class: class InvoiceService(BaseService):
```

**Size policy:** cap the chunk text at **`EMBED_MAX_TOKENS = 1024`** tokens
(approximated as chars ÷ 3.5, which is conservative for code). Over the cap, keep the
header, signature, docstring and the first part of the body, then a `# … truncated`
marker. The share of truncated chunks is logged per index run. If evals show misses
on long functions, U7 adds a `node_chunks` table with statement-boundary sub-chunks.

Why not fixed-size line windows: they cut functions in half, mix unrelated code into
one vector, and break the 1:1 join between a vector hit and a graph node, which U2
graph expansion depends on.

### 5.7 Embedding
- **Model:** `qwen3-embedding:0.6b` through Ollama `POST /api/embed` (it accepts a
  batch `input` array). Output 1024-d, **L2-normalised** by us before storing, so cosine
  distance equals 1 − dot product.
- **Asymmetric prompts:** documents are embedded as-is. Queries get an instruction
  prefix (Qwen3-Embedding is instruction-aware):
  `Instruct: Given a code change, retrieve code that it depends on or that depends on it\nQuery: <query text>`.
  The `Embedder` interface has `kind="query" | "document"` for exactly this.
- **What gets embedded:** nodes where `embedding IS NULL`, or `embedding_model` ≠
  current model, or `embedded_hash` ≠ the sha256 of the current chunk text. Re-running
  is therefore a no-op. The chunk-text hash is the *only* staleness test (D47): the
  indexer never clears embeddings.
- **Batching:** 32 chunks per request; one in-flight request. Retries with exponential
  backoff on 5xx.
- **Throughput (measured 2026-10-11, RTX 4060, `scripts/bench_embed.py`):** the raw
  ceiling is **~40 chunks/s ≈ 5.3k tokens/s**, and it doesn't move: batch sizes
  8/32/64/128 × 1/2/4 concurrent requests all land at 34–42 chunks/s, and
  `OLLAMA_NUM_PARALLEL=4` didn't help (36–37/s). Ollama evidently processes embedding
  inputs one at a time, so no client-side tuning is worth adding. The `core.embed` job
  end to end (model load, chunk building, DB writes) runs at ~25–29 nodes/s, so ~8–9
  min for a 14k-node repo, once per repo; re-runs embed only stale nodes.
- **Celery:** `embed_repo` fans out into `embed_batch(node_ids)` tasks of 256 nodes.
  Each is idempotent, so a crash resumes where it stopped.

### 5.8 Lexical index (tsv)
Identifier splitting in Python (`core/text/identifiers.py`):

```
getUserById → getuserbyid get user by id      HTTPServer → httpserver http server
parse_repo  → parse_repo parse repo           __init__   → init
```

Stored as weighted tsvectors with the `simple` config:
`setweight(name parts, 'A') || setweight(signature + docstring, 'B') || setweight(identifiers in body, 'C')`.
Updated in the same pass as embeddings, only for nodes whose `content_hash` changed.

### 5.9 Graph analysis
After each index run: load the resolved `calls` + `inherits` edges into a
`networkx.DiGraph`, run `pagerank(alpha=0.85)`, normalise to [0, 1], and bulk-update
`centrality`. Expected time is a few seconds for 10⁵ edges.

### 5.10 Job orchestration (Celery, Phase 5)

```
index_repo(repo_id, sha) = chain(
    sync_source,                 # fetch mirror / read local tree
    parse_and_persist,           # per-file transactions, internally batched
    relink_edges,
    build_tsv,
    group(embed_batch × N),      # queue: gpu
    compute_pagerank,
    mark_repo_ready,
)
review_pr(review_id) = single task running the query pipeline (§6), queue: review
```

**As built (Phase 5 step 1, D88–D91):** the core jobs are not split that finely yet
(`index_repository` does parse, persist and re-link in one call; tsv and PageRank arrive
with U1/U4), so a re-index is `chain(index_repo, embed_repo)`, and a review is one task.
Names and routes live in `core/jobs.py`.

| Queue | Concurrency | Tasks |
|---|---|---|
| `index` | 2 | `index_repo` (parse, persist, re-link), `ping` |
| `gpu` | **1** | `embed_repo` and whole reviews (`review`). Serialising them prevents VRAM thrash |

There is no separate `review` queue (D88): a review is GPU-bound end to end, and a task
that waits on another task's result can deadlock the pool.

Each repo has a lock: the Postgres advisory lock the indexer already takes (D41), not a
Redis `SET NX` with a TTL (D89). An index task that finds it held retries every 30 s.

### 5.11 Anti-pattern library
- **Sources:** global patterns in `anti_patterns/*.yml` (in this repo); team patterns
  in the reviewed repo's `.codelens/anti-patterns.yml`.
- **Format:** `slug, title, description, severity, category, language?, example_bad,
  example_good?`.
- **Sync:** upsert by `(repo_id, slug)`, embed `title + description + example_bad`, and
  delete rows that disappeared from the YAML.
- **Seed set:** ~25 global patterns, e.g. mutable default argument, bare `except:`,
  `await` missing on a coroutine, raise → return-None contract change, SQL built with
  f-strings, `==` vs `===`, unhandled promise, `forEach` with async callback, swallowed
  errors, N+1 query in a loop, shared mutable state across requests, time-of-check
  vs time-of-use on files.

---

## 6. Query pipeline (review time)

### 6.1 Stages

```
S0 intake ─► S1 diff ─► S2 seeds ─► S3 retrieve ─► S4 expand ─► S5 fuse
                                     (vector, +FTS)  (+graph)     (+RRF)
   ─► S6 rerank ─► S7 pack ─► S8 generate ─► S9 validate ─► S10 persist & deliver
      (+cross-enc)  (budget)   (Ollama)       (guards)        (DB, SSE, GitHub)
```

| Stage | Input → Output | Details | Toggle |
|---|---|---|---|
| **S0 intake** | trigger → `ReviewJob` | Sources: CLI (`--base --head`), eval runner, GitHub webhook. Creates the `reviews` row, then emits `stage` events throughout | — |
| **S1 diff** | base/head → `list[FileDiff]` | Unified diff parsed into hunks with right-side added/modified line sets. Files filtered by skip rules and `.codelens.yml`; deleted files noted | — |
| **S2 seeds** | diffs → `list[Seed]` | Parse changed files **at head in memory** (the overlay). Seeds = head nodes intersecting changed lines (added/modified), plus base nodes missing at head (deleted). For each seed: `change_size`, `signature_changed` | — |
| **S3 retrieve** | seeds → candidates | **Vector:** the query text (seed code + hunk) is embedded on CPU; top 30 per seed within the repo, excluding the seed itself. **FTS (U1):** `websearch_to_tsquery('simple', <identifiers in hunk>)`, top 30 | `fts` |
| **S4 expand** (U2) | seed IDs → nodes + depth + direction | Recursive CTE over `calls`/`inherits` (both directions) + `contains` parent; `max_depth` 1 (try 2); ≤ 15 neighbours per node per hop by centrality; don't expand *through* nodes with degree > 50 | `graph` |
| **S5 fuse** | ranked lists → one list | Baseline: vector order. With U1: **RRF** (k = 60) over vector + FTS (+ graph as a third list ranked by depth, then centrality) | `rrf` |
| **S6 rerank** (U3) | top 100 → scored | Cross-encoder scores `(seed hunk, candidate chunk text)` pairs on CPU. Final score = weighted blend (rerank, graph proximity, centrality, caller-of-changed-signature flag); weights live in config and are tuned on the eval set | `rerank` |
| **S7 pack** | scored items → `ContextPack` per chunk | Group PR files into **review chunks** (by directory, ≤ budget). Greedy fill by score; if an item doesn't fit, include just its signature and docstring; dedupe a method already covered by its class | — |
| **S8 generate** | `ContextPack` → `ReviewOut` | Ollama `/api/chat`, `format = ReviewOut JSON schema`, `think` per U6, `temperature 0.1`, fixed `seed`, `num_ctx 16384`. One call per review chunk, run serially on the `gpu` queue | `think` |
| **S9 validate** | raw findings → posted/dropped | Lines ∩ added/modified lines; evidence IDs ⊆ provided; confidence ≥ threshold; dedupe (fingerprint); `.codelens.yml` filters; cap N; drop reasons recorded | — |
| **S10 deliver** | findings → DB, SSE, GitHub | Persist findings + `review_context_items` + `token_usage`; publish `finding`/`done`; post a GitHub review (Phase 5) | — |

### 6.2 Token budget per LLM call (`num_ctx = 16384`)

| Section | Budget (tokens) |
|---|---|
| System prompt (rubric, severities, rules) | 1,000 |
| PR title + description (truncated) | 400 |
| Diff for this chunk | ≤ 4,000 |
| Changed nodes (head versions) | ≤ 3,500 |
| Retrieved context | ≤ 4,000 |
| Anti-patterns | ≤ 800 |
| **Reserved for output** | 2,000 (non-thinking) |
| **Total** | ≤ 15,700 |

If the diff alone exceeds its budget, the chunk is split further. A PR of more than
`MAX_REVIEW_CHUNKS` (default 4) chunks gets a summary-only review covering the
highest-risk files (largest change × centrality).

### 6.3 Prompt and output contract
- Untrusted content (diff, code, PR text) goes inside tagged blocks
  (`<diff>`, `<context id="N12">`). The system prompt states that everything inside
  them is data.
- Each context item is labelled `[N<id>]` (node) or `[AP<id>]` (anti-pattern) so the
  model can cite evidence.
- Output schema (Pydantic → JSON Schema → Ollama `format`):
  `ReviewOut{summary, findings: [FindingOut{file_path, line_start, line_end, severity,
  category, title, explanation, suggested_fix?, evidence: [str], confidence}]}`.
- **Repair:** if validation of the JSON itself fails, retry once with the error
  message appended; after that, fail the chunk (recorded) and continue with the others.

### 6.4 Latency (measured 2026-10-11)

A real PR (this repo's Phase 2 step 4: 13 files, 84 seeds, of which 24 were used),
`b0` at an 8k window, RTX 4060:

| Stage | Before step 6 fixes | After |
|---|---|---|
| Changes (git + both-side parses) | 0.8 s | 0.8 s |
| Retrieval (24 query embeddings + 24 searches) | 44.1 s (CPU embedding) | **4.8 s** (GPU) |
| Generation (4 chunks) | 122 s | 155 s |
| **Wall clock** | 169 s | 163 s |
| Context blocks shown to the model | 3 | 11 |

Per chunk (from the `chunk_done` events): **~4.3–5.7k real prompt tokens, ~1.1k output
tokens, 37–42 s**. Prompt processing is ~3 s; the rest is decoding at ~30 tok/s. The model
emitted the maximum 8 findings in every chunk (~140 tokens each), so **output length is
the latency driver**. A lower per-chunk cap is both faster and possibly more precise:
open question Q9, to be decided by evals.

The token estimate (chars ÷ 3.0) now over-counts real prompts by 6–14%, so no prompt can
silently exceed the window (D63).

**End to end on GitHub (Phase 5 step 5, 2026-10-13).** The whole stack ran in Docker, with
Ollama native and a smee relay, on a private copy of sqlite-utils (`codelens-playground`, 108
files, 1,337 nodes). The times are from GitHub's own timestamps: PR opened (or pushed) →
review posted.

| PR | Change | Open/push → posted | What CodeLens posted |
|---|---|---|---|
| #1 | `sql_format` bug, 1 file, opened while the repo was still embedding | **25 s** (waited 6 s for an embedding slice, D107) | 🔴 critical · security, on the changed line |
| #2 | `rename_local` (behaviour-preserving) | **9 s** | a summary, no findings |
| #3 | 4 files, 4 planted bugs (`raise_to_none`, `remove_guard`, `reorder_params`, `exception_type`) | **33 s** (queued behind #2) | all 4 found; 2 inline, 2 in the body (fixed: D109) |
| #3, 2nd push | README only | **≈33 s** | "No new findings"; 4 not repeated (D104) |
| #4 | #3's change again, after the placement fix | **49 s** (queued behind #3's push) | all 4 inline |

The worst case, 49 s, is ~8% of the 10-minute budget (D84). Single-PR reviews take 7–30 s of
GPU at this size, and a review queued behind another adds that one's time. The earlier
fashion-store reviews took 33–135 s; the slow ones coincided with the laptop GPU's
power-capped state (§1.3).

---

## 7. API surface

**Base path:** `/api/v1`, except `/health` and `/webhooks/*`. JSON in and out, Pydantic
schemas in `api/schemas/`. Errors use the shape
`{"error": {"code": "not_found", "message": "...", "details": {...}}}`.

**Auth:** the dashboard's Next.js server calls the API with `Authorization: Bearer <JWT>`
(HS256, shared `API_JWT_SECRET`, 5-minute expiry, claims: `sub` = GitHub user ID,
`login`). The API checks repo access against installation membership. Webhooks are
authenticated by HMAC. With `DEMO_MODE=true`, all mutating endpoints return `403`.
*As built in 5.1 (D92):* tokens are verified (secret ≥ 32 bytes; unset = every request is
refused), and `python -m api.token` mints one for development. The per-repo access check
moved to Phase 6 (D101): the dashboard's GitHub login puts the user's installation ids
in the JWT (D111, 6.1), and the dashboard's server signs a fresh token for every call
(6.2, D113).

| Method | Path | Auth | Purpose | Phase |
|---|---|---|---|---|
| GET | `/health` | none | Readiness: DB + Redis (+ Ollama, reranker once used) | ✅ 1 |
| POST | `/webhooks/github` | HMAC | `installation*`, `push`, `pull_request` events → `202` + enqueue | ✅ 5.2 (verify, dedupe, mirror `installation`; others recorded) · enqueue in 5.3 |
| GET | `/api/v1/repos` | JWT | Repos visible to the user (status, last index, last review) | ✅ 5.1 |
| GET | `/api/v1/repos/{repo_id}` | JWT | Repo detail + latest index stats | ✅ 5.1 |
| POST | `/api/v1/repos/{repo_id}/index` | JWT | Trigger a re-index, then embed → `202 {repo_id, status}` (the run row is created when the job starts; poll index-runs) | ✅ 5.1 (local repos), 5.3a (GitHub repos of an installation) |
| GET | `/api/v1/repos/{repo_id}/index-runs` | JWT | Index history (`?cursor=&limit=`) | ✅ 5.1 |
| GET | `/api/v1/repos/{repo_id}/reviews` | JWT | Paginated reviews (`?status=&cursor=&limit=`), with posted counts by severity | ✅ 5.1 |
| POST | `/api/v1/reviews` | JWT | Manual review `{repo_id, pr_number}` or `{repo_id, base, head}` → `202 {review_id}` | ✅ 5.1 `{repo_id, base, head}` (local repos); `pr_number` in 5.3 |
| GET | `/api/v1/reviews/{review_id}` | JWT | Review + findings (posted; `?include_dropped=true`) | ✅ 5.1 |
| GET | `/api/v1/reviews/{review_id}/events` | JWT | **SSE** stream (replay + live; `Last-Event-ID` or `?after=`) | ✅ 5.4 |
| GET | `/api/v1/reviews/{review_id}/diff` | JWT | Unified diff for the viewer, + the changed nodes (stored with the review, D110) | ✅ 6.1 |
| GET | `/api/v1/reviews/{review_id}/graph` | JWT | Nodes + edges around the seeds for React Flow `{nodes:[{id,label,kind,depth,centrality,is_seed}], edges:[{src,dst,kind,resolved}]}` | ✅ 6.1 (`in_context` instead of `centrality`, which U4 hasn't computed; resolved edges only) |
| GET | `/api/v1/reviews/{review_id}/trace` | JWT | Per-chunk context items with all stage scores; dropped findings with reasons | ✅ 6.1 (+ the packing budgets; dropped findings: `GET /reviews/{id}?include_dropped=true`) |
| GET | `/api/v1/nodes/{node_id}` | JWT | Node code + metadata (evidence drawer) | ✅ 6.1 |
| POST | `/api/v1/findings/{finding_id}/feedback` | JWT | `{vote: 1 \| -1}` | ✅ 6.1 (+ `DELETE` to take a vote back; votes and `my_vote` on every finding of `GET /reviews/{id}`) |
| GET / POST / PATCH / DELETE | `/api/v1/anti-patterns[/{id}]` | JWT | Library CRUD (`?repo_id=` or global) | deferred with U5 (D112) |
| GET | `/api/v1/repos/{repo_id}/search?q=&mode=vector\|fts\|hybrid` | JWT | Retrieval debugging (also a demo feature) | 4 |

**SSE events** (`event:` name, `data:` JSON, `id:` = `review_events.seq`):

| event | data |
|---|---|
| `stage` | `{stage: "retrieving", chunk: 1, of: 2}` |
| `finding` | the `Finding` schema |
| `progress` | `{chunks_done, chunks_total}` |
| `done` | `{status, counts: {critical, warning, suggestion}, duration_ms, tokens}` |
| `error` | `{code, message}` |

A heartbeat comment line is sent every 15 s.

*As built (5.4):* the stream forwards the `review_events` rows exactly as recorded, so the
event names are the recorder's. The `finding` and `progress` events above don't exist:
findings are fetched with `GET /reviews/{id}` once the review is done.

| event | data |
|---|---|
| `stage` | `{stage: "seeds", files, seeds}` · `{stage: "packed", chunks, budgets, left_out}` · `{stage: "generating", chunk, of, prompt_tokens}` |
| `chunk_done` | `{chunk, findings, input_tokens, output_tokens, llm_ms, estimated_prompt_tokens}` |
| `chunk_failed` / `think_fallback` | `{chunk, error}` |
| `verified` | the verification pass's verdict per finding (off by default) |
| `done` | `{status, posted, dropped}`: the pipeline finished |
| `published` | `{github_review_id, new, repeated, inline}` (GitHub PRs) |
| `superseded` | `{by_review, when}` |
| `error` | `{message}`: the worker's review failed |
| `end` | `{status}`: the last event of every review; the server closes the stream after it (D106) |

**CLIs** (the internal "API" for Phases 1–4; all go through the same `core` code):

```
python -m core.index   <path> [--rev REV] [--repo-name NAME]
python -m core.embed   <repo>                       # Phase 2
python -m core.search  <repo> "query" [--mode hybrid] [--k 20]   # Phase 2/4
python -m core.review  <path> --base main --head feature [--config configs/pipeline/x.yaml] [--json]
python -m core.anti_patterns sync [--repo <repo>]
python -m evals.run    --dataset seeded --config configs/pipeline/b0.yaml [--limit N]
python -m evals.report evals/results/<run_id>.json
```

---

## 8. Project layout

Legend: ✅ exists · 🔜 next · 📋 planned (phase).

```
codelens/
├── idea.md                         ✅ vision & design narrative
├── planning.md                     ✅ this file: plan of record
├── architecture.md                 ✅ how the system works now (as built)
├── README.md                       ✅ setup & usage
├── pyproject.toml / uv.lock        ✅ uv workspace, ruff, pytest
├── docker-compose.yml              ✅ postgres, redis, migrate, api, worker (+ reranker 📋4)
├── .env.example                    ✅
├── .github/workflows/ci.yml        ✅
├── configs/
│   └── pipeline/                   📋2 b0.yaml (baseline), u1_fts.yaml, … full.yaml, default.yaml
├── anti_patterns/                  📋4 global anti-pattern YAML library
├── apps/web/                       ✅ Next.js dashboard (6.2: login, layout, repos page)
│   ├── src/proxy.ts                ✅ early redirect when signed out
│   ├── src/app/api/auth/{login,callback,logout}/  ✅ GitHub login (D113)
│   ├── src/app/{login,repos}/      ✅
│   ├── src/app/repos/[id]/{page,indexing/page,layout,actions}  ✅ 6.3
│   ├── src/app/reviews/[id]/       ✅ 6.4 layout, header, page (diff), diff-view, finding-card, live-timeline, evidence, vote-buttons, actions · 6.5 graph/, trace/
│   ├── src/app/api/{reviews/[id]/events,nodes/[id]}/  ✅ 6.4 session-checked proxies
│   ├── src/components/             ✅ badges, pager, skeleton, auto-refresh, tabs
│   ├── src/lib/                    ✅ config, tokens, session, dal, github, oauth, paths, params, format, github-links, api, diff, events, graph-layout, trace
│   └── test/                       ✅ vitest
├── packages/core/
│   ├── alembic.ini                 ✅
│   ├── src/core/
│   │   ├── config.py               ✅ Settings (env)
│   │   ├── enums.py                ✅
│   │   ├── db/
│   │   │   ├── base.py session.py migrate.py   ✅
│   │   │   ├── models/             ✅ repo, code_graph, anti_pattern, review
│   │   │   ├── migrations/         ✅ env.py, versions/
│   │   │   └── repositories/       📋1 typed query functions (nodes, edges, files, reviews)
│   │   ├── parser/                 🔜1
│   │   │   ├── types.py            ParsedFile / ParsedNode / RawEdge dataclasses
│   │   │   ├── walker.py           file discovery + skip rules
│   │   │   ├── languages.py        extension → grammar, tree-sitter setup
│   │   │   ├── python.py           Python extractor
│   │   │   ├── javascript.py       JS/TS extractor
│   │   │   └── resolver.py         import tables + call resolution
│   │   ├── indexing/               📋1 persist.py (node-level diff), relink.py, summary.py
│   │   ├── index.py                📋1 `python -m core.index` entrypoint
│   │   ├── text/                   📋2 identifiers.py (splitting), chunk_text.py, tokens.py
│   │   ├── providers/              📋2 interfaces + implementations
│   │   │   ├── base.py             Embedder / Reranker / LLM protocols, Usage
│   │   │   ├── ollama.py           OllamaEmbedder, OllamaLLM
│   │   │   ├── reranker_http.py    📋4 client for the reranker service
│   │   │   └── fake.py             deterministic fakes for tests/CI
│   │   ├── diff/                   📋2 unified-diff parser, hunk → line sets
│   │   ├── review/                 📋2
│   │   │   ├── pipeline.py         stage orchestration (reads PipelineConfig)
│   │   │   ├── seeds.py overlay.py
│   │   │   ├── retrieval/          vector.py, fts.py 📋4, graph.py 📋4, fusion.py 📋4, rerank.py 📋4
│   │   │   ├── packing.py prompts.py schemas.py
│   │   │   ├── generate.py validate.py
│   │   │   └── trace.py            review_context_items + events writer
│   │   ├── graph/                  📋4 pagerank.py
│   │   ├── anti_patterns/          📋4 yaml loader + sync
│   │   ├── github/                 ✅ (5.2) app auth, client, manifest + register/check CLI, webhook verification · 📋5.3 review posting
│   │   └── review_cli.py           📋2 `python -m core.review`
│   └── tests/
│       ├── fixtures/repos/         🔜1 py_basic/, js_basic/, ts_basic/ (+ expected.json)
│       ├── test_parser_*.py        🔜1
│       └── test_migrations.py …    ✅
├── services/
│   ├── api/src/api/
│   │   ├── main.py checks.py dependencies.py   ✅
│   │   ├── routes/{health,repos,reviews}.py    ✅ (5.1), routes/webhooks.py ✅ (5.2)
│   │   ├── routes/{anti_patterns,search}.py   📋6
│   │   ├── schemas.py queries.py   ✅ (5.1) Pydantic I/O models, shared reads
│   │   ├── auth.py token.py errors.py   ✅ (5.1) JWT verification, dev tokens, error shape
│   │   ├── queue.py                ✅ (5.1) enqueue by task name (core/jobs.py)
│   │   └── sse.py                  ✅ (5.4) review events over SSE (Redis doorbell, D105)
│   ├── worker/src/worker/
│   │   └── celery_app.py tasks.py  ✅ (5.1) ping, index_repo, embed_repo, review
│   └── reranker/                   📋4 FastAPI + sentence-transformers CrossEncoder (CPU)
└── evals/
    ├── datasets/{seeded,clean}/<case_id>/{case.yaml, patch.diff}   ✅ (golden/ later)
    ├── sources.yaml sources.py     ✅ pinned OSS repos (name, url, sha, license) + checkouts
    ├── generate_seeded.py          ✅ operators → verified cases (+ graph.py syntax.py edits.py operators.py cases.py)
    ├── run.py metrics.py report.py records.py   ✅ runner, scoring, scorecard
    ├── results/<run>/              ✅ run.json + cases.jsonl + scorecard.{json,md} (gitignored)
    └── RESULTS.md                  ✅ the adopted/rejected upgrade log + tables (A0, B0, Q9)
```

---

## 9. Build phases

Each phase is a series of **numbered steps**. After each step: stop for review, report
what was built, how to run it, and what to verify. Estimates are rough
(part-time student pace) and are re-planned at each phase boundary.

### Phase 1: Foundation *(steps 1–2 done)*

| Step | Deliverable | Exit criteria | Est. |
|---|---|---|---|
| 1 ✅ | Monorepo, compose, uv workspace, ruff/pytest, CI, empty Next.js | `docker compose up` healthy; tests green | — |
| 2 ✅ | Models, initial migration, `/health` (DB + Redis), migrate service | `alembic check` clean; 200/503 behaviour verified | — |
| 3 ✅ | Parser: walker + skip rules → Python extractor → JS/TS extractor → resolver | Plain dataclasses out; resolution order per §5.4 | done (PR #1) |
| 4 ✅ | Migration (§4.2 row 1), `core.index` CLI: incremental persist, re-link, `index_runs`, summary | 2nd run on an unchanged repo = zero writes; summary includes % resolved (all / in-repo) | done (PR #2) |
| 5 ✅ | Fixture repos (`py_shop`, `ts_shop`) with golden `.graph` files; exact-match tests for parser output and the DB round trip, incl. unresolved/external edges and skips; re-index leaves the stored graph identical | Every node and edge asserted; CI green | done (PR #3) |

### Phase 2: Naive baseline, end to end (B0)
| Step | Deliverable | Exit criteria |
|---|---|---|
| 1 ✅ | Provider interfaces + `OllamaEmbedder`/`OllamaLLM` + fakes; Ollama health check | Contract tests pass against fakes; a manual smoke test against real Ollama (PR #4) |
| 2 ✅ | Chunk text builder (§5.6), token estimate, migration (embedding provenance), `core.embed` | All nodes embedded; re-run is a no-op; throughput recorded in §5.7 (PR #5) |
| 3 ✅ | Vector search + `core.search --mode vector` | Sensible top-k on the fixture repo (spot check) (PR #6) |
| 4 ✅ | Diff parser, head overlay, seed mapping | Unit tests on tricky diffs (renames, deletions, new files) (PR #7) |
| 5 ✅ | Prompts, structured output, validation, packing (vector only), trace writer, `core.review` CLI | Reviewing a local branch prints validated findings; trace rows written (PR #8) |
| 6 ✅ | Measure VRAM / latency / tokens; fill in §1.3 and §6.4 | Numbers recorded (PR #9) |

### Phase 3: Evaluation harness (measure before upgrading)
| Step | Deliverable | Exit criteria |
|---|---|---|
| 1 ✅ | `sources.yaml` (3–4 permissively licensed OSS repos, pinned SHAs) + seeded-bug generator (§10.1) | ≥ 60 seeded cases + ≥ 20 clean cases generated reproducibly (73 + 24; D68–D71; PR #10) |
| 2 ✅ | Runner (`evals.run`), matching + metrics, bootstrap CIs, report | One command produces a scorecard JSON + markdown (D72–D74; PR #11) |
| 3 ✅ | Run **diff-only (A0)** and **B0** | Baseline numbers committed to `evals/RESULTS.md` (PR #12; **contaminated by the commit-subject leak, D77**) |
| 4 ✅ | Counterfactual cases (Q10) + leak fix | 27 counterfactuals (`drop_param_cf` 12, `reorder_params_cf` 12, `exception_type_cf` 3); runner indexes each prep commit; precision counts counterfactual findings (D77–D79; PR #13) |
| 5 ✅ | Rerun A0/B0 on test (valid numbers) | `evals/RESULTS.md`: A0 F1 0.741, B0 0.654, paired Δ −0.086 [−0.163, −0.026] (D80) |

### Phase 4: The "+" upgrades, one per step, each gated by evals

**Re-planned 2026-10-11 (D81):** the baselines showed the bottleneck is not
retrieval. B0 packs the callers it needs 56% of the time and ignores them,
and it loses bugs by anchoring findings 1–3 lines off. So the steps that fix
how the model's output is anchored and how it reads the diff and context
come first; the retrieval upgrades follow, once better context can pay off.

| Step | Upgrade | Extra deliverables |
|---|---|---|
| 1 ✅ | A1 anchoring (Q11): snap near-miss findings to the nearest changed line; lines bordering a pure deletion are anchors | `validation.anchor_window`, `anchor_deletions`; `b0_anchor.yaml`, `a0_anchor.yaml`, `default.yaml`. **Adopted:** test F1 +0.113 [+0.040, +0.194] (D82) |
| 2 ✅ | Diff rendering (Q12) and context use (Q13): render hunks so moved lines aren't read as duplicates; prompt the model to check callers before claiming an API break | `packing.diff_style`, `retrieval.tag_relations`, `generation.check_callers` (all default off). **Both rejected** (D83) |
| 3 ✅ | U6 thinking mode (moved up; Q14 answered), at 8k and 16k (Q15) | `default_think.yaml`, `default_think16k*.yaml`; `generation.temperature/top_p/top_k`, `think_fallback`; `--retry-failed`; `report --completed-only`. **Not adopted** (D85, D86) |
| 4 ✅ | Verification pass (Q16b): one narrow call per caller-dependent finding, with the graph's callers | `review/verify.py`, `verification` config, `default_verify.yaml`. **Rejected** (D87); Phase 4 closed here (Q16d) |
| later | U1 FTS + RRF | `identifiers.py`, tsv builder, fusion SQL, `--mode fts/hybrid` |
| later | U2 graph expansion | Recursive CTE, hub control, graph-based fused list |
| later | U3 reranker | `services/reranker`, compose service, HTTP client, blend weights |
| later | U4 centrality prior | PageRank job, score term |
| later | U5 anti-patterns | YAML library + sync + retrieval + `[AP]` citations |

Every step ends by running the eval, writing a `RESULTS.md` entry (adopt or reject,
with numbers), and updating `configs/pipeline/default.yaml`.

### Phase 5: GitHub App and delivery
App registration doc (manifest), webhook endpoint (HMAC, delivery dedupe),
installation tokens, mirror clone, Celery tasks and queues (§5.10), index on
install/push, review on PR events, posting reviews (§9.1 of `idea.md`), Redis
pub/sub → SSE, migration (`installations`, `webhook_deliveries`). Local webhook
delivery uses a tunnel: smee.io (D94; `gh webhook forward` can't forward an App's webhook).
**Exit:** opening a PR on a test repo yields inline comments within the latency
budget; a re-push doesn't duplicate comments.

| Step | Deliverable | User action |
|---|---|---|
| 1 ✅ #20 | Celery jobs and queues (`index`, `gpu`; D88): `index_repo` → `embed_repo` chain, `review`; advisory-lock retry (D89); queued reviews claimed by the worker (D90); the API enqueues by name (D91); API: repos, index-runs, reviews (manual, local repos), JWT auth + `DEMO_MODE` + one error shape (D92) | none |
| 2 ✅ #22 | GitHub App: manifest + `python -m core.github register` (D93) and `check`, `docs/github-app.md`; App JWT (RS256) → cached installation tokens (D96); httpx GitHub client; `POST /webhooks/github` with HMAC SHA-256, delivery dedupe (D95), `installation` events mirrored (D97); migration (`installations`, `webhook_deliveries`, `repos.installation_id`) | register the App (`docs/github-app.md`) |
| 3a ✅ #23 | Repos from events (D101): `installation` created and `installation_repositories` attach repos (keyed by GitHub id) and queue indexing; removed repos are detached; a push to the default branch re-indexes. Bare mirrors refreshed with an installation token (D99); indexing straight from git objects (`parse_revision`, `index_revision`; D100); webhook state → jobs → processed, in that order (D98) | none |
| 3b ✅ #24 | Pull requests (D102): `opened`/`synchronize`/`reopened`/`ready_for_review` (open, not drafts) queue a review of the PR head (`refs/pull/N/head`) against its base, with the PR title in the prompt; one `COMMENT` review per push: inline comments plus a summary even when nothing new (D101); a newer push supersedes older reviews (D103); no repeats on unchanged functions (topics, D104). Verified on a real PR | none |
| 4 ✅ #25 | `GET /api/v1/reviews/{id}/events` (SSE): replay after `Last-Event-ID`, then live through a Redis doorbell over the `review_events` rows (D105), until an `end` event (D106); heartbeats every 15 s. Verified live on a real PR | none |
| 5 ✅ #26 | End to end in Docker on a private test repo (`codelens-playground`, sqlite-utils), via smee: 5 PR events, 9–49 s from open/push to posted review (§6.4), all planted bugs found, no repeats on re-push. **Exit criterion met.** Q17 answered with embedding slices (D107), Q18 with the whole PR diff (D108), comment placement clipped to hunks (D109); runbook `docs/e2e.md` | added the playground to the App |

### Phase 6: Dashboard
GitHub login (our own, D113; planned as Auth.js) → JWT to the API, carrying the user's
installation ids, and per-repo access checks in the API (moved from Phase 5, D101); pages: repos, repo, reviews, **review detail**
(diff + inline findings + live SSE + evidence drawer + 👍/👎), **graph** (React Flow),
**trace** (stage scores), anti-patterns CRUD.
**Exit:** watch a review stream live and click any finding through to its evidence and
graph.

| Step | Deliverable | User action |
|---|---|---|
| 1 ✅ #27 | The API the dashboard reads: per-repo access checks (JWT `installations` claim + `API_ADMIN_LOGINS`, D111); a review's diff and seeds stored with it (D110); `/reviews/{id}/diff`, `/trace`, `/graph`, `/nodes/{id}`, `/findings/{id}/feedback`; migration (`finding_feedback`, `reviews.diff`, `reviews.seeds`) | none |
| 2 ✅ #28 | Web foundation: GitHub login with the App's OAuth client, our own code (D113): state cookie, code exchange, `GET /user` + `GET /user/installations`, a signed session cookie, the GitHub token dropped; `proxy.ts` redirects early, the data access layer decides; the server-side API client that signs a 5-minute JWT per call; layout, navigation; the repos page; vitest | add `http://localhost:3000/api/auth/callback` as the App's redirect URI (`docs/github-app.md` §5) |
| 3 ✅ #29 | A repo's page (`/repos/[id]`): header with status and a Re-index button (a Server Action; the page re-renders every 3 s while indexing); the Reviews tab (newest first, status filter, keyset pages, PR and commit links, findings by severity, duration); the Indexing tab (the graph's size and call resolution, every run with files and nodes changed); repo rows link here | none |
| 4 ✅ #30 | Review detail (`/reviews/[id]`): the stored diff (own parser) with each finding under its last line, clipped like the GitHub review (D109); the live SSE timeline through a session-checked Next.js route that adds the JWT; the evidence drawer (node code via another route); 👍/👎 (a Server Action, optimistic); `reviews.title` stored and shown (Q19, D114); review rows link here | none |
| 5 ✅ #31 | A review's Graph and Trace tabs (the review page becomes a layout with Diff / Graph / Trace tabs and one shared evidence drawer). Graph: React Flow, laid out in four columns by our own `lib/graph-layout.ts` (callers → changed code → callees → other context), changed code coloured by its change, nodes the model saw solid, cited evidence starred, click for the code. Trace: per chunk and per changed definition, every candidate with its rank, similarity, outcome (shown, signature only, or why not) and tokens, against the token budgets | none |
| 6 ✅ #32 | End to end in the browser. A finding's **Show in graph** opens the Graph tab with its changed code and evidence highlighted (`lib/focus.ts`). Run (2026-10-13, review 1383, playground PR #4): queued 6 s after the push, started at 11 s, finished at 43 s; the timeline streamed live and the page re-rendered into the diff with 4 findings; a finding's evidence opened `Database.__getitem__`, and Show in graph highlighted it and the changed `Database.table`. **Exit criterion met.** Runbook: `docs/e2e.md` | none (allowed the test push) |

Anti-patterns CRUD is deferred with U5 (D112).

### Phase 7: Demo, observability, hardening
`DEMO_MODE`, demo data export/import, hosted deploy (§15), optional Langfuse
integration, rate limits and budgets, README with architecture diagram, results
table, and a demo video.
**Exit:** a public URL showing real reviews; the README tells the eval story.

Decided (2026-10-13): the demo is **open, with no login** (Q7, changed the same day, D117):
anyone with the link browses it, and every visitor sees every repo of the demo (D115).
Hosting is Vercel + Supabase (Q8, D116), on a subdomain of the user's own domain. The demo
shows a new public repo, not the private playground. Proof for people who won't click
comes from the README and a video (step 5). Letting other people use CodeLens on their
own repos is a separate, later upgrade: `docs/hosted-service.md`.

| Step | Deliverable | User action |
|---|---|---|
| 1 ✅ #33 | Demo mode end to end in the code: with `DEMO_MODE=true` the API runs on Postgres alone (no Redis, no queue; event streams read the database; `/health` checks only Postgres), refuses every write and webhook, and shows every repo to every token holder (D115); the dashboard (`DEMO_MODE=true`) shows a demo banner and hides Re-index and 👍/👎 | none |
| 2a ✅ #34 | The open demo (D117): with `DEMO_MODE=true` the dashboard has no sign-in (the proxy lets everyone in; the sign-in page and routes go to the repos) and calls the API as one anonymous visitor; a demo needs only `API_URL`, `API_JWT_SECRET` and `DEMO_MODE`. `python -m core.demo export` (only the named repos; refuses any that isn't public on GitHub, and local ones; leaves out embeddings, votes, installations, local paths) and `import` (replaces those repos, keeps ids, moves the sequences). `docs/demo.md`; `docs/hosted-service.md` (the future upgrade) | none |
| 2b ✅ #35 | The public demo repo `RAIF-KARANI/codelens-demo`: sqlite-utils at `9c66baf` (Apache-2.0, credited; its CI left out) with three PRs, reviewed by the App from the laptop (2026-10-13): **#1** a planted SQL injection → 1 critical, inline (15 s); **#2** a harmless rename → no findings (8 s); **#3** four planted bugs → all four found, inline (23 s). Exported with `core.demo` (555 KB: 1 repo, 66 files, 1,337 nodes, 6,077 edges, 3 reviews, 5 findings); the private playground was refused by the live public check. Imported twice into a fresh, migrated database (the second replaced the first), and the open demo served every page from it | created the public repo and added it to the App |
| 3 ✅ #36–#40 | Hosting (D116) at `https://codelens.raifkarani.in`: `deploy/api/` is the API's Vercel root (an entrypoint importing the app, its own `pyproject.toml` + `uv.lock`, pinned to the workspace's `uv.lock` by `scripts/export_demo_requirements.py` and checked in CI, Python 3.12, a daily `/health` cron that keeps the free Supabase project awake); the dashboard's Vercel root is `apps/web`; the database is Supabase through its Session pooler (asyncpg, `ssl=require`); `scripts/publish_demo.py` migrates it and loads the export (also how to refresh the demo); a CNAME on Cloudflare (DNS only); database and functions in Mumbai. Runbook: `docs/demo.md` §5. Checked locally: a fresh environment from it alone (53 MB) serves the demo API. The first Vercel build used a `requirements.txt` and failed: Vercel's converter rebases `../../` paths to the repository root but writes them into `deploy/api`; fixed in the follow-up PR with the uv project (installed by Vercel's uv 0.10.11, rehearsed locally). **2026-10-15:** Supabase project `codelens-demo` (Mumbai, Postgres 17) migrated and loaded by `publish_demo.py` (pooler `aws-0-ap-south-1`); row-level security on for all tables with no policies, so Supabase's own REST API can't reach them; `deploy/api` run locally against it served every endpoint. **Live 2026-10-15:** API project `codelens-api` at `https://codelens-api-beta.vercel.app` (`codelens-api.vercel.app` is someone else's), dashboard project `codelens` at `https://codelens.raifkarani.in` (CNAME to Vercel, DNS only). Three fixes on the way: functions pinned to Mumbai in `vercel.json` (they ran in Washington, 1.2 s per new connection; #38), and the API through Supabase's transaction pooler with `DATABASE_TRANSACTION_POOLER` (the session pooler's 15 connections ran out under a review page's parallel calls; #39, D118). Checked live: the site opens without a sign-in, with the banner; repos, review, graph (32 nodes, 25 edges) and trace pages render with no error; 6 bursts of 16 parallel API calls 96/96 OK; `/health` ok (database ~9 ms); no token or another secret's token → 401 | create the Supabase project and the two Vercel projects; set their variables (from `.env.demo`); add the CNAME in Cloudflare |
| 4a ✅ #41 | **Installation allowlist** (`GITHUB_ALLOWED_ACCOUNTS`, D119): events from installations by other accounts are ignored, so the App can be opened to "Any account" for a friend's repos while strangers can't use the laptop (user's choice, 2026-10-13); unset means nobody (fail closed, user's choice 2026-10-15); checked on every push and PR, so delisting is immediate; `register` writes the owner into it. **Review cap** (`GITHUB_REVIEWS_PER_HOUR`, default 10 per account, D120): over it, the review is recorded as failed with the reason and not queued. **Live 2026-10-15** (Docker stack, playground PR #4): with `RAIF-KARANI` listed, a push was reviewed and posted (review 1387, 4 findings, none repeated, ~3 min on a cold, then power-limited GPU); with only `someone-else` listed, the next push and PR event were delivered (202) and recorded, and no review was made | add `GITHUB_ALLOWED_ACCOUNTS` (done for the user's `.env`); to let the friend in: list them, then **Make public** in the App's settings (`docs/github-app.md` §6) |
| 4b ✅ #42 | **Structured logs** (D121): one setup (`core/logs.py`) for the API, the workers and the demo, `LOG_FORMAT=text` (default) or `json`; every line in a request carries its `request_id` (answered in `X-Request-ID`), every line in a job its `task_id`; one access line per request with `duration_ms`; the demo logs JSON. **`/health` checks Ollama** where it's used (D122): the configured models must be pulled; failing → `degraded` (still 200), while DB/Redis failing stays `unavailable` (503). Live 2026-10-15: `/health` ok with Ollama (92 ms); JSON lines from the API (a caller's `X-Request-ID` kept) and from an index job (`task_id` on its GitHub calls) | — |
| 4c ✅ #43 | **Rate limits for the public demo** (D123), at Vercel's edge rather than in code: the dashboard project limits each IP to 100 requests a minute outside `/_next/` (a 4-page visit measured at 18), and the API project denies requests without an `authorization` header except `/health`. Checked live 2026-10-15: 99 × 200 then 429 for a 110-request burst; tokenless API calls 403 at the edge, `/health` 200, the site unaffected. `docs/demo.md` §5 "Abuse and cost". Langfuse stays an unbuilt opt-in (§13) | create the two Firewall rules (done 2026-10-15) |
| 5 🔍 | **README** rewritten for readers, not developers: the pitch, the live demo, screenshots taken from it (`docs/images/`: the GitHub review, a review page with its replayed timeline, the graph, the trace), the pipeline as a diagram and six steps, **the evaluation story** (the 83-case test set, the gate, the results table, anchoring adopted, four ideas rejected, retrieval not yet paying at 8B, the leak caught early), engineering notes, **"Run it on your own repos"** (own App via `register`, smee, compose), tech stack, doc index. The old README's developer reference moved to `docs/development.md` (stale lines fixed). **`docs/demo-video.md`**: an 8-scene, ~3-minute script with a preparation checklist | record the video (`docs/demo-video.md`); make the repo public so the README and the demo's links work; update the repo's description and website on GitHub |

### Stretch
CodeLens as a hosted service other people install (`docs/hosted-service.md`) · U7 sub-chunking · a feedback loop (suppress patterns users vote down) · mining
anti-patterns from past human review comments · Go/Java support · TS path aliases ·
light type inference for `self.attr`.

---

## 10. Evaluation

### 10.1 Datasets

**Seeded bugs (primary).** Take functions from pinned OSS repos and apply mutation
operators whose bug is **only visible with context**. Ground truth is then automatic,
including which context is *necessary*:

| Operator | Mutation | Expected finding | Oracle context (auto) |
|---|---|---|---|
| `raise_to_none` | `raise X` → `return None` in a function whose callers dereference the result | critical / bug at the changed line | callers that use the return value |
| `drop_param` | Remove a parameter that callers pass | critical / api-contract | callers passing that argument |
| `reorder_params` | Swap two same-typed parameters in the definition | warning+ / api-contract | callers using positional args |
| `drop_await` | Remove `await` on a call to an `async def` (Python) / async fn (JS) | critical / concurrency | the callee definition |
| `remove_guard` | Delete a `None`/null check before an attribute access | warning+ / bug | where the value comes from (callee/caller) |
| `sql_format` | Replace a parameterised query with string formatting | critical / security | the DB helper definition |
| `exception_type` | Change the raised exception class that callers `except` | warning+ / error-handling | callers' `try/except` |

Generation rules: the mutated function must have ≥ 1 resolved caller (where the
operator needs one); the diff must parse cleanly; dedupe near-identical cases. Each
case is `case.yaml` (source repo, sha, file, operator, expected findings, oracle node
qualified names) + `patch.diff`.

**Clean PRs.** Behaviour-preserving changes: renaming a local variable, adding
docstrings, reformatting, extracting a helper with every call site updated. Expected
findings: **none**.

**Golden real PRs** (Phase 6+, optional): 20–30 historical OSS PRs where review
comments or follow-up fix commits reveal a bug. Hand-labelled.

**Source repos:** 3–4 small-to-medium, permissively licensed (MIT/BSD/Apache) Python
and JS/TS projects with decent internal call depth. Chosen in Phase 3 step 1 and
pinned by SHA in `evals/sources.yaml`: sqlite-utils, starlette, click, h3 (D68).

**As built (Phase 3 step 1):** `python -m evals.generate_seeded` produces 73 seeded
cases (12 per operator; `sql_format` 7 and `exception_type` 6, limited by supply) and
24 clean ones (`rename_local`, `extract_return`), split 32 dev / 65 test. Clean edits
follow each language's naming convention so they aren't style findings. See
`evals/README.md` and `evals/datasets/summary.md`.

### 10.2 Metrics

| Level | Metric | Definition |
|---|---|---|
| Retrieval | **Oracle recall@k** (k = 5, 10, 20) | Share of oracle nodes present in the packed context (and in top-k before packing) |
| Retrieval | MRR | 1 / rank of the first oracle node |
| Findings | **Precision, recall, F1** (primary = F1) | A prediction matches an expectation if: same file, line ranges overlap within ±3, compatible category (D72), severity ≥ expected − 1 level. One-to-one greedy matching. Precision counts posted findings on seeded cases; clean cases feed the noise metric |
| Findings | Critical recall | Recall restricted to `critical` expectations |
| Noise | **Findings per clean PR** | Mean posted findings on clean cases (target ≤ 0.3) |
| Ops | p50 / p95 latency, prompt + completion tokens per case | Wall-clock, from the trace |

### 10.3 Ablation configs
`configs/pipeline/`: `a0_diff_only`, `b0_vector`, `b0+u1`, `b0+u1+u2`,
`b0+u1+u2+u3`, `…+u4`, `…+u5`, `…+u6`, plus `graph_only`. Same model, same prompt,
same budgets; only the toggles change.

### 10.4 Rigor
- **Determinism:** `temperature 0.1`, a fixed `seed`, and pinned model digests (record
  `ollama show` digests in each result file).
- **Uncertainty:** report 95% **bootstrap CIs** (1,000 resamples over cases). Compare
  configs **paired** on the same cases.
- **Variance check:** run the final configs 3 times with different seeds; report the
  spread.
- **No leakage:** prompts and weights are tuned on a **dev split** (≈ 30%) and the
  final numbers reported on the **test split**.

### 10.5 Adoption gate (per upgrade)
Adopt if all of these hold: ΔF1 > 0 with a CI lower bound > 0 (paired bootstrap) ·
noise per clean PR doesn't increase by more than 0.1 · p95 latency within budget
(**p95 ≤ 10 minutes per review**, D84) (or
the gain justifies relaxing it, stated explicitly in `RESULTS.md`).

### 10.6 CI vs local
CI has no GPU, so **CI runs no model-based evals.** CI covers the parser, retrieval
SQL, fusion math, packing and validation with the fake embedder/LLM. Full evals run
locally (`python -m evals.run`), and their summaries are committed to
`evals/RESULTS.md`.

---

## 11. Config sketch

### 11.1 Service settings (env → `core.config.Settings`)

```dotenv
# --- infrastructure (exists)
DATABASE_URL=postgresql+asyncpg://codelens:codelens@localhost:5433/codelens
REDIS_URL=redis://localhost:6379/0
EMBED_DIM=1024
LOG_LEVEL=INFO

# --- models (Phase 2)
OLLAMA_BASE_URL=http://localhost:11434          # containers: http://host.docker.internal:11434
LLM_MODEL=qwen3:8b
LLM_NUM_CTX=16384
LLM_TEMPERATURE=0.1
LLM_SEED=42
LLM_TIMEOUT_S=180
EMBED_MODEL=qwen3-embedding:0.6b
EMBED_BATCH_SIZE=32
EMBED_MAX_TOKENS=1024
EMBED_QUERY_ON_CPU=true

# --- reranker (Phase 4)
RERANKER_URL=http://localhost:8081
RERANKER_MODEL=BAAI/bge-reranker-base

# --- pipeline
PIPELINE_CONFIG=configs/pipeline/default.yaml

# --- storage
DATA_DIR=./data                                  # mirrors, caches

# --- GitHub App (Phase 5)
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY_PATH=./secrets/github-app.pem
GITHUB_WEBHOOK_SECRET=

# --- API / dashboard (Phase 5–6)
API_JWT_SECRET=
CORS_ORIGINS=http://localhost:3000
DEMO_MODE=false
```

### 11.2 Pipeline config (`configs/pipeline/default.yaml`)

```yaml
name: default
retrieval:
  vector:  { enabled: true,  k: 30 }
  fts:     { enabled: false, k: 30 }            # U1
  graph:                                         # U2
    enabled: false
    max_depth: 1
    edge_kinds: [calls, inherits]
    include_parent: true
    max_neighbors_per_hop: 15
    hub_degree_threshold: 50
  fusion:  { method: vector_only, rrf_k: 60 }   # vector_only | rrf
  rerank:                                        # U3
    enabled: false
    top_n: 100
    weights: { rerank: 0.6, graph: 0.2, centrality: 0.1, caller_of_changed_sig: 0.1 }
  centrality_prior: false                        # U4
  anti_patterns: { enabled: false, k: 5 }        # U5
packing:
  budget_tokens: { diff: 4000, changed_nodes: 3500, context: 4000, anti_patterns: 800 }
  max_review_chunks: 4
  degrade_to_signature: true
generation:
  think: false                                   # U6
  max_output_tokens: 2000
validation:
  min_confidence: 0.6
  max_comments: 5
  line_tolerance: 0
```

### 11.3 Per-repo config (`.codelens.yml` in the reviewed repo)

```yaml
ignore: ["migrations/**", "**/*.snap", "docs/**"]
min_severity: warning
max_comments: 5
categories: [bug, security, concurrency, api-contract, error-handling]
anti_patterns: .codelens/anti-patterns.yml
```

### 11.4 Ollama host (Windows user env vars, optional)
Only needed for a 16k window: `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`
(measured: 16k then fits fully in VRAM at full speed, §1.3), and `OLLAMA_KEEP_ALIVE=30m`.
Leave `OLLAMA_NUM_PARALLEL` at 1. Restart Ollama after setting them, then set
`LLM_NUM_CTX=16384`.

---

## 12. Testing strategy

| Layer | What | How |
|---|---|---|
| Unit | Identifier splitting, diff parsing, RRF math, packing, validation rules, fingerprinting, prompt rendering | Pure functions; table-driven tests |
| Parser | Exact nodes and edges for fixture repos | `expected.json` per fixture; assert on whole sets with clear diffs on failure |
| Integration (DB) | Migrations, persistence idempotency, re-link, retrieval SQL, CTE traversal | Throwaway `codelens_test` DB per test (exists); marker `integration` |
| Providers | Request/response shapes, retries, timeouts | `httpx.MockTransport`; fakes implement the same Protocol |
| Pipeline | Full `core.review` on a fixture repo + fixture diff | Fake LLM returns canned `ReviewOut`; assert validation and trace rows |
| API | Routes, auth, SSE, webhook HMAC | `TestClient`, signed payload fixtures |
| E2E (manual / pre-release) | Real Ollama, real GitHub test repo | Checklist in `docs/release.md` |
| Evals | Quality | §10 (local only) |

**Fakes:** `FakeEmbedder` hashes tokens into a deterministic sparse→dense vector, so
lexically similar texts are close. That's good enough to test retrieval plumbing in
CI. `FakeLLM` returns scripted outputs keyed by prompt hash.

---

## 13. Observability

- **Built-in traces (primary):** `reviews` (config, timing, tokens),
  `review_context_items` (every candidate with stage scores, and whether it was
  included), `review_events` (timeline), `findings` with `status`/`drop_reason`. These
  power the dashboard's trace page and the eval analysis. Nothing leaves the machine.
- **Logs:** structured JSON (stdlib `logging` + a JSON formatter), with `review_id`,
  `repo_id` and `stage` bound via `contextvars`.
- **Langfuse (optional, Phase 7):** self-hosting it needs extra infrastructure, and the
  cloud tier would send code off-machine. That conflicts with the local-only story, so
  it's an opt-in integration behind `LANGFUSE_*` env vars, off by default.
- **Health:** `/health` grows checks for Ollama (`/api/version`) and the reranker once
  they're used.

---

## 14. Security and privacy

The threat model and mitigations are in `idea.md` §12. What changes with local models:

- **Privacy is a feature:** code and prompts never leave the machine (Ollama is local).
  The README can say so plainly.
- **Prompt injection** still applies (§6.3). The LLM has no tools, output is
  schema-constrained, and findings are validated.
- **Demo instance** holds only data from public OSS repos you choose. `DEMO_MODE`
  disables all writes and webhooks.
- **Secrets:** the GitHub App key lives in `secrets/` (gitignored), with its path set
  via env. `.env` stays gitignored. Pre-commit `detect-private-key` is already enabled.

---

## 15. Public demo plan

**Constraint:** free, local models need the laptop's GPU, and hosting a GPU is not
free. So the demo separates **serving** from **generating**:

```
laptop (GPU)                                    hosted (free/cheap tiers)
────────────                                    ─────────────────────────
index + review demo repos ──export──► demo DB ◄── API (DEMO_MODE, read-only) ◄── Next.js dashboard
GitHub App live reviews (when online, via tunnel) → posts comments on the demo repo's PRs
```

1. Curate 2–3 public demo repos with PRs (including seeded-bug PRs from the eval set).
2. Generate reviews locally, then run `python -m core.demo export --repos …` → a SQL
   dump of only those repos' rows. Import it into the hosted Postgres (pgvector-capable
   managed DB).
3. Host the API (`DEMO_MODE=true`, no worker, no Ollama) and the dashboard. Every page
   works, including trace and graph, from stored data. `/search` works with FTS only
   (no query embeddings without a model). That's an acceptable demo degradation.
4. **Live mode:** when the laptop is on with a tunnel, real PRs on the demo repo get
   live reviews. The demo dashboard shows those once exported.
5. **Optional later:** `LLM_PROVIDER=openai_compatible` lets any hosted OpenAI-style
   endpoint replace Ollama for an always-on demo, with no code change beyond config.
   Not planned unless needed.

Hosting choices (which providers) are decided in Phase 7, based on free-tier terms at
that time.

---

## 16. Decisions

D1–D17 are recorded in `idea.md` §15 (one Postgres, own retrieval code, uv workspace,
VARCHAR+CHECK enums, `SET NULL` edges + `target_name`, own tsv with identifier
splitting, `asyncio.run` + NullPool in workers, `EMBED_DIM` fixed at migration,
node-level diffing, RRF, bounded rerank, precision-first, COMMENT-only reviews, SSE,
PR overlay, migrate service, host port 5433). New decisions:

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D18 | **naive+ architecture:** a fixed single-pass pipeline; upgrades are config toggles adopted only through the eval gate | Agentic / LLM-driven retrieval | Deterministic, measurable, fast on local 8B models; ablations share the production code path |
| D19 | **Build the eval harness before any upgrade** (Phase 3 before 4) | Build everything, then evaluate | Every upgrade has to justify itself with numbers; that's the project's story |
| D20 | **Free/local models via native Ollama** (`qwen3:8b`, `qwen3-embedding:0.6b`) | Hosted APIs | Zero cost, code never leaves the machine, already installed; quality risk is managed by evals and the validation layer |
| D21 | Ollama runs **natively on Windows**; containers reach it at `host.docker.internal` | Ollama in Docker with GPU passthrough | Native GPU use, already working, no WSL GPU setup |
| D22 | **Chunk = AST node**; class chunks hold member signatures only; a context header on each chunk; truncation at 1024 tokens | Fixed windows; sub-chunking from day 1 | Keeps vector hits 1:1 with graph nodes; no duplicated method bodies; sub-chunking only if evals demand it (U7) |
| D23 | Asymmetric embeddings (an instruction prefix for queries only), L2-normalised | Symmetric | Matches how Qwen3-Embedding is trained; normalised vectors make cosine = dot |
| D24 | Our own small reranker service (sentence-transformers `CrossEncoder`, CPU) | HF TEI server; LLM-as-reranker | Explainable (~40 lines), keeps the GPU free for the LLM, swappable behind the `Reranker` protocol |
| D25 | Separate Celery queues, with the `gpu` queue at concurrency 1 | One queue | Prevents VRAM thrash and keeps reviews from waiting behind bulk indexing |
| D26 | `num_ctx 16384` with a q8_0 KV cache and flash attention | Larger context; fp16 KV | Fits 8 GB VRAM alongside the 8B model; the budget table (§6.2) makes the trade-off explicit |
| D27 | Built-in trace tables first; Langfuse optional | Langfuse from day 1 | No extra infrastructure, nothing leaves the machine, and the trace view is a product feature |
| D28 | Demo = hosted read-only data + local generation | Hosted GPU; hosted LLM API | Stays free; the optional `openai_compatible` provider remains an escape hatch |
| D29 | Each phase gets its own migration; applied migrations are never edited | Squash/edit the initial migration | Practises real migration discipline; history is part of the story |
| D30 | Reviews from CLI/evals are stored as `reviews` rows (`source` column) | Separate eval tables | One pipeline, one trace format, and the dashboard can show eval runs |
| D31 | Official per-language grammar wheels instead of `tree-sitter-language-pack` | language-pack 1.x | language-pack 1.x downloads native grammar libraries at runtime (network needed, not pinned by `uv.lock`); the wheels are the same grammars, bundled and hash-pinned |
| D32 | Pin `tree-sitter<0.26` | Latest | py-tree-sitter 0.26.0 segfaults reading `Node.start_point` on larger files (reproduced 2026-10-10); 0.25.2 is fine. Re-test before lifting |
| D33 | Qualified names: Python dotted (`pkg.mod.Class.method`); JS/TS `path/without/ext:Symbol.member`; nested defs `outer.<locals>.inner` | One scheme for both | JS module paths contain `/` and `.`, so `:` separates module from member unambiguously; `<locals>` mirrors Python's `__qualname__` |
| D34 | Calls to builtins / JS globals (`len`, `print`, `console.log`) produce **no edge** unless shadowed by a definition or import | Keep them as unresolved edges | They can never resolve, carry no review signal, and would make up a large share of all edges |
| D35 | One edge per (source, kind, target): call sites are deduplicated | One edge per call site | The graph models relationships; call-site counts aren't needed for expansion or ranking |
| D36 | Re-parse the whole repo on every index run (writes stay incremental) | Parse only changed files | Resolution of an unchanged file can depend on others; parsing is cheap (SQLAlchemy, 258 files: 3.7 s). Known gap: an unchanged file's edges are not re-resolved, so a resolved edge only changes when its own file changes or its target disappears (then `SET NULL` + re-link) |
| D37 | One DB connection per index run, one transaction per changed file | Connection per file; one big transaction | Per-file atomicity (crash leaves each file fully old or new; re-run finishes) without paying a TCP connect + auth per file under `NullPool` |
| D38 | `SET LOCAL synchronous_commit = off` for indexer write transactions | Durable commits | A Postgres crash can lose the last few commits but never corrupts; a lost commit means the file keeps its old hash and the next run redoes it. Avoids a WAL flush per file (costly on Docker/WSL) |
| D39 | Re-link as a per-edge index probe, and `ANALYZE` before it after bulk writes (≥ 1,000 rows) | `UPDATE … FROM (DISTINCT ON …)` join | The join took 0.2 s or 307 s on identical data depending on whether statistics had caught up after a bulk load; the probe form plus fresh stats is fast either way (SQLAlchemy cold index: 330 s → 20 s) |
| D40 | Parsing runs in `asyncio.to_thread` inside the async indexer | Call it directly | It is seconds of blocking I/O + CPU; on a worker's event loop it would stall everything else |
| D41 | Advisory lock (`pg_try_advisory_lock(hashtext('index:' || name))`) held for the run | Redis lock; no lock | Two concurrent indexers on one repo would interleave diffs; Postgres already holds the data, so the lock lives there too. Phase 5 workers reuse it |
| D42 | Files skipped for *transient* reasons (`parse_error`, `unreadable`) keep their previous rows; files that became unindexable (too large, minified…) are removed | Treat every skip as a deletion | A one-off read failure must not wipe a file's graph |
| D43 | Golden `.graph` files (one fact per line, sorted) for fixture repos; regenerate only via `UPDATE_GOLDEN=1` and review the diff | Hand-written assertions per edge; JSON snapshots | Every node/edge is asserted, and any behaviour change is a readable line diff in the PR. It already caught a real bug: edge dedupe keyed on `target_name` dropped the `contains` edge to a @property setter (same name, different ordinal) |
| D44 | Structured output: Ollama `format` = the Pydantic model's JSON Schema, then Pydantic validation; **one** repair turn (the model sees its output + the validation errors); truncated output (`done_reason: length`) fails immediately | Trust the grammar; unlimited retries | Ollama's grammar doesn't enforce every schema keyword, so validation is still needed; a repair turn fixes most slips; repairs can't fix a token limit, so retrying it only wastes GPU time |
| D45 | HTTP retries only for connection errors and 5xx (backoff 0.5 s, 1 s); never for timeouts or 4xx | Retry everything | A timed-out generation would just time out again (doubling latency); a 4xx (e.g. model not pulled) never succeeds on retry |
| D46 | Providers are built by `open_providers(settings)`, an async context manager | A plain factory | The Ollama providers own an httpx client that must be closed; the context manager makes leaking it impossible |
| D47 | The sha256 of the chunk text is the single test of embedding staleness; the indexer no longer clears embeddings on code change | Indexer clears `embedding` when a node's code changes | Many code edits leave a chunk unchanged (a module's skeleton shows only signatures), so clearing forced pointless re-embeds. A header change, such as a class signature edit, changes its methods' chunks without touching their code; the hash catches that and a clear-on-change rule wouldn't. Trade-off: until `core.embed` runs, a changed node keeps its old vector |
| D48 | Class and module chunks are *skeletons*: direct children replaced by `signature ...` | Embed the full class/module source | Method bodies are embedded once, in their own chunks, and a class vector represents its shape (attributes + API), not a blur of all its methods |
| D49 | Node `code` is dedented when the node starts its own line (nested methods read like top-level code) | Raw slice from the node's first byte | A raw slice left the first line at column 0 and the body at its original indent (e.g. 8 spaces), which is wasted tokens in chunks and garbled code in review prompts |
| D50 | A `CHUNK_FORMAT` version tag inside every chunk header | Separate config | Changing the chunk format changes every hash, so `core.embed` re-embeds everything automatically, with no migration and no manual flag |
| D51 | Vector search keeps Postgres's plan choice: at our sizes that's an *exact* seq scan + top-N heapsort, not HNSW | Force the index (`SET LOCAL enable_seqscan = off`) | Measured on 17k nodes: the planner picks the exact scan (25–90 ms; pg_stat shows 0 HNSW index scans) because pgvector's cost estimate overstates the index at this size; forced, the HNSW plan takes ~7 ms. Exact results are the better default while it's this cheap; the index takes over by itself as tables grow. Revisit with real review latencies in step 6 |
| D52 | `vector_search` sets `hnsw.iterative_scan = relaxed_order` and `ef_search = max(100, k)` per transaction, re-sorts by exact distance, and joins the caller's open transaction if there is one | pgvector defaults; always `begin()` | Iterative scans stop filtered HNSW searches returning fewer than k rows once the index is used; relaxed order needs the re-sort; joining an open transaction avoids failing on connections SQLAlchemy has already auto-begun |
| D53 | Seeds = the *innermost* node per changed line. Added lines are mapped on the head side, removed lines on the base side; a removed line in a node that no longer exists marks its closest surviving ancestor MODIFIED and the node itself DELETED | Mark every enclosing node; anchor deletions on new-side line numbers | Innermost keeps seeds specific (a method, not its class and module). New-side anchors for deletions landed on blank lines between methods and blamed the module. Base-side mapping is exact: a class that lost a method is MODIFIED, and the method is DELETED (its callers are prime suspects) |
| D54 | Whitespace-only added/removed lines never create seeds | Count every line | A blank line between methods sits outside every method, so it would mark the whole class as changed |
| D55 | Head/base versions of a node are matched by name *within the file* (qualified name minus module) | Full qualified name | Module names come from paths, so a renamed file would show every node as ADDED + DELETED; local names make a rename-with-edits a MODIFIED change |
| D56 | Changes are read from git objects only (`merge-base`, `diff base...head`, `show rev:path`), both sides parsed in memory; no checkout, no DB | Check out head; parse the working tree | No side effects on the repo; the base is the merge-base exactly as GitHub computes PRs; git runs with `--no-ext-diff --no-textconv` so a repo's diff drivers can't execute, and option-like revisions (`-…`) are rejected. Known gap for step 5: the stored graph may be indexed at a different commit than the merge-base; seeds map to stored nodes by qualified name |
| D57 | ~~Query embeddings run on CPU~~ (superseded by D62) (`EMBED_QUERY_ON_CPU=true`), bulk document embeddings on GPU; default `LLM_NUM_CTX=8192` | Both models on GPU; 16k context by default | Measured on the RTX 4060 (8 GB): `qwen3:8b` at 16k ctx with fp16 KV = 7.8 GB, 20% on CPU, a review timed out at 300 s. At 8k ctx with the embedder also on GPU it decoded at 8 tok/s; with query embeddings on CPU, **29 tok/s** and a 12 s review. 16k still works once `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` are set on the Ollama server (§11.4); supersedes D26's default |
| D58 | Every prompt is fitted to `num_ctx − max_output_tokens − margin` before sending: context blocks dropped weakest-first, then changed blocks; recorded as `context_window` in the trace | Trust the budgets; let Ollama handle overflow | Ollama silently truncates an over-long prompt, which can cut the system instructions themselves; the guard makes budgets self-adjust to whatever window is configured |
| D59 | Everything shown to the model carries new-file line numbers in a left column (diff, changed code, context) | Plain unified diff | Findings must cite exact lines to be postable; models are unreliable at counting lines from `@@` headers |
| D60 | Retrieved classes/modules from a file the change edits are excluded from context (`contains_changed_code`) | Include everything retrieved | Their stored code is the *old* version of the changed code, which contradicts the diff and misleads the model |
| D61 | Prompt-injection hardening: untrusted content only inside tagged blocks declared as data; our own closing tags neutralised inside content (`</context>` → `</ context>`); no tools; schema + validation | Trust the input | Code under review is attacker-controllable; with these layers an injection can at worst yield one bad finding, never an action |
| D62 | Embedder runs on GPU with `EMBED_NUM_CTX=2048` for queries *and* documents; CPU queries are opt-in | D57's CPU query embedding | Measured: the 8 tok/s slowdown came from the embedder's 4096-token context (2.4 GiB) overflowing VRAM beside the LLM, not from co-residence itself. At 2048 (2.0 GiB) both stay in VRAM with no LLM slowdown, and GPU queries are ~20× faster than CPU (9.6 s vs 187 s for 24). Same options both ways avoids Ollama reloading the model |
| D63 | Token estimate = chars ÷ 3.0 (was 3.5) | A tokenizer library; 3.5 | Measured against qwen3: line-numbered diffs are 3.0 chars/token, line-numbered code 3.3, plain code 4.2. 3.5 under-counted diffs by 16%, enough that 'fitted' prompts were really ~7.1k + 1.5k output > 8,192. Now real/estimate is 0.86–0.94 on review prompts |
| D64 | Packing budgets are shares of the window's prompt space (diff 40%, changed 25%, context 35%), with the YAML token values as caps | Fixed token budgets | With budgets sized for 16k, an 8k window was filled by diff + changed code and context got 0 blocks in 3 of 4 chunks, making B0 effectively A0. With shares: 11 context blocks on the same PR |
| D65 | When `max_chunks` binds, test files' changes count ×0.25, and source files are grouped before tests | Raw change size | On a real PR, two of four chunks were spent on large test diffs while the core source files they test were left out |
| D66 | 16k context is recommended once the Ollama server has flash attention + q8_0 KV; the default stays 8k (works with no server changes) | Make 16k the default | Measured: 16k + q8_0 + flash attention = 5.8 GiB, 100% in VRAM, same speed as 8k; 16k with fp16 KV spills (80% VRAM, half speed). A default must work without system changes |
| D67 | Methodology: benchmark one Ollama setting per *fresh* server, and verify no `llama-server` runners survive between runs | Kill and restart servers ad hoc | Killing `ollama serve` leaves its runner processes holding VRAM. That contaminated a first round of measurements and briefly suggested q8_0 was 2–30× slower; clean runs showed no slowdown |
| D68 | Eval sources: sqlite-utils (Apache-2.0), starlette (BSD-3), click (BSD-3), h3 (MIT, TS), pinned by commit (answers Q1) | requests, httpx, marshmallow, ky, hono, zod | Chosen from a measured survey of 10 candidates (mutation sites per operator, in-repo call resolution). sqlite-utils is the only one with real parameterised SQL; starlette and h3 bring async; click has the best call resolution (60%); h3 is ESM TypeScript, which our resolver follows (it does not follow CommonJS `require`) |
| D69 | Cases are generated from `core.parser`'s graph, not the DB: operators mutate a function only where the bug needs context (a caller that uses the result, passes the argument or catches the exception; an async callee), and those nodes become the oracle. Each case is verified: the file still parses (tree-sitter, plus `ast` for Python), the patch applies (`git apply --check`), and if the patch adds lines the expectation touches one | Hand-written cases; mutate anything | Automatic ground truth including the *necessary* context, reproducible byte for byte. The same parser builds the indexed graph, so oracle names match what retrieval returns. The added-line rule exists because validation only posts findings on added lines |
| D70 | `remove_guard` stays a pure deletion even though B0 cannot post on it (validation drops findings on unchanged lines: `not_on_changed_lines`) | Rewrite the guard so the diff adds a line | Deleting a guard is a real and common bug shape. Keeping it measures a known blind spot of B0 (12 cases) instead of hiding it; the fix (anchor a finding at the line after a deletion) becomes a candidate upgrade with a number attached |
| D71 | Dataset shape: up to 12 cases per operator, taken round-robin across sources; 6 clean per source; stratified 30/70 dev/test split per (kind, operator) by id hash; patches stored byte-exact (`.gitattributes`: `-text`) | One flat random sample | Round-robin keeps any one repo from dominating an operator; stratifying keeps every operator in both splits; byte-exact patches survive the repo's `eol=lf` normalisation |
| D72 | Category compatibility for matching: bug ← {bug, api-contract, error-handling}; api-contract ← {api-contract, bug}; concurrency ← {concurrency, bug}; error-handling ← {error-handling, bug, api-contract}; security ← {security} only. A second finding on the same bug is a false positive | Exact category; any category | The planted bug is what matters, not the label: a caller-breaking change called a "bug" is found. But SQL injection called anything other than security is not, and duplicate comments are noise a user would see |
| D73 | Runner: each case is a commit built with git plumbing (`read-tree`, `apply --cached`, `commit-tree` with a fixed author/date) on the shared checkout; records are appended per case to `cases.jsonl` (resumable); each record copies its expectation and oracle; sources are indexed and embedded as `eval/<name>` (incremental). Default split is `dev` | A worktree/branch per case; scoring by joining with the dataset later | No working-tree churn, the same commit sha every run, a 65-minute run survives interruption, and a results directory scores on its own after the dataset changes. Defaulting to dev keeps test numbers for reporting only |
| D74 | Statistics in plain Python: percentile bootstrap (1,000 resamples, seeded) per metric, and a *paired* bootstrap for deltas against a baseline (same case ids resampled for both); undefined metrics are NaN → `null`/"n/a", never 0. The scorecard checks the §10.5 gate (F1 up with CI > 0; noise up ≤ 0.1) | numpy/scipy | ~60 lines, no new dependency, every number explainable; NaN keeps "no findings" distinct from "all findings wrong" |
| D75 | `max_findings_per_chunk` stays 8 (answers Q9) | 4 | Measured on dev (paired): F1 −0.036, precision −0.079, noise +0.375. The cap is part of the prompt, so it changes what the model writes: with 4 it posted *more* false findings. Q9's premise (always 8 findings) came from a 13-file real PR; single-function changes average ~1 |
| D76 | Order for measuring: tuning questions on dev first, then the final configs once on test; baselines on test only after dev decisions | Run everything on test | Keeps test numbers untouched by choices made from them (§10.4). Dev also caught a dataset flaw (`help_` → `help__value`) before the test runs |
| D77 | Case commits are titled `Update <file>`, never with the case id | The id as the message | Commit subjects go into the prompt. `eval case click.raise_to_none.1a2b` told the model which bug to look for: every number in PR #12 (A0 and B0 alike) is contaminated and must be rerun. A regression test now checks that the subject never names the operator |
| D78 | Counterfactuals are built as two-commit refactors: a *prep* commit makes every caller compatible (drop the argument, swap the arguments, catch the new class too), then the case's diff is byte-identical to the seeded operator's. Only for private functions whose every `name(` in the repo is a resolved call site. With retrieval on, the runner indexes the prep commit (worktree, `eval-cf/<source>`) before reviewing | Mine naturally safe sites; mutate public functions | A survey found ~0 natural sites (real code has no dead parameters). Identical diffs mean a diff-only reviewer cannot tell bug from non-bug; only context can. Public functions are excluded because outside users could still pass the old arguments, which would make "safe" a lie. `raise_to_none` has no unambiguously safe prep (a swallowed error), so it has no counterfactual |
| D79 | Findings posted on counterfactual cases are false positives in **precision** (so in F1, the gate metric); also reported as `false_alarms_per_counterfactual`; their callers count as oracle for retrieval metrics | Report them only as a separate noise number | If they only fed a side metric, F1 could never reward a reviewer for using context, which is the very thing Phase 4 upgrades must show |
| D80 | The valid baselines (test, 83 cases): A0 F1 0.741, B0 0.654; B0 is the baseline Phase 4 improves, and A0 stays the reference upgrades must also beat | Treat A0 as the baseline | B0 is the pipeline we ship; its deficit has a measured mechanism (mis-anchored findings, unused context) that Phase 4 targets. An upgrade that only gets B0 back to A0 has not yet shown retrieval is worth its cost |
| D81 | Phase 4 re-ordered: anchoring (Q11), then diff rendering and context use (Q12, Q13), then the retrieval upgrades U1–U6 (user's choice, 2026-10-11) | The original U1-first order | Measured: B0 already retrieves the oracle half the time without using it, and mis-anchoring costs it most of its gap to A0. Better retrieval would raise oracle recall without moving F1 until the model can use and place what it gets |
| D82 | Adopt A1 anchoring (`anchor_window: 3`, `anchor_deletions: true`) into `configs/pipeline/default.yaml`, the new default of `core.review` | Keep B0's strict on-added-line rule | Test, paired vs B0: F1 +0.113 [+0.040, +0.194], recall +0.225, noise unchanged, 0 matches lost to validation (was 11); `remove_guard` 0 → 7/8. Window 3 = the offsets measured on B0's misses; checked on dev first (+0.110). Snapping is product behaviour, not eval gaming: a comment must sit on a diff line, and moving a near miss there is what a human would do. 3 of 8 moved findings were false positives, reported |
| D83 | Reject split diff rendering (Q12) and caller-aware context (Q13); keep both as off-by-default toggles | Adopt caller tags on the dev result | Dev: split −0.006 with noise +0.50; callers +0.029 [0.000, +0.072]; both 0.000. Test, callers: +0.006 [−0.056, +0.069], so the dev gain was noise. Counterfactual false alarms stayed at 1.0 throughout: the 8B model does not use tagged callers even when the prompt tells it to |
| D84 | Latency budget for the adoption gate: **p95 ≤ 10 minutes per review**, wall clock (user's decision, 2026-10-12) | Interactive (p95 ≤ 2 min); no budget | CodeLens posts reviews asynchronously on PR events, like hosted review bots, so minutes are acceptable; a budget is still needed so an upgrade can't trade unbounded time for quality. Latency is measured per eval case (one changed function), so a real multi-file PR scales with its chunk count; Phase 5 re-checks the budget end to end |
| D85 | Thinking mode (8k window, 3,000-token output reserve) is not adopted; kept as `default_think.yaml` | Adopt on the dev result, or on the completed-only view | Test, paired: F1 +0.050 [−0.035, +0.138] (all cases) and +0.058 [−0.011, +0.136] (72 both completed), so the gate fails either way. 11/83 reviews (13%) ran out of output budget, costing recall −0.122. On completed reviews it is the first change that makes the model use context: precision +0.109, noise 0.44 → 0.06, counterfactual false alarms 1.00 → 0.54, all significant. The fix is room to think (Q15), not a different idea. Failed reviews would flatter a config on counterfactual/clean cases, so `--completed-only` reports the view without them |
| D86 | Thinking is not adopted at 16k either. Its failures are repetition loops at low temperature (a chain of thought re-reading a moved line forever; 128 completed reviews never exceeded 2,617 tokens), not too small a budget. Qwen's sampling (0.6/0.95/20) stops the loops but loses recall (dev F1 −0.021); temp 0.1 with a 3,500 budget plus `think_fallback` (redo the chunk without thinking) fails nothing but gains nothing (dev +0.008). Kept: per-config sampling, `think_fallback`, truncation errors that say where the tokens went | Keep tuning thinking | Four measurements disagree (8k dev +0.08, 8k test +0.06 n.s., 16k dev ±0.01) at ~4× latency. More GPU hours on thinking at 8B have diminishing returns |
| D87 | Reject the verification pass; close Phase 4 at anchoring and move to Phase 5; U1–U5 deferred until a model that uses context (Q2) | Keep iterating on 8B prompting | Test, paired: F1 +0.026 [−0.048, +0.104], precision +0.089, recall −0.082. It cleared 4/8 real reorder bugs and 3/8 safe twins, so it cannot tell them apart. Lesson kept: in structured output, the reasoning field must come before the verdict (Ollama generates fields in schema order); verdict-first, it confirmed 31/31 while its own reasons said safe |
| D88 | Two Celery queues: `index` (CPU, concurrency 2) and `gpu` (concurrency 1), and a whole review runs on `gpu` | §5.10's third `review` queue, whose tasks await LLM subtasks on `gpu` | A review is GPU-bound end to end (query embeddings, then generation at ~30 tok/s). A task that blocks on another task's result holds its worker slot while it waits, and deadlocks once every slot is waiting; Celery's docs forbid it. One `gpu` worker gives the same VRAM guarantee with no waiting. Cost: a review queued behind a long embedding job waits for it (FIFO; Q17) |
| D89 | The per-repo lock stays the Postgres advisory lock (D41); an index task that finds it held retries every 30 s, for up to 30 min | A Redis `SET NX` lock with a TTL (§5.10) | Postgres releases an advisory lock as soon as its connection drops, so a crashed worker can never leave a repo locked. A TTL lock needs a TTL longer than the longest run yet short enough to recover from a crash. A retry keeps the task's place in its chain, so the embed step still follows |
| D90 | The API queues a review by inserting a `queued` row (refs resolved to SHAs) and sending its id; the worker claims the row. A redelivered task skips a finished review and restarts a `running` one (a crashed attempt), clearing its partial events, trace and findings | The worker creates the row; pass the refs as given | `POST` can answer `202 {review_id}` at once, so the client can follow the review. A bad ref is a 422 now, not a failed review later. The review is pinned to the commits named when it was asked for. Acks are late (redelivery after a crash), so the task must be idempotent |
| D91 | The API enqueues tasks by name through a producer-only Celery client; task names and routes live in `core/jobs.py`. A re-index is `chain(index_repo, embed_repo)` | Import the worker's task functions into the API | The API image doesn't contain the worker (or tree-sitter, or model clients), and shouldn't need them to put a message on a queue. One shared module keeps both sides' names and queues from drifting. The chain embeds only after a successful index |
| D92 | API auth from the first endpoint: HS256 JWT on all of `/api/v1` (`alg` pinned, `exp` and `sub` required, secret ≥ 32 bytes), refusing every request when `API_JWT_SECRET` is unset; `DEMO_MODE` → 403 on mutations; one error shape for every error. Per-repo access checks wait for installations (5.3) | Add auth with the GitHub App in step 2 or 3 | The endpoints exist from now on, and shouldn't exist unauthenticated, even on a laptop. An unconfigured server fails closed, not open. HS256 keys shorter than the hash can be brute-forced offline from one token (RFC 7518 §3.2). Manual reviews and re-indexing need a local checkout the API and worker can both read, so in step 1 they run on the host, as the evals do |
| D93 | The GitHub App is created from a manifest in code (`core/github/manifest.py`) by `python -m core.github register`: a one-shot local page POSTs it to GitHub, and the redirect's one-time code is traded for the App's id, private key and webhook secret, written to gitignored `secrets/` (0600 where supported), never printed. Permissions: contents read, metadata read, pull requests write; events: `push`, `pull_request` | Fill in GitHub's App form by hand | The permissions live in code and are reviewed like code; anyone can recreate an identical App in one click; credentials go straight to files instead of being copied around. A random `state` must round-trip, so a forged callback can't make the command save anything |
| D94 | Local webhook delivery goes through smee.io (`npx smee-client`) | `gh webhook forward` | `gh webhook forward` forwards repository and organisation webhooks only; an App's webhook is configured on the App. smee is what GitHub's own App tooling (Probot) uses |
| D95 | A webhook delivery counts as seen only once handled: it is upserted by `X-GitHub-Delivery` with `processed_at` NULL, and a row already processed means `duplicate`. Recording and handling share one transaction | Insert-or-skip on the delivery id alone | GitHub doesn't retry failed App deliveries by itself; a person redelivers from the App's settings with the *same* id. Skip-on-insert would make a delivery that crashed halfway impossible to redeliver. Same-transaction handling means a failure leaves nothing half-done. Cost: the table grows (pruning later) |
| D96 | App JWT: RS256, `iat` backdated 60 s, 9-minute lifetime. Installation tokens cached in process memory until 5 minutes before they expire; no lock (two concurrent mints are harmless) | Mint per request; cache in Redis or the DB | GitHub caps the JWT at 10 minutes and tolerates small clock drift only into the past. Minting per call costs a round trip each time. Tokens are credentials, so they stay out of storage and logs. An asyncio lock would bind the cache to one event loop, and each Celery task runs its own |
| D97 | The webhook endpoint fails closed (503 without `GITHUB_WEBHOOK_SECRET`, 403 in `DEMO_MODE`), verifies the HMAC on the raw body in constant time before parsing, and in step 2 only mirrors `installation` events into `installations` (every action but `deleted` carries the current state, so it upserts; `deleted` removes the row and `SET NULL`s its repos). Other events are recorded as `ignored` until step 3 queues work for them | Act on every event now | No real work in the request: GitHub times out after 10 s. Mirroring installations is a few-row write, and step 3's access checks need the table. Settings now treat empty env values as unset, so `GITHUB_APP_ID=` from `.env.example` loads |
| D98 | A webhook delivery is handled in three steps: (1) the claim and the state the event describes, committed together; (2) the jobs it calls for; (3) only then `processed_at`. Refines D95 | One transaction with the enqueue inside | A broker outage at step 2 returns 503 and leaves the delivery unprocessed, so "Redeliver" queues the jobs again; with the enqueue inside one transaction, either a committed state loses its jobs or a rolled-back state has queued jobs. Re-handling is safe: the upserts and every job are idempotent |
| D99 | GitHub repos are bare mirrors at `DATA_DIR/repos/<id>.git`: `git init --bare --template=` once, then `fetch --prune --no-tags` of `refs/heads/*` (PR heads per review, 3b) with all objects. The installation token reaches git only through `GIT_CONFIG_*` env vars (an `http.extraHeader`), with credential helpers reset and prompts off | `clone --mirror --filter=blob:none` (§5.1); the token in the remote URL | A partial clone fetches blobs lazily, i.e. over the network, with a token that may have expired, in the middle of parsing. `--mirror` would also fetch every `refs/pull/*`. A token in the URL is written to the repo's config and shows in process lists; an env var is in neither, and it expires within the hour anyway. Reset helpers: on Windows, Git Credential Manager would otherwise pop up a login window |
| D100 | Indexing from git objects: `parse_revision(git_dir, rev)` lists files with `ls-tree -r -l -z` (sizes known before reading; symlinks and submodules dropped) and reads every blob through one `cat-file --batch`, then applies the walker's rules; `index_revision` shares the lock and incremental writes with the working-tree indexer. Only the default branch is indexed; GitHub repo rows are keyed by GitHub's repo id | Check out a temporary worktree and reuse the walker | No checkout means no file from the repo is written and nothing in it runs (filters, LFS, hooks). One batch process instead of a `git show` per file. The golden fixtures parse and index to identical graphs both ways (tests). Keying by GitHub id makes a renamed repo an update, not a new repo |
| D101 | User decisions (2026-10-12): step 3 is split into 3a (repos from events, mirrors, indexing) and 3b (PR reviews and posting); in 3b, every reviewed push gets one `COMMENT` review, a short summary when nothing new was found; per-repo access checks move to Phase 6 | One step 3; stay silent when nothing new; access checks in 3 | Two reviewable PRs instead of one large one. Authors can see CodeLens ran on each push. Real users (and their installations) only exist once the dashboard has GitHub login; a check against dev tokens would guard nothing |
| D102 | Pull request reviews: queued from `opened`, `synchronize`, `reopened`, `ready_for_review` on open, non-draft PRs of attached repos, once per head (a failed one may be retried). The worker fetches `refs/pull/N/head` plus the branches into the mirror and reviews the head against its merge-base with the PR's base SHA, with the PR's title in the prompt. Posted as one `COMMENT` review: inline comments on the RIGHT side (a range only within one hunk) and a summary body. If GitHub refuses a placement (422), it is posted again with every finding in the body. The model's `@mentions` are defused (a zero-width space); failed reviews post nothing | Review on every event; `REQUEST_CHANGES`; one comment per finding as separate API calls | `refs/pull/N/head` also covers PRs from forks. One review call is atomic and notifies once. A bot shouldn't block merges (D13). The model's text is steerable by the code under review, and an @mention would notify a real person |
| D103 | A newer push supersedes older reviews of the same PR: queued ones are marked `superseded` when its webhook arrives, and the worker re-checks before running and again before posting (a push during a review leaves its findings recorded but unposted). New review status `superseded` (migration `e53885814355`) | Review every head; cancel running tasks | Only the latest head matters to the author, and the GPU queue is FIFO (Q17): superseding queued work saves it. Killing a running GPU task is messier than letting it finish and not posting |
| D104 | Across pushes, a finding is a repeat if an earlier posted review of the PR had a finding with the same *topic*: file + innermost node at its first line + that node's content hash. Validation's within-review dedupe keeps the fingerprint (eval behaviour unchanged) | The fingerprint (file + category + the code in its exact lines) | Measured on a real PR: the same concern ("null if `gridId` is not provided") came back on lines 167–168 instead of 167, got a new fingerprint, and was posted twice, alongside three rewordings. With topics, a README-only push posted "No new findings; 2 more on code already commented on". Trade-off: a different second issue in an unchanged function isn't posted on later pushes; the first review of that code had its chance |
| D105 | Live events: Postgres is the mailbox, Redis the doorbell. Each event is a `review_events` row, committed first; then the worker publishes a bare notification on `review:<id>`. A stream subscribes, *then* reads the rows after `Last-Event-ID`, then re-reads on every ring and at every 15 s heartbeat. Notification failures never fail a review | Publish the events themselves on Redis; poll Postgres only | Subscribing before reading closes the gap between replay and live. A lost ring (pub/sub stores nothing) costs one heartbeat of delay, never an event, and live and replayed streams are the same rows. Polling alone would delay every event by up to its interval. Measured live on a real PR: every event reached the client the moment it was committed |
| D106 | Every review's last event is `end` (`{status}`), and the stream closes after it. The pipeline writes it for CLI and eval reviews; the worker writes it itself, after posting to GitHub or after any failure. Reviews from before this step have no `end`: once finished and quiet for 30 s, the stream sends a synthetic one (no id) | Close on `done`, or when the status turns final | For a GitHub review, `done` (pipeline finished) comes before `published`, and the status turns `completed` before posting. Only the writer of the last event knows it is last. An EventSource reconnects when a stream closes, so the client needs `end` to know to stop |
| D107 | `embed_repo` embeds at most 512 nodes per run (~20 s), then re-queues itself if stale nodes remain, so a review queued meanwhile runs before the next slice (answers Q17) | Celery priorities; a separate queue for reviews; one long task | The Redis transport's priorities are emulated and approximate, and two queues on one concurrency-1 worker still don't pre-empt a running task. Slicing bounds the wait by construction, needs no broker features, and `embed_repository` already took a `limit`. Measured live: a PR opened mid-embedding waited 6 s and was posted 25 s after it was opened; without slices it would wait for the whole job (~9 min for 14k nodes). Cost: a review may run while the repo is still partly embedded, so retrieval sees fewer vectors |
| D108 | Each push is reviewed on the whole PR diff, not just its own commits (answers Q18; user's choice, 2026-10-13) | Review only the files changed since the last reviewed head | It's the diff GitHub shows reviewers, findings must sit on PR diff lines anyway, and a later push can expose a bug in code from an earlier one. Topics (D104) keep the cost to the author at zero repeated comments; the cost is GPU time (7–30 s per push here) |
| D109 | An inline comment's range is clipped to the first diff hunk it overlaps | Require the finding's own first and last lines to be in a hunk | Measured on a real PR: the model flagged a swapped parameter as lines 2263–2321 (the whole function) around a 6-line hunk, and a removed guard as 842–850. Two of four true findings went to the review body. Clipped, all four were inline on the next PR |
| D110 | A review stores what it reviewed: the raw unified diff (capped at 1 MB) and its changed nodes (`seeds`: path, qualified name, kind, change, lines, signature changed) | Recompute the diff from git when asked | The API container has neither git nor the repos' mirrors, and Phase 7's hosted demo has only a database copy. Stored, a review is a complete record, the diff is exactly what the model saw (a branch can move on), and the graph view starts from the real seeds. Older reviews have neither, and their diff view says so (`not_recorded`) |
| D111 | Per-repo access: a repo is visible to a token whose `installations` claim (GitHub installation ids, filled by the dashboard from `GET /user/installations` at login) contains the repo's installation, and to logins in `API_ADMIN_LOGINS` (the user's choice for local and eval repos, which have no installation); every route checks through `api/access.py`; anything not visible is a 404 | Check GitHub on every request; let every signed-in user see local/eval repos | GitHub itself decides which installations a user can reach, once per login; the API only checks a signed claim, so no GitHub call per request. Admin is decided by the server from the login, never by a claim. A 404 for hidden repos doesn't reveal that a private repo exists |
| D112 | The anti-patterns editor (CRUD) is deferred with U5 (user's choice, 2026-10-13) | Build it in Phase 6 | Nothing reads the anti-patterns table until U5; an editor for unused data adds pages without showing anything |
| D113 | The dashboard's GitHub login is our own code (~200 lines in `apps/web`): GitHub's web flow with the App's OAuth client and a `state` cookie; at the callback, `GET /user` and `GET /user/installations`; then an httpOnly session cookie signed with `jose` (HS256, its own `SESSION_SECRET`, 8 h) holding the user and their installation ids. The GitHub user token is dropped. The server mints a 5-minute API token per call from the session | Auth.js, as §3 planned | Auth.js v5 is still a beta, and its examples predate Next 16's `proxy.ts` and Cache Components. We need one provider plus one extra call, which in Auth.js means callbacks around a flow you can't see. Our own is short, readable and tested (CSRF state, open-redirect guard, forged, expired and `alg: none` cookies). Without a stored GitHub token, a stolen cookie can't act on GitHub. The cost: new installations show after the next sign-in |
| D114 | Reviews keep a nullable `title`: the pull request's title when the review is queued (from the webhook), or a manual review's branch; a second Phase 6 migration (Q19, user's choice) | Keep D29's one migration per phase and show only `PR #N` | The title is what people recognise a change by, and the worker already had it for the prompt. One added nullable column is cheap; D29's point (never edit an applied migration) still holds. Older and eval reviews stay untitled |
| D115 | On a demo instance (`DEMO_MODE=true`), every signed-in user sees every repo (Q7: the demo asks for a GitHub login, user's choice; with D117 nobody signs in, so this is every visitor). Its database holds only the public demo repos exported to it (§14), so there is nothing private to hide. Outside demo mode, D111 is unchanged | Give visitors an installation; mark repos public | A visitor has no installation, so D111 would show them nothing. A per-repo public flag would add a column to protect data that, on a demo, is all public anyway. The rule lives in `api/access.py` with D111, and only demo mode turns it on |
| D116 | The demo is hosted on Vercel (the dashboard, and the API as a Python function with `DEMO_MODE`) and Supabase (Postgres with pgvector) (Q8, user's choice). The API runs on Postgres alone there (no Redis, worker or model). A daily Vercel cron calls `/health`, because Supabase pauses a free project after a week without activity | Neon + Render; one VPS | Two accounts, no servers to keep running, and both have free tiers. Every page works from stored data (D110). Checked 2026-10-13: Vercel allows Python functions up to 500 MB; Supabase's free tier has 500 MB of database and pauses after 7 idle days |
| D117 | The demo is open: no sign-in. With `DEMO_MODE=true` the dashboard calls the API as one anonymous visitor (`DEMO_VISITOR`), and the proxy, sign-in page and sign-in routes let everyone through. The API still verifies a token on every request, and only the dashboard holds `API_JWT_SECRET`; writes stay refused (403) | Require a GitHub login (Q7's first answer); a public API with no token | The demo exists to be seen by people who won't sign in for it, and its data is a public repo's. Keeping the token check means the API isn't an open endpoint, and a demo needs no GitHub OAuth or session secrets at all |
| D118 | The hosted demo API reaches Supabase through its **transaction-mode pooler** (port 6543) with `DATABASE_TRANSACTION_POOLER=true` (asyncpg's prepared-statement caches off, unique statement names); migrations and `publish_demo.py` keep the session pooler. Its Vercel functions run in Mumbai (`regions` in each project's `vercel.json`) | The session pooler (port 5432); the direct address; Vercel's default region | The session pooler allows 15 client connections per project, and Vercel's instances plus a review page's ~8 parallel calls ran out (`EMAXCONNSESSION`, a 500 for the call; 2026-10-15). The direct address is IPv6-only, which Vercel can't reach. From Washington a new connection took 1.2 s |
| D119 | CodeLens acts only on installations by accounts in `GITHUB_ALLOWED_ACCOUNTS` (comma-separated logins, case-insensitive); others are recorded as `ignored`, with nothing stored. **Unset means nobody** (user's choice, 2026-10-15). Push and PR events are checked against the mirrored installation every time; an uninstall is applied for anyone; `register` writes the App's owner into the list | Unset = everyone (today's behaviour); checking only at install time | Lets the App be public for a friend's repos without strangers' PRs running on the laptop's GPU. Failing closed matches the JWT and webhook secrets: one missing line can't open the GPU. Checking every event makes delisting immediate |
| D120 | Each account may start `GITHUB_REVIEWS_PER_HOUR` PR reviews an hour (default 10, 0 = no cap; user's choice). Counted: reviews started in the last hour plus those waiting, minus the PR's own queued ones that the new one supersedes. Over the cap, the review is inserted as `failed` with the reason and no job | A new `paused` review status; 30 a day; no cap | Bounds what one account can take from a single GPU. A failed head may be retried, so the next push or reopen reviews it once there's room. A new status would need a migration and dashboard changes for a rare case; a daily budget lets one busy morning use the whole day |
| D121 | One log setup (`core/logs.py`) for the API, workers, CLIs and the demo: stdout, `LOG_FORMAT=text` (default) or `json` (the demo's entrypoint defaults to json); a `ContextVar` adds `request_id` (API middleware, answered in `X-Request-ID`, a plain caller id kept) or `task_id`/`task` (Celery signals) to every line; one access line per request with `duration_ms`, replacing uvicorn's; passing `/health` probes not logged | structlog or python-json-logger; Celery's own logging; JSON everywhere | Stdlib only, a few dozen lines to explain. JSON is for a log service (Vercel's view filters by field); people reading `docker compose logs` want text. The ids are what make one request's or job's lines findable together |
| D122 | `/health` checks Ollama where it's used (not on a demo, not with fake providers): reachable, and the configured LLM and embedding models pulled (`name` or `name:latest`). Failing → `degraded` with 200; DB or Redis failing → `unavailable` with 503 | Ollama failing → 503; checking from the worker | The API works without Ollama (pages, webhooks; reviews wait), so a 503 would make compose and load balancers restart something that isn't broken. The API container reaches Ollama like the workers do, so one probe covers both |
| D123 | The public demo is rate-limited at Vercel's edge, not in code: the dashboard project allows 100 requests a minute per IP outside `/_next/`; the API project denies requests with no `authorization` header except `/health` | An in-memory limiter in `proxy.ts` (per instance only, so many instances multiply it); `@vercel/firewall`'s SDK (still needs a dashboard rule, plus a network call per request); Upstash Redis (another account); caching the demo's pages | A blocked request never starts a function, which is what the free plan's limits count (4 CPU-hours, 1M invocations a month; past them it pauses, never bills). A measured visit is 18 requests, so 100 a minute leaves people alone. Caching would cut the cost of a visit further and stays the next step if traffic ever needs it |

---

## 17. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **8B local model gives shallow or wrong reviews** | High | High | Strict validation + confidence gate + low comment cap; graph context does the heavy lifting; try qwen3:14b for offline eval runs; thinking mode (U6) |
| Model doesn't follow the JSON schema | Medium | Medium | Ollama `format` schema-constrained decoding; one repair retry; per-chunk failure isolation |
| VRAM pressure (LLM + embedder + desktop) | Medium | Medium | q8_0 KV, query embeddings on CPU, `gpu` queue concurrency 1, measure in Phase 2 step 6 |
| Latency over budget on CPU rerank / big PRs | Medium | Medium | `bge-reranker-base` (not v2-m3), top-N 100, chunk cap + summary mode |
| Seeded bugs too easy or unrealistic → misleading metrics (**confirmed in Phase 3 step 3: A0 ≈ B0; Q10**) | Medium | High | Several operators, a clean-PR noise metric, a small golden real-PR set later, a dev/test split |
| Small eval set → noisy comparisons | High | Medium | Paired bootstrap CIs; ≥ 60 cases; report uncertainty honestly |
| Weak call resolution for `obj.method()` | High | Medium | FTS on method names (U1), report resolution rates, light type inference (stretch) |
| Windows/Docker instability (Insider build) | Medium | Medium | Documented recovery (§1.5); everything also runs on plain Linux/CI |
| Demo hosting free tiers change | Medium | Low | Demo is plain Postgres + 2 containers; portable to any host |
| Scope creep (dashboard polish, more languages) | High | Medium | Phases have exit criteria; stretch is explicitly separate |

---

## 18. Open questions

| # | Question | Decide by |
|---|---|---|
| Q1 | ~~Which 3–4 OSS repos (and licenses) for eval sources?~~ **Answered 2026-10-11:** sqlite-utils, starlette, click, h3 (D68) | Phase 3 step 1 |
| Q2 | Is `qwen3:14b` (partial CPU offload) worth it for offline eval runs, given the slowdown? | Phase 3 step 3 (measure) |
| Q3 | Do module chunks help or add noise in retrieval? | Phase 4 step 1 (ablate) |
| Q4 | Test files: include with a lower prior, or exclude from context? | Phase 4 step 2 |
| Q5 | Graph `max_depth` 1 vs 2? | Phase 4 step 2 (ablate) |
| Q6 | Barrel-file re-export following depth for TS | Phase 1 step 3 (keep it to 1 level unless fixtures need more) |
| Q7 | ~~Demo login?~~ **Answered 2026-10-13: open, no login** (user's choice, changed the same day from "GitHub login required": the demo is proof for recruiters, and a sign-in page loses them; its data is public anyway; D117). Was: Dashboard auth for the demo: public read-only without login, or GitHub login required? | Phase 7 |
| Q8 | ~~Hosting?~~ **Answered 2026-10-13: Vercel + Supabase** (user's choice; D116). Was: Hosting providers for the demo | Phase 7 |
| Q9 | ~~Lower `max_findings_per_chunk` (e.g. 4)?~~ **Answered 2026-10-11:** no, keep 8 (D75; `evals/RESULTS.md`) | Phase 3 step 3 (ablate) |
| Q10 | ~~The seeded dataset can't show context helping?~~ **Answered 2026-10-11: counterfactual cases** (D78), built in Phase 3 step 4 | Before Phase 4 step 1 |
| Q11 | ~~Anchoring?~~ **Answered 2026-10-12: yes, adopted (D82).** Was: snap a finding to the nearest added line within a few lines, and let a finding sit next to a pure deletion? Validation drops both today: `remove_guard` 7/8 flagged but 0 posted, and 4 of B0's 6 lost bugs were 1–3 lines off. Upper bound from the test runs: recall A0 0.816 → 0.959, B0 0.714 → 0.939 | Phase 4 step 1 |
| Q12 | ~~Diff rendering?~~ **Answered 2026-10-12: no** (split rendering raised noise, F1 unchanged; D83). Was: the model reads moved lines as duplicates ("Duplicate parameter `type_arg`" when a parameter only moved). Render hunks differently (old/new side by side, or the new function whole with changed lines marked)? | Phase 4 |
| Q13 | ~~Caller-aware context?~~ **Answered 2026-10-12: no gain at qwen3:8b** (test +0.006 [−0.056, +0.069]; D83). Was: B0 flags 17/18 counterfactuals even with the safe callers in the prompt (56% of the time). Does a prompt that asks the model to check callers before claiming an API break, or a stronger model (Q2), use the context? | Phase 4 |
| Q14 | ~~Which lever next?~~ **Answered 2026-10-12: thinking mode first** (user's choice; D85). Was: The 8B model reacts to a diff's shape and ignores the callers that show a change is safe, even tagged and with a rule (D83). Which lever next: U6 thinking mode (a toggle already), Q2 a stronger model (qwen3:14b, partly on CPU), or a verification pass (for each api-contract finding, a short second call with only the change and its callers: "does any caller break?")? | Before Phase 4 step 3 |
| Q15 | ~~16k window for thinking?~~ **Answered 2026-10-12:** measured (Ollama now runs with flash attention + q8_0 KV); thinking still not adopted (D86). Was: Thinking needs a bigger output reserve than the 8k window can spare (13% of test reviews truncated at 3,000). Run Ollama with flash attention + q8_0 KV (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, D66: free on this GPU) and evaluate thinking at a 16k window with a ~6,000-token reserve? A server setting on the user's machine | Before Phase 4 step 4 |
| Q16 | ~~How to continue Phase 4?~~ **Answered 2026-10-12: (b) a verification pass, then (d) Phase 5** (user's choice; verification rejected, D87). Was: How to continue Phase 4, now that prompting (D83) and thinking (D86) don't get qwen3:8b to use context: (a) the retrieval upgrades U1–U5 as planned, accepting that F1 may not move; (b) a verification pass (a second, narrow call per API-contract finding); (c) a stronger model (Q2: qwen3:14b, partly on CPU); or (d) stop Phase 4 at anchoring and move to Phase 5 (GitHub App) | Now |
| Q17 | ~~Reviews ahead of bulk embedding?~~ **Answered 2026-10-13:** embedding runs in slices of 512 nodes and re-queues itself, so a review waits for at most one slice (D107); measured live, 6 s. Was: Should the `gpu` queue put reviews ahead of bulk embedding (Celery priorities, or a third queue that the GPU worker drains first)? Today it is FIFO, so a PR opened while a new repo is embedding (~8–9 min for 14k nodes, §5.7) waits for it (D88) | Phase 5 step 5 (measure end to end) |
| Q19 | ~~Store PR titles?~~ **Answered 2026-10-13: yes** (user's choice; D114). Was: Store the pull request's title on the review (a nullable `reviews.title`)? The worker already receives it for the prompt but doesn't keep it, so review lists show only `PR #4` and the commit. A small second Phase 6 migration (D29 plans one per phase); older reviews would stay untitled | Phase 6 step 4 (the review page wants it too) |
| Q18 | ~~Incremental per-push review?~~ **Answered 2026-10-13: no, the whole PR diff** (user's choice; D108). Was: Should a push be reviewed on its *incremental* diff (since the last reviewed head) rather than the whole PR? Today every push re-reviews the full PR diff; topics (D104) stop repeats, but the model still re-reads unchanged code, and its output varies from push to push | Phase 5 step 5 (watch real PRs) |
| Q20 | **Deferred (user, 2026-10-15): "run your own CodeLens"**, for a friend to review his repos on his own machine instead of the owner's laptop. Feasible: his own GitHub App (`python -m core.github register`) and smee channel, the Docker stack, Ollama with the two models (~6 GB; needs an 8 GB GPU or Apple silicon for minutes-not-seconds reviews). Recommended form: one guided setup command (check Docker and Ollama, pull models, register his App, write `.env` with his login allowed, start, print the install link), a few days of work; a desktop app (Electron/Tauri installers, signing, updates) is weeks and mostly packaging. Not started | after Phase 7 |

---

## 19. Conventions and definition of done

**Git:** trunk-based on `main`, with short-lived branches `phase-N/step-M-short-name`.
Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`), and a
PR per step (it can be reviewed by CodeLens itself once Phase 5 lands). *The folder isn't
a git repo yet; `git init` comes before step 3.*

**Code:** type hints everywhere; Pydantic at every I/O boundary; small modules (rough
guideline: under 300 lines); comments explain *why*, not *what*; no secrets in code;
config via env.

**Docs:** update this file's status line and §9 checkmarks as steps land. Record every
upgrade decision in `evals/RESULTS.md`. Keep the README quick-start working.

**A step is done when:** the code and tests are merged, CI is green, ruff is clean,
docs are updated, there's a "what I built / how to run / what to verify" note, and
you've reviewed it.

**The project is done (v1) when:**
1. A GitHub App reviews PRs on a real repo with inline comments, using local models.
2. The dashboard shows live reviews, evidence, the call graph and the retrieval trace.
3. `evals/RESULTS.md` holds the ablation table with CIs, telling the
   naive → naive+ story.
4. The public demo URL works, and the README has the architecture diagram, results
   table and demo video.
