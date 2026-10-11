# Eval results

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

## Setup

| | |
|---|---|
| LLM | `qwen3:8b`, digest `500a1f067a9f…`, num_ctx 8192, temperature 0.1, seed 42, no thinking |
| Embedder | `qwen3-embedding:0.6b`, digest `ac6da0dfba84…`, on the GPU at num_ctx 2048 |
| Hardware | RTX 4060 Laptop (8 GB), Ollama 0.40.2 on Windows |
| Dataset | test split, hash `6aa58f963590`: 49 seeded + 18 counterfactual + 16 clean = 83 cases (Phase 3 step 4) |
| Code | `main` @ `9992f7f`, plus the step 5 worktree-path fix. That fix is runner plumbing and does not affect what is reviewed |
| Runs | 2026-10-11, A0 then B0. 0 errors, 0 failed reviews |

**Current default** (`configs/pipeline/default.yaml`): B0 + A1 anchoring.

| Step | Upgrade | Test F1 vs predecessor (paired 95% CI) | Decision |
|---|---|---|---|
| Phase 4 step 1 | A1 anchoring | 0.654 → **0.767**, +0.113 [+0.040, +0.194] | **adopted** |

---

## Phase 4 step 1: A1 anchoring (Q11), adopted

**What changed:** validation only. Prompts, retrieval and model calls are identical to B0.
- **`anchor_window: 3`:** a finding up to 3 lines off a changed line is moved onto the nearest one, instead of being dropped.
- **`anchor_deletions: true`:** the lines on either side of a pure deletion count as changed lines.

Configs: `b0_anchor.yaml`, plus `a0_anchor.yaml` for reference.

**Dev check first (D76, 41 cases):**
- F1 rose from 0.704 to 0.814, +0.110 [+0.034, +0.219].
- Recall rose from 0.792 to 1.000.
- Noise and counterfactual false alarms were unchanged.
- Raw model output was identical in 40 of 41 cases, so the delta is anchoring alone.

### Test split (83 cases, paired against the baselines above)

| metric | B0 | B0 + A1 | Δ (95% CI) | A0 | A0 + A1 | Δ (95% CI) |
|---|---|---|---|---|---|---|
| **F1** | 0.654 | **0.767** | **+0.113 [+0.040, +0.194]** | 0.741 | **0.821** | +0.080 [+0.026, +0.145] |
| precision | 0.603 | 0.648 | +0.044 [−0.013, +0.100] | 0.678 | 0.706 | +0.028 |
| recall | 0.714 | **0.939** | +0.225 [+0.111, +0.349] | 0.816 | 0.980 | +0.163 |
| critical recall | 0.828 | 0.931 | +0.103 | 0.966 | 1.000 | +0.035 |
| noise per clean PR | 0.438 | 0.438 | 0.000 | 0.312 | 0.312 | 0.000 |
| false alarms per counterfactual | 0.944 | 1.000 | +0.056 | 1.000 | 1.000 | 0.000 |
| matches lost to validation | 11 | **0** | | 7 | 0 | |
| latency p50 / p95 | 14.3 / 30.2 s | 13.1 / 23.5 s | | 6.1 / 9.4 s | 4.7 / 5.7 s | |

**The gate is passed** (F1 CI above 0, noise unchanged), so A1 is adopted into `default.yaml`.

- **`remove_guard` went from 0 to 7 of 8.** The deletion borders made the model's existing findings postable.
- **Snapping moved 8 findings: 5 real catches and 3 false positives.** The false positives were on counterfactuals; the snapped findings there are the same "parameter removed" claims the model makes on the seeded twins.
- **Raw model output was identical to the B0 run in 77 of 83 cases.** The 6 that differ are all click cases, from the earlier interrupted run and a since-rebuilt `eval-cf/click` index. So almost all of the delta is anchoring.

### Does retrieval earn its cost now? Not yet (D80)

| metric | A0 + A1 | B0 + A1 | B0 − A0, paired 95% CI |
|---|---|---|---|
| F1 | 0.821 | 0.767 | **−0.054 [−0.110, −0.012]** |
| precision | 0.706 | 0.648 | −0.058 [−0.112, −0.014] |
| recall | 0.980 | 0.939 | −0.041 [−0.111, 0.000] |

With anchoring on both sides, retrieved context still costs F1, now mostly through precision. Both configs flag every counterfactual (1.0 false alarms per case), so B0 doesn't yet use the safe callers it retrieves. That is Phase 4 step 2 (Q12 diff rendering, Q13 context use).

---

## Baselines (test split)

| metric | A0 (diff only) | B0 (vector retrieval) | B0 − A0, paired 95% CI |
|---|---|---|---|
| **F1** | **0.741** [0.64, 0.82] | 0.654 [0.54, 0.75] | **−0.086 [−0.163, −0.026]** |
| precision | 0.678 [0.56, 0.79] | 0.603 [0.48, 0.73] | −0.074 [−0.146, −0.014] |
| recall | 0.816 [0.71, 0.92] | 0.714 [0.58, 0.83] | −0.102 [−0.204, 0.000] |
| critical recall | 0.966 [0.88, 1.00] | 0.828 [0.68, 0.96] | −0.138 [−0.300, +0.001] |
| false alarms per counterfactual | 1.000 [1.00, 1.00] | 0.944 [0.81, 1.00] | −0.056 [−0.188, 0.000] |
| noise per clean PR (target ≤ 0.3) | 0.312 [0.10, 0.56] | 0.438 [0.20, 0.70] | +0.125 [−0.111, +0.385] |
| oracle recall@10 | 0 (no retrieval) | 0.559 [0.44, 0.69] | |
| oracle packed recall | 0 | 0.559 [0.44, 0.69] | |
| MRR | 0 | 0.385 [0.29, 0.49] | |
| latency p50 / p95 | 6.1 s / 9.4 s | 14.3 s / 30.2 s* | |
| tokens in / out (mean) | 945 / 161 | 2,715 / 182 | |

\* The GPU was power-capped (25–53 W) during the B0 run. Latency is not comparable across runs taken at different times (see Caveats).

**B0 is significantly worse than A0.** The paired CI on F1 lies entirely below 0. B0 is the baseline Phase 4 builds on; the point of this table is that retrieved context, as B0 packs and uses it, costs quality today.

### Per operator

The cell for a seeded bug shows "bugs found / findings posted". Counterfactual and clean cells show "findings posted on N cases", where every finding is a false alarm.

| operator | kind | A0 | B0 |
|---|---|---|---|
| raise_to_none | seeded | 8/8 (8) | 7/8 (7) |
| drop_param | seeded | 7/8 (8) | 6/8 (11) |
| reorder_params | seeded | 8/8 (8) | 8/8 (8) |
| drop_await | seeded | 8/8 (8) | 6/8 (7) |
| remove_guard | seeded | 0/8 (0) | 0/8 (0) |
| sql_format | seeded | 5/5 (5) | 5/5 (5) |
| exception_type | seeded | 4/4 (4) | 3/4 (3) |
| drop_param_cf | counterfactual | 8 on 8 | 7 on 8 |
| reorder_params_cf | counterfactual | 8 on 8 | 8 on 8 |
| exception_type_cf | counterfactual | 2 on 2 | 2 on 2 |
| rename_local | clean | 4 on 8 | 5 on 8 |
| extract_return | clean | 1 on 8 | 2 on 8 |

## What the numbers say

1. **Counterfactuals do their job (Q10).**
   - A diff-only reviewer flags all 18 safe twins of seeded bugs, so A0's precision is 0.68, not the 0.95 of the earlier dataset.
   - The room for context to prove itself is now in the metric that gates upgrades.
2. **But B0 does not use the context it retrieves.**
   - The prompt held the callers that show the change is safe in 56% of counterfactual cases (oracle packed recall).
   - B0 still flagged 17 of 18.
   - The 8B model treats a removed or reordered parameter as a bug on sight, whatever the callers say.
3. **B0 loses bugs mainly by mis-anchoring them, not by missing them.** In the 6 seeded cases A0 found and B0 didn't:
   - **4: right bug, wrong line.** The finding sat 1–3 lines off the changed line (95 vs 96, 267 vs 270, 570 vs 571, 124 vs 127), on a line the diff didn't add. Validation dropped it (`not_on_changed_lines`).
   - **1: right bug, wrong category.** A missing `await` was called `error-handling`.
   - **1: the wrong focus altogether.**
   - **Mechanism:** a 2.9× longer prompt makes the model's line numbers drift.
   - **Bound:** if validation kept findings that would have matched, recall would be A0 **0.959** vs B0 **0.939**, close to equal.
4. **`remove_guard` stays invisible to both (D70).** The model flags the deleted guard in 7 of 8 cases, but a pure deletion has no added line to anchor to. Together with point 3, this makes **anchoring the top candidate upgrade** (Q11).
5. **Diff reading is a separate weakness.** Several counterfactual titles say *"Duplicate parameter `type_arg`"* where a parameter only moved. The model read the `-` and `+` lines as both present. How the diff is rendered in the prompt is a candidate fix (Q12).
6. **Noise is around target for A0 (0.31) and above it for B0 (0.44).** Most of it is confident "critical bug" findings on correct renames.

## Q9: max 4 vs 8 findings per chunk

**Decided: keep 8 (D75).** On dev, a cap of 4 gave F1 −0.036 and noise +0.375 (paired). That run predates the leak fix, but the decision rests on a *difference* between two configs. Both were equally exposed, since the hint named the bug rather than the cap, so it stands.

## Superseded: Phase 3 step 3 (PR #12), contaminated

The first A0/B0 runs (A0 F1 0.879, B0 0.864) used commits titled `eval case <id>`. The id names the operator, and the prompt includes commit subjects, so the model was told which bug to look for (D77). They also predate the counterfactual cases.

Comparing A0 before and after the fix, recall is unchanged at 0.816. So these seeded bugs are visible from the diff alone, which is why counterfactuals were needed.

## Caveats

- **Latency depends on the machine's state.** The laptop GPU drops into a power-capped state (25–53 W) for long stretches, and per-case time then doubles to quadruples. Latency is only comparable within back-to-back runs. Quality numbers are unaffected (fixed seed, low temperature).
- **Some groups are small.** The bootstrap CIs are wide. `sql_format` (5), `exception_type` (4) and `exception_type_cf` (2) can't be read on their own.
- **Results are for one 8B local model.** A stronger model might use the callers it is shown. Q2 (`qwen3:14b`) is unmeasured.
