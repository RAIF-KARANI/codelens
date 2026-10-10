# CodeLens: Engineering Plan

> **How this relates to `idea.md`:** `idea.md` explains *what* CodeLens is and *why*.
> This file is the plan of record for *how* and *in what order* we build it. Where the
> two disagree, **this file wins**. It supersedes `idea.md` §14 (roadmap) and adds
> decisions D18+.

**Last updated:** 2026-10-10 · **Current position:** Phase 1 complete (PRs #1–#3); Phase 2 steps 1–3
done (PRs #4–#6); step 4 (diff parsing + seeds) built and awaiting review; next is step 5 (prompts, generation, validation, `core.review`).

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

**VRAM (8 GB)**: estimated, to be measured in Phase 2:

| Item | Size |
|---|---|
| `qwen3:8b` weights (Q4_K_M) | ~5.2 GB |
| KV cache at `num_ctx = 16384`, q8_0 KV | ~1.2 GB (36 layers × 8 KV heads × 128 dim × 2 (K+V) × 1 byte × 16k tokens) |
| Windows desktop / display | ~0.5–1 GB |
| **Headroom** | ~0.6–1 GB |

Required Ollama host settings (set once, as Windows user env vars, then restart Ollama):
`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` (halves KV-cache memory),
plus `OLLAMA_KEEP_ALIVE=30m`.

The embedding model also runs through Ollama. To avoid evicting the LLM from VRAM
during a review, **query-time embeddings run on CPU** (Ollama option `num_gpu: 0` for
that request). Bulk indexing embeddings use the GPU, since no LLM is loaded then.
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
| Frontend extras | React Flow (graph), react-diff-view (diff), react-markdown (comments), Auth.js (GitHub OAuth) | One library per job | Cytoscape.js (graph) |
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
| 5 | **`installations`** | `id, github_installation_id, account_login, account_type, created_at, suspended_at?` + `repos.installation_id` | GitHub App |
| 5 | **`webhook_deliveries`** | `delivery_id PK, event, received_at, processed_at?` | Idempotent webhook handling |
| 6 | **`finding_feedback`** | `finding_id, github_user_id, vote (+1/-1), created_at`; unique per user | 👍/👎 and suppression |

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
- **Batching:** 32 chunks per request; one in-flight request (single GPU). Retries with
  exponential backoff on timeouts or 5xx.
- **Throughput (measured 2026-10-10, RTX 4060, defaults, model 100% on GPU):**
  **29 nodes/s ≈ 4.8k tokens/s** (SQLAlchemy, 3,000 nodes in 105 s, ~166 tokens per
  chunk), so ~8 min for a 14k-node repo. 2.2% of chunks hit the 1,024-token cap.
  That's slow for a 0.6B model; Ollama seems to process a batch's inputs
  sequentially. Tuning (concurrent requests, `OLLAMA_NUM_PARALLEL`, batch size) is
  part of Phase 2 step 6.
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

| Queue | Concurrency | Tasks |
|---|---|---|
| `index` | 2 | sync, parse, relink, tsv, pagerank |
| `gpu` | **1** | embedding batches and LLM generation. Serialising them prevents VRAM thrash |
| `review` | 2 | review orchestration (it awaits `gpu` work) |

Each repo has a lock (a Redis `SET NX` with a TTL), so two index runs never overlap
on the same repo.

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

### 6.4 Latency budget (estimates to replace with measurements)

| Step | Estimate (4060, qwen3:8b) |
|---|---|
| Diff + seeds + retrieval + expansion (SQL) | < 1 s |
| Query embeddings (CPU) | 1–3 s |
| Rerank 100 pairs (CPU, base model) | 3–8 s |
| Prompt processing (~13k tokens) | ~8–15 s |
| Generation (~800 tokens non-thinking) | ~20–30 s |
| **Per chunk** | **~35–55 s** → target **≤ 2 min** for a 2-chunk PR |

---

## 7. API surface

**Base path:** `/api/v1`, except `/health` and `/webhooks/*`. JSON in and out, Pydantic
schemas in `api/schemas/`. Errors use the shape
`{"error": {"code": "not_found", "message": "...", "details": {...}}}`.

**Auth:** the dashboard's Next.js server calls the API with `Authorization: Bearer <JWT>`
(HS256, shared `API_JWT_SECRET`, 5-minute expiry, claims: `sub` = GitHub user ID,
`login`). The API checks repo access against installation membership. Webhooks are
authenticated by HMAC. With `DEMO_MODE=true`, all mutating endpoints return `403`.

| Method | Path | Auth | Purpose | Phase |
|---|---|---|---|---|
| GET | `/health` | none | Readiness: DB + Redis (+ Ollama, reranker once used) | ✅ 1 |
| POST | `/webhooks/github` | HMAC | `installation*`, `push`, `pull_request` events → `202` + enqueue | 5 |
| GET | `/api/v1/repos` | JWT | Repos visible to the user (status, last index, last review) | 5 |
| GET | `/api/v1/repos/{repo_id}` | JWT | Repo detail + latest index stats | 5 |
| POST | `/api/v1/repos/{repo_id}/index` | JWT | Trigger a re-index → `202 {index_run_id}` | 5 |
| GET | `/api/v1/repos/{repo_id}/index-runs` | JWT | Index history | 5 |
| GET | `/api/v1/repos/{repo_id}/reviews` | JWT | Paginated reviews (`?status=&cursor=`) | 5 |
| POST | `/api/v1/reviews` | JWT | Manual review `{repo_id, pr_number}` or `{repo_id, base, head}` → `202 {review_id}` | 5 |
| GET | `/api/v1/reviews/{review_id}` | JWT | Review + findings (posted; `?include_dropped=true`) | 5 |
| GET | `/api/v1/reviews/{review_id}/events` | JWT | **SSE** stream (replay + live; `Last-Event-ID`) | 5 |
| GET | `/api/v1/reviews/{review_id}/diff` | JWT | Unified diff for the viewer | 6 |
| GET | `/api/v1/reviews/{review_id}/graph` | JWT | Nodes + edges around the seeds for React Flow `{nodes:[{id,label,kind,depth,centrality,is_seed}], edges:[{src,dst,kind,resolved}]}` | 6 |
| GET | `/api/v1/reviews/{review_id}/trace` | JWT | Per-chunk context items with all stage scores; dropped findings with reasons | 6 |
| GET | `/api/v1/nodes/{node_id}` | JWT | Node code + metadata (evidence drawer) | 6 |
| POST | `/api/v1/findings/{finding_id}/feedback` | JWT | `{vote: 1 \| -1}` | 6 |
| GET / POST / PATCH / DELETE | `/api/v1/anti-patterns[/{id}]` | JWT | Library CRUD (`?repo_id=` or global) | 6 |
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
├── README.md                       ✅ setup & usage
├── pyproject.toml / uv.lock        ✅ uv workspace, ruff, pytest
├── docker-compose.yml              ✅ postgres, redis, migrate, api, worker (+ reranker 📋4)
├── .env.example                    ✅
├── .github/workflows/ci.yml        ✅
├── configs/
│   └── pipeline/                   📋2 b0.yaml (baseline), u1_fts.yaml, … full.yaml, default.yaml
├── anti_patterns/                  📋4 global anti-pattern YAML library
├── apps/web/                       ✅ Next.js (empty) → 📋6 dashboard
│   └── src/
│       ├── app/(auth)/ …           📋6 Auth.js routes
│       ├── app/repos/[id]/ …       📋6
│       ├── app/reviews/[id]/{page,graph,trace}/   📋6
│       ├── components/{diff,findings,graph,trace}/ 📋6
│       └── lib/{api.ts,sse.ts,auth.ts}            📋6
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
│   │   ├── github/                 📋5 app auth, client, webhook verification, review posting
│   │   └── review_cli.py           📋2 `python -m core.review`
│   └── tests/
│       ├── fixtures/repos/         🔜1 py_basic/, js_basic/, ts_basic/ (+ expected.json)
│       ├── test_parser_*.py        🔜1
│       └── test_migrations.py …    ✅
├── services/
│   ├── api/src/api/
│   │   ├── main.py checks.py dependencies.py   ✅
│   │   ├── routes/health.py        ✅
│   │   ├── routes/{webhooks,repos,reviews,anti_patterns,search}.py   📋4–6
│   │   ├── schemas/                📋5 Pydantic I/O models
│   │   ├── auth.py                 📋5 JWT verification
│   │   └── sse.py                  📋5 Redis pub/sub → SSE
│   ├── worker/src/worker/
│   │   ├── celery_app.py tasks.py  ✅ (ping)
│   │   └── tasks/{index,embed,review,graph}.py   📋5
│   └── reranker/                   📋4 FastAPI + sentence-transformers CrossEncoder (CPU)
└── evals/
    ├── datasets/{seeded,clean,golden}/<case_id>/{case.yaml, patch.diff}   📋3
    ├── sources.yaml                📋3 pinned OSS repos (name, url, sha, license)
    ├── generate_seeded.py          📋3 mutation operators → cases
    ├── run.py metrics.py report.py 📋3
    ├── results/                    📋3 JSON per run (gitignored except summaries)
    └── RESULTS.md                  📋3 the adopted/rejected upgrade log + tables
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
| 4 🔍 | Diff parser, head overlay, seed mapping | Unit tests on tricky diffs (renames, deletions, new files) |
| 5 | Prompts, structured output, validation, packing (vector only), trace writer, `core.review` CLI | Reviewing a local branch prints validated findings; trace rows written |
| 6 | Measure VRAM / latency / tokens; fill in §1.3 and §6.4 | Numbers recorded |

### Phase 3: Evaluation harness (measure before upgrading)
| Step | Deliverable | Exit criteria |
|---|---|---|
| 1 | `sources.yaml` (3–4 permissively licensed OSS repos, pinned SHAs) + seeded-bug generator (§10.1) | ≥ 60 seeded cases + ≥ 20 clean cases generated reproducibly |
| 2 | Runner (`evals.run`), matching + metrics, bootstrap CIs, report | One command produces a scorecard JSON + markdown |
| 3 | Run **diff-only (A0)** and **B0** | Baseline numbers committed to `evals/RESULTS.md` |

### Phase 4: The "+" upgrades, one per step, each gated by evals
| Step | Upgrade | Extra deliverables |
|---|---|---|
| 1 | U1 FTS + RRF | `identifiers.py`, tsv builder, fusion SQL, `--mode fts/hybrid` |
| 2 | U2 graph expansion | Recursive CTE, hub control, graph-based fused list |
| 3 | U3 reranker | `services/reranker`, compose service, HTTP client, blend weights |
| 4 | U4 centrality prior | PageRank job, score term |
| 5 | U5 anti-patterns | YAML library + sync + retrieval + `[AP]` citations |
| 6 | U6 thinking mode | `think: true` ablation + latency/token cost |

Every step ends by running the eval, writing a `RESULTS.md` entry (adopt or reject,
with numbers), and updating `configs/pipeline/default.yaml`.

### Phase 5: GitHub App and delivery
App registration doc (manifest), webhook endpoint (HMAC, delivery dedupe),
installation tokens, mirror clone, Celery tasks and queues (§5.10), index on
install/push, review on PR events, posting reviews (§9.1 of `idea.md`), Redis
pub/sub → SSE, migration (`installations`, `webhook_deliveries`). Local webhook
delivery uses a tunnel (smee.io or `gh webhook forward`).
**Exit:** opening a PR on a test repo yields inline comments within the latency
budget; a re-push doesn't duplicate comments.

### Phase 6: Dashboard
Auth.js (GitHub) → JWT to the API; pages: repos, repo, reviews, **review detail**
(diff + inline findings + live SSE + evidence drawer + 👍/👎), **graph** (React Flow),
**trace** (stage scores), anti-patterns CRUD.
**Exit:** watch a review stream live and click any finding through to its evidence and
graph.

### Phase 7: Demo, observability, hardening
`DEMO_MODE`, demo data export/import, hosted deploy (§15), optional Langfuse
integration, rate limits and budgets, README with architecture diagram, results
table, and a demo video.
**Exit:** a public URL showing real reviews; the README tells the eval story.

### Stretch
U7 sub-chunking · a feedback loop (suppress patterns users vote down) · mining
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
pinned by SHA in `evals/sources.yaml`.

### 10.2 Metrics

| Level | Metric | Definition |
|---|---|---|
| Retrieval | **Oracle recall@k** (k = 5, 10, 20) | Share of oracle nodes present in the packed context (and in top-k before packing) |
| Retrieval | MRR | 1 / rank of the first oracle node |
| Findings | **Precision, recall, F1** (primary = F1) | A prediction matches an expectation if: same file, line ranges overlap within ±3, compatible category, severity ≥ expected − 1 level. One-to-one greedy matching |
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
noise per clean PR doesn't increase by more than 0.1 · p95 latency within budget (or
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

### 11.4 Ollama host (Windows user env vars)
`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`.
Restart Ollama after setting them.

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

---

## 17. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **8B local model gives shallow or wrong reviews** | High | High | Strict validation + confidence gate + low comment cap; graph context does the heavy lifting; try qwen3:14b for offline eval runs; thinking mode (U6) |
| Model doesn't follow the JSON schema | Medium | Medium | Ollama `format` schema-constrained decoding; one repair retry; per-chunk failure isolation |
| VRAM pressure (LLM + embedder + desktop) | Medium | Medium | q8_0 KV, query embeddings on CPU, `gpu` queue concurrency 1, measure in Phase 2 step 6 |
| Latency over budget on CPU rerank / big PRs | Medium | Medium | `bge-reranker-base` (not v2-m3), top-N 100, chunk cap + summary mode |
| Seeded bugs too easy or unrealistic → misleading metrics | Medium | High | Several operators, a clean-PR noise metric, a small golden real-PR set later, a dev/test split |
| Small eval set → noisy comparisons | High | Medium | Paired bootstrap CIs; ≥ 60 cases; report uncertainty honestly |
| Weak call resolution for `obj.method()` | High | Medium | FTS on method names (U1), report resolution rates, light type inference (stretch) |
| Windows/Docker instability (Insider build) | Medium | Medium | Documented recovery (§1.5); everything also runs on plain Linux/CI |
| Demo hosting free tiers change | Medium | Low | Demo is plain Postgres + 2 containers; portable to any host |
| Scope creep (dashboard polish, more languages) | High | Medium | Phases have exit criteria; stretch is explicitly separate |

---

## 18. Open questions

| # | Question | Decide by |
|---|---|---|
| Q1 | Which 3–4 OSS repos (and licenses) for eval sources? | Phase 3 step 1 |
| Q2 | Is `qwen3:14b` (partial CPU offload) worth it for offline eval runs, given the slowdown? | Phase 3 step 3 (measure) |
| Q3 | Do module chunks help or add noise in retrieval? | Phase 4 step 1 (ablate) |
| Q4 | Test files: include with a lower prior, or exclude from context? | Phase 4 step 2 |
| Q5 | Graph `max_depth` 1 vs 2? | Phase 4 step 2 (ablate) |
| Q6 | Barrel-file re-export following depth for TS | Phase 1 step 3 (keep it to 1 level unless fixtures need more) |
| Q7 | Dashboard auth for the demo: public read-only without login, or GitHub login required? | Phase 7 |
| Q8 | Hosting providers for the demo | Phase 7 |

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
