# evals

The evaluation harness (planning.md §10). Every pipeline upgrade must beat the
baseline here before it is adopted.

**Status:** Phase 3 is done: the dataset, the runner, the scorecard and the A0/B0
baselines in [`RESULTS.md`](RESULTS.md).

## Running an eval

```bash
docker compose up -d postgres redis          # and Ollama running natively
python -m evals.run --config configs/pipeline/b0.yaml             # dev split (default)
python -m evals.run --config configs/pipeline/b0.yaml --split test --name b0-test
python -m evals.run ... --operator drop_await --limit 5           # a quick slice
python -m evals.run ... --name b0-test --resume                   # after an interruption
python -m evals.report evals/results/b0-test --baseline evals/results/a0-test
```

The runner:

1. Checks out each source it needs.
2. Indexes and embeds the source as `eval/<source>`. The first time takes about 1 to 1.5 minutes per repo; after that it is a no-op.
3. Turns each case into a commit with git plumbing, so nothing is checked out and the same case always gives the same SHA.
4. Runs the review pipeline on that commit (`source=eval`).
5. Appends one record per case to `evals/results/<run>/cases.jsonl`.
6. At the end, writes `scorecard.json` and `scorecard.md` next to the records.

**Timing.** About 15–40 s per case at an 8k window: roughly 20 min for the dev split and 45 min for test.

**Use dev while iterating.** Test numbers are for reporting only.

## Scoring (`metrics.py`, planning §10.2)

**When a finding matches a planted bug:** same file, lines within ±3, a compatible category (D72), and a severity at most one level below the expected one. Matching is greedy and one-to-one, so a second comment on the same bug is a false positive.

| Metric | Meaning |
|---|---|
| precision / recall / F1 | Over seeded cases; F1 is primary. Precision is `n/a` when nothing was posted |
| critical_recall | Recall restricted to critical expectations |
| noise_per_clean_pr | Findings posted on behaviour-preserving changes (target ≤ 0.3) |
| oracle_recall@k, mrr | Did retrieval rank the oracle nodes in its top k (by similarity, across chunks)? |
| oracle_packed_recall | Did the oracle nodes make it into the prompt? |
| latency p50/p95, tokens | Per case, wall clock, including retrieval |

**Confidence intervals and comparisons:**
- Every quality metric gets a 95% bootstrap interval (1,000 resamples, seeded).
- `--baseline` adds paired deltas (the same cases resampled for both runs) and checks the adoption gate (§10.5).

**Matches lost to validation.** The scorecard also counts findings that validation dropped but that would have matched a missed bug. This is how the `remove_guard` blind spot (D70) shows up.

## The dataset

```bash
python -m evals.generate_seeded            # ~1 min; replaces datasets/{seeded,clean}
```

The generator:

1. Checks out each repo in [`sources.yaml`](sources.yaml) at its pinned commit, into `data/evals/sources/` (gitignored).
2. Parses each repo with `core.parser`, the same parser the indexer uses.
3. Runs every operator over every non-test function.
4. Picks cases round-robin across the repos.

Running it twice gives byte-identical output.

| Repo | License | Language | Why |
|---|---|---|---|
| sqlite-utils | Apache-2.0 | Python | the only candidate with real parameterised SQL |
| starlette | BSD-3-Clause | Python | async-heavy |
| click | BSD-3-Clause | Python | best call resolution (60% of in-repo calls) |
| h3 | MIT | TypeScript | ESM imports, async, mid-size |

### Seeded bugs: visible only with context

Each operator mutates a function only where catching the bug needs *other* code:
- a caller that uses the result, passes the argument, or catches the exception;
- or an async callee.

That code becomes the case's **oracle**, so the eval can measure whether retrieval
found what the reviewer needed.

| Operator | Mutation | Expected | Oracle |
|---|---|---|---|
| `raise_to_none` | `raise X(...)` → `return None` (`return undefined`) | bug, critical | callers that use the return value |
| `drop_param` | remove a defaulted parameter; its default becomes a local | api-contract, critical | callers that pass it |
| `reorder_params` | swap two adjacent parameters of the same shape and type | api-contract, warning | callers that pass both by position |
| `drop_await` | remove `await` before a call to an in-repo async function | concurrency, critical | the async callee |
| `remove_guard` | delete `if x is None: return` / `if (!x) return` before `x` is used | bug, warning | where `x` comes from (the callee that produced it, or the callers) |
| `sql_format` | `execute("… = ?", [v])` → `execute(f"… = '{v}'")` (Python) | security, critical | the DB helper, when it is in the repo |
| `exception_type` | raise a different class than the one callers catch | error-handling, warning | callers with the handler |

### Counterfactual cases: the same diff, made safe by context (Q10, D78)

A counterfactual has **exactly the diff of a seeded bug**. It differs only in its base commit, where a *prep* commit has already made every caller compatible.

| Counterfactual | Prep commit (stored as `prep.diff`) |
|---|---|
| `drop_param_cf` | removes that argument at every call site |
| `reorder_params_cf` | swaps the two arguments at every positional call |
| `exception_type_cf` | makes every handler also catch the new class |

A reviewer who reads only the diff cannot tell it from the bug, so it flags it; only the context shows nothing is wrong.

- **Only private functions qualify,** and only when every `name(` in the repo is a resolved call site. Outside users of a public function could still pass the old arguments, so "safe" would be false.
- **Scoring:** a finding on a counterfactual is a false positive in precision (D79), and is also reported as `false_alarms_per_counterfactual`.
- **Retrieval:** with retrieval on, the runner first indexes the prep commit from a worktree, as `eval-cf/<source>`. Otherwise retrieval would show the old callers.

### Clean cases (expect no findings)

These are behaviour-preserving edits:
- `rename_local`: rename a local variable, following each language's naming convention.
- `extract_return`: `return expr` becomes `result = expr` followed by `return result`.

### Checks on every case

Each case passes all of these, or it isn't written:
- **It still parses.** tree-sitter finds no new syntax errors; for Python, `ast.parse` also succeeds.
- **The patch applies.** `git apply --check` passes on the pinned checkout.
- **A correct finding can be posted.** If the patch adds lines, the expected range must touch one, because the reviewer only posts on added lines.
  - `remove_guard` is a pure deletion, so B0 cannot post on it at all. Keeping those cases measures that blind spot (D70).

## Files

```
datasets/
  seeded/<id>/case.yaml   source, sha, file, function, operator, description,
                          expected [{file, line_start, line_end, category, severity}],
                          oracle [{qualified_name, ordinal, role}], split (dev|test)
  seeded/<id>/patch.diff  byte-exact (git won't normalise it; see .gitattributes)
  clean/<id>/...          the same, with empty expected and oracle
  summary.md              case counts per operator, source and split
```

Case ids are `<source>.<operator>.<hash of file + function>`.

The split is stratified per (kind, operator), with 30% of cases in dev. Prompts and weights are tuned on **dev** only, and reported numbers come from **test**.

## Code

| Module | Role |
|---|---|
| `sources.py` | `sources.yaml` model; shallow checkout of the pinned commit |
| `graph.py` | parse result + callers/callees + each function's syntax tree (`Target`) |
| `syntax.py` | tree-sitter helpers: own-body walk, parameters, call sites, try/except |
| `operators.py` | the 7 seeded operators and 2 clean edits |
| `edits.py` | byte edits → new file + expected lines; `git apply`-able diffs |
| `cases.py` | `case.yaml` model, read/write |
| `generate_seeded.py` | the CLI: candidates → selection → checks → split → write |
| `run.py` | the runner CLI: prepare sources, case commits, run_review, records |
| `records.py` | `run.json` / `cases.jsonl` models |
| `metrics.py` | matching, per-case scores, aggregates, bootstrap (plain + paired) |
| `report.py` | scorecard JSON + markdown, baseline comparison, adoption gate |

Tests are in `tests/`, run on a toy Python + TypeScript repo (the runner test uses Postgres and fake models). CI runs them; it runs no model evals (no GPU, §10.6).
