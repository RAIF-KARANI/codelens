# Eval results

> **⚠ The numbers below are contaminated and pending a rerun (D77).**
> - **What leaked:** until Phase 3 step 4, the runner committed each case as `eval case <id>`. The id names the operator (for example `click.raise_to_none.1acf4f8e`), and commit subjects are part of the prompt. So the model was told which bug to look for, in both A0 and B0.
> - **Why the numbers can't be trusted:** recall is inflated, and the A0 ≈ B0 conclusion may not hold.
> - **Status:** the runner is fixed (commits now say `Update <file>`). A0 and B0 have not yet been rerun, on the extended dataset that now includes counterfactuals.

The log of measured pipeline configs. Each upgrade in Phase 4 is adopted or rejected here, through the adoption gate (planning.md §10.5).

The gate has three conditions:
- F1 rises, with the lower bound of its paired 95% CI above 0.
- Noise per clean PR rises by at most 0.1.
- p95 latency stays within budget.

Reproduce any row with:

```bash
python -m evals.run --config configs/pipeline/<config>.yaml --split <split> --name <run>
python -m evals.report evals/results/<run> --baseline evals/results/<baseline-run>
```

## Setup (all runs below)

| | |
|---|---|
| LLM | `qwen3:8b`, digest `500a1f067a9f…`, num_ctx 8192, temperature 0.1, seed 42, no thinking |
| Embedder | `qwen3-embedding:0.6b`, digest `ac6da0dfba84…`, on the GPU at num_ctx 2048 |
| Hardware | RTX 4060 Laptop (8 GB), Ollama 0.40.2 on Windows |
| Dataset | `evals/datasets` (Phase 3 step 1). Split hash: test `b38f9c757cca`, dev `0725930db611` |
| Code | `main` @ `2b3f4f7`, plus the step 3 dataset fix (a `rename_local` naming fix that touches one clean case) |
| Runs | 2026-10-11. 0 errors, 0 failed reviews |

---

## Baselines on the test split (65 cases: 49 seeded, 16 clean)

| metric | A0 (diff only) | B0 (vector retrieval) | B0 − A0, paired 95% CI |
|---|---|---|---|
| **F1** | 0.879 [0.80, 0.94] | 0.864 [0.78, 0.93] | −0.015 [−0.06, +0.02] |
| precision | 0.952 [0.89, 1.00] | 0.974 [0.92, 1.00] | +0.022 [−0.00, +0.08] |
| recall | 0.816 [0.70, 0.91] | 0.775 [0.65, 0.88] | −0.041 [−0.10, 0.00] |
| critical recall | 0.966 [0.89, 1.00] | 0.931 [0.83, 1.00] | −0.035 [−0.10, 0.00] |
| noise per clean PR (target ≤ 0.3) | 0.438 [0.10, 0.80] | 0.438 [0.18, 0.67] | 0.000 [−0.29, +0.33] |
| oracle recall@5 | 0 (no retrieval) | 0.476 [0.34, 0.62] | |
| oracle recall@10 | 0 | 0.539 [0.40, 0.68] | |
| oracle packed recall | 0 | 0.539 [0.40, 0.68] | |
| MRR | 0 | 0.334 [0.23, 0.45] | |
| latency p50 / p95 | 5.9 s / 9.7 s | 14.4 s / 16.5 s | |
| tokens in / out (mean) | 961 / 167 | 2,683 / 176 | |

**B0 does not pass the gate against A0.** Its F1 is not higher, so with these numbers retrieval adds nothing measurable.

### Per operator (test)

| operator | A0 | B0 |
|---|---|---|
| raise_to_none | 8/8 found (9 posted) | 8/8 (8 posted) |
| drop_param | 8/8 (9) | 7/8 (8) |
| reorder_params | 8/8 (8) | 8/8 (8) |
| drop_await | 7/8 (7) | 7/8 (7) |
| remove_guard | **0/8** (0) | **0/8** (0) |
| sql_format | 5/5 (5) | 5/5 (5) |
| exception_type | 4/4 (4) | 3/4 (3) |
| *clean:* rename_local | 7 findings on 8 PRs | 5 on 8 |
| *clean:* extract_return | 0 on 8 | 2 on 8 |

**Matches lost to validation:** every one was dropped as `not_on_changed_lines`. A finding counts as lost if validation dropped it but it would have matched a missed bug.
- A0 lost 8 findings: in 7 of the 8 `remove_guard` cases, plus 1 elsewhere.
- B0 lost 11: in 7 of 8 `remove_guard` cases, plus 3 elsewhere.

---

## What the numbers say

1. **The seeded bugs are visible from the diff alone, so this dataset cannot yet show context helping.**
   - A0 never sees a caller or callee, yet it finds as many bugs as B0, case for case.
   - Removing a parameter, swapping `raise` for `return None`, or dropping an `await` all look suspicious whatever the callers do. A reviewer flags them on sight.
   - B0 retrieves the needed context about half the time (oracle recall@10 0.54), but that changes no outcome.
   - This is risk §17 in planning.md ("seeded bugs too easy"), now measured. It is **Q10**: the dataset needs cases where the diff alone is ambiguous.
2. **`remove_guard` is invisible to both, by construction (D70).**
   - The model *did* flag the deleted guard in 7 of 8 test cases, under both A0 and B0.
   - Validation dropped every one of those findings: a pure deletion has no added line to anchor a comment to.
   - The fix is to anchor a finding at a line next to a deletion, as GitHub allows on any line of a diff hunk. That is a ready-made candidate upgrade worth about +14 recall points (7 of 49).
3. **Noise is above target (0.44 vs ≤ 0.3).**
   - Clean renames attract confident, wrong "critical bug" findings. For example, *"Incorrect variable name used in etag comparison"* on a correct rename, at confidence 1.0.
   - Confidence is no filter here: the model says 1.0 or 0.8 almost regardless of correctness.
4. **Retrieval costs about 2.8× the input tokens and about 2.4× the latency** (p50 14.4 s vs 5.9 s) for no quality gain on this dataset.

## Q9: max 4 vs 8 findings per chunk (dev split, 32 cases)

| metric | B0 (max 8) | B0, max 4 | delta, paired 95% CI |
|---|---|---|---|
| F1 | 0.844 [0.73, 0.94] | 0.808 [0.67, 0.93] | −0.036 [−0.10, 0.00] |
| precision | 0.905 [0.79, 1.00] | 0.826 [0.63, 1.00] | −0.079 [−0.18, 0.00] |
| recall | 0.792 | 0.792 | 0 |
| noise per clean PR | 0.625 [0.25, 1.00] | 1.000 [0.29, 2.00] | +0.375 [0.00, +1.20] |
| output tokens (mean) | 186 | 215 | |

**Rejected: max_findings_per_chunk stays at 8.**
- The cap goes into the prompt, so it changes what the model writes, not just how much.
- With 4, the model posted *more* false findings (7 vs 4 on renames), and F1 fell.
- Q9's premise came from the Phase 2 real-PR review, where the model always filled all 8 slots. It doesn't hold for single-function changes: here B0 averages about 1 finding per case.

The dev split also confirmed point 1 before the test runs:

| metric | A0 | B0 |
|---|---|---|
| F1 | 0.884 | 0.844 |
| recall | 0.792 | 0.792 |

## Caveats

- **Latency depends on the machine's state.**
  - Partway through the dev runs, the laptop GPU dropped to a power-capped state, at about 47 W instead of its usual draw. Per-case time doubled from there, with identical token counts.
  - Dev latencies are therefore inflated, and latency is never compared across runs taken at different times. Quality numbers are unaffected (fixed seed, low temperature).
  - The test runs ran back to back.
- **Some groups are small.** Bootstrap CIs are wide at 49 seeded and 16 clean cases, and `sql_format` (5) and `exception_type` (4) are too small to read on their own.
- **Results are for one 8B local model.** A stronger model might use context differently. Q2 (`qwen3:14b`) is unmeasured.
