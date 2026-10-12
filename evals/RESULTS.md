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
| Phase 4 step 2 | split diff (Q12) | dev: −0.006 [−0.072, +0.052], noise +0.50 | rejected on dev |
| Phase 4 step 2 | caller-aware context (Q13) | dev +0.029 [0.000, +0.072]; **test +0.006 [−0.056, +0.069]** | rejected |
| Phase 4 step 2 | split + callers | dev: 0.000 [−0.052, +0.054], noise +0.25 | rejected on dev |
| Phase 4 step 3 | U6 thinking (output reserve 3000) | dev +0.092 [+0.030, +0.165]; **test +0.050 [−0.035, +0.138]**, 13% of reviews truncated | not adopted |
| Phase 4 step 3b | thinking at 16k, Qwen sampling (0.6) | dev −0.021 [−0.115, +0.069], 0 failures | rejected on dev |
| Phase 4 step 3b | thinking at 16k, temp 0.1 + fallback | dev +0.008 [−0.050, +0.061], 0 failures, 7 fallbacks | rejected on dev |
| Phase 4 step 4 | verification pass (Q16b) | dev +0.038 [−0.041, +0.101]; **test +0.026 [−0.048, +0.104]**, recall −0.082 | rejected; **Phase 4 closed** |

---

## Phase 4 step 4: a verification pass (Q16b), rejected; Phase 4 closed

**What changed** (`verify.py`, `default_verify.yaml`; off by default): after validation, each posted finding labelled `bug`, `api-contract` or `error-handling` gets **one narrow second call**.
- **What the call sees:** the finding, its file's diff, and the changed function's callers. The callers come **from the graph's `calls` edges** (not similarity search), tests excluded, at most 8 callers of up to 40 lines each.
- **The question:** do these callers show the problem cannot happen?
- **The rule:** only a clear "yes" drops the finding (`verified_safe`). With no known caller, or if the call fails, the finding stands.
- **Why those categories:** the reviewer labels its counterfactual false alarms `bug` (17 of 18), not `api-contract`.

**A schema lesson.** In the first dev run, the verifier never said "safe" (31 of 31 confirmed). Its reasons showed why: with the verdict field *first*, the model commits to `true` before reasoning, then writes, for example, *"the calling code is already handling the new `RuntimeError`"*. That's right, but it's after its own wrong verdict. Ollama's grammar generates fields in schema order, so `reason` now comes first and the verdict last (D87).

### Results (paired against the default)

| metric | dev, verdict first | dev, reason first | **test, reason first** |
|---|---|---|---|
| F1 Δ | +0.014 | +0.038 [−0.041, +0.101] | **+0.026 [−0.048, +0.104]** |
| precision Δ | +0.020 | +0.081 [+0.004, +0.170] | +0.089 [+0.006, +0.176] |
| recall Δ | 0 | −0.042 | **−0.082** (6 real bugs cleared) |
| false alarms per counterfactual | 1.00 | 0.67 | 0.72 |
| latency p50 / p95 | 21 / 25 s | 19 / 25 s | 19 / 30 s |

**Rejected (D87).** The verifier does clear safe counterfactuals using the real callers, the first non-thinking config to do so. But it clears real bugs at the same rate: of the reordered parameters on test, it dropped **4 of 8 real bugs and 3 of 8 safe twins**. qwen3:8b can't tell a safe call from a breaking one even with only the callers in front of it.

### Phase 4 in one paragraph

Retrieval, prompting, rendering, thinking, a 16k window and a narrow verification call were each measured against the same 83 test cases.
- **One upgrade was adopted:** anchoring (F1 0.654 → 0.767), which fixes how findings are placed.
- **Everything aimed at making qwen3:8b reason over context failed the gate.** These changes moved precision or noise without a significant F1 gain: caller tags, the split diff, thinking (at 8k and at 16k with fallback), and verification. The 8B model reacts to the shape of a diff, and the evidence that would clear it doesn't change its verdict reliably.
- **What's left,** for later: a stronger model (Q2), and the retrieval upgrades U1–U5. Those should be re-measured with whichever model can use their context.

---

## Phase 4 step 3b: thinking with room to think (Q15), rejected

**Setup:** Ollama now runs with `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`, so a 16k window fits in 8 GB. Measured with the embedder loaded: the LLM is 5.8 GiB, 7.26 of 8.19 GB is used in total, everything is 100% on the GPU, and it decodes at 29 tok/s. Runs use `LLM_NUM_CTX=16384`.

### Why thinking reviews fail: repetition loops

The 8k runs blamed the failures on the 3,000-token budget. At 16k with a 6,000-token budget, about 11% of reviews still failed (4 of 36 on dev). The new error message shows where the tokens went: *thinking 24,058 characters, JSON 0*.

A failing case reproduced deterministically with a direct call. The chain of thought re-reads the diff in a loop: *"Wait … the line with 'encoding' was duplicated … Wait, no, looking at the diff…"*. It repeats the same signature line 31 times and never answers. The trigger is a moved parameter, the same `-`/`+` confusion as Q12.

- **The split diff doesn't stop it.** The loop just changes wording; one phrase appeared 84 times.
- **Qwen's recommended thinking sampling does.** With temperature 0.6, top-p 0.95 and top-k 20, the same case finishes in 2,899 tokens.
- **There's no middle ground in length.** Across 128 completed reviews at temperature 0.1, the longest used 2,617 output tokens. A review either finishes or loops.

### Results (dev, 41 cases, paired against the default at 8k)

| config | F1 | Δ (95% CI) | recall | noise | false alarms per counterfactual | failed | fallbacks |
|---|---|---|---|---|---|---|---|
| default at 16k (window only) | 0.828 | +0.014 [0.000, +0.045] | 1.000 | 0.75 | 1.00 | 0 | |
| thinking at 16k, Qwen sampling, 6,000 budget | 0.792 | −0.021 [−0.115, +0.069] | **0.875** | 0.13 | 0.78 | **0** | |
| thinking at 16k, temp 0.1, 3,500 budget + fallback | 0.821 | +0.008 [−0.050, +0.061] | 0.958 | 0.38 | 0.89 | **0** | 7 |

- **Qwen's sampling fixes the loops but costs quality:** recall falls to 0.875 (3 bugs missed).
- **The fallback** (`generation.think_fallback`) handles each loop by reviewing the chunk again without thinking. No reviews failed, and all 7 fallbacks were real loops (14–16k characters of thinking, no JSON). But at 16k the thinking itself no longer adds anything over the default.
- **The 16k window alone is neutral** (+0.014).

### Thinking across all runs

| run | F1 Δ vs the default |
|---|---|
| 8k, dev (completed reviews only) | +0.078 [+0.017, +0.150] |
| 8k, test (completed reviews only) | +0.058 [−0.011, +0.136] |
| 16k + fallback, dev | +0.008 [−0.050, +0.061] |

**Not adopted (D86).** Thinking's effect at qwen3:8b is inconsistent: positive at 8k on dev, not significant on test, absent at 16k. It also costs about 4× the latency (p50 ~55 s, p95 ~170 s) and loops on 10–17% of reviews.

Two things are kept, both off by default: the loop diagnosis, and the `think_fallback` mechanism for any later thinking config. The configs are `default_think16k.yaml` (fallback) and `default_think16k_qs.yaml`.

---

## Phase 4 step 3: thinking mode (U6), not adopted as configured

**What changed:** `generation.think: true` (Qwen3 thinking) in `default_think.yaml`. The output reserve went from 1,500 to 3,000 tokens.
- **Why the reserve grew:** thinking tokens count against `num_predict` and the 8k window.
- **How 3,000 was chosen:** a probe on 8 dev cases measured 551–2,157 output tokens.
- **The trade-off:** the bigger reserve shrinks the prompt budget.
- **Runtime setting:** runs used `LLM_TIMEOUT_S=900`, a machine setting rather than a config one. The power-capped GPU pushed some calls past the default 300 s.

**Latency budget (D84):** p95 ≤ 10 minutes per review.

### Dev (41 cases, paired against the default)

- **F1:** 0.814 → 0.906, **+0.092 [+0.030, +0.165]**.
- **Precision:** +0.142.
- **Noise:** 0.75 → 0.00.
- **Counterfactual false alarms:** 1.00 → 0.56.
- **Recall:** unchanged.
- **Failures:** 1 review truncated, plus 4 Ollama timeouts that were retried with `--retry-failed`.

### Test (83 cases, paired against the default)

**11 reviews failed (13%), all truncated:** thinking ran past the 3,000-token reserve before the JSON ended. That was 6 seeded and 5 counterfactual cases. When thinking does finish, it needs a median of 742 output tokens (p90 1,830, max 2,617).

A failed review posts nothing. On a seeded case that's a miss; on a counterfactual it *looks* like a correct "no alarm". So both views are reported. The second is `python -m evals.report … --completed-only`, which keeps only the cases both runs reviewed.

| metric | default | thinking (all 83) | Δ (95% CI) | thinking (72 both completed) | Δ (95% CI) |
|---|---|---|---|---|---|
| **F1** | 0.767 | 0.816 | +0.050 [−0.035, +0.138] | 0.870 | +0.058 [−0.011, +0.136] |
| precision | 0.648 | 0.816 | +0.168 [+0.078, +0.267] | 0.816 | **+0.109 [+0.025, +0.202]** |
| recall | 0.939 | 0.816 | **−0.122 [−0.229, −0.018]** | 0.930 | −0.023 [−0.098, +0.056] |
| noise per clean PR | 0.438 | 0.062 | −0.375 | 0.062 | **−0.375 [−0.636, −0.143]** |
| false alarms per counterfactual | 1.000 | 0.389 | −0.611 (flattered) | 0.538 | **−0.462 [−0.750, −0.200]** |
| latency p50 / p95 | 13 / 24 s | 28 / 93 s | | 26 / 66 s | |

**Gate:** F1's CI includes 0 in both views, so thinking is **not adopted as configured**. Latency is well inside the budget.

### What this says

1. **Thinking is the first change that makes the model use context.** On completed reviews:
   - it clears about half the counterfactuals (false alarms 1.00 → 0.54), the first movement on them in Phase 4;
   - it nearly eliminates noise on clean PRs;
   - it keeps recall.

   The significant precision and noise gains are real. Prompting (step 2) didn't get there; reasoning does.
2. **Its loss is mechanical.** 13% of reviews run out of output budget. That costs recall (the 6 seeded failures) and makes the F1 gain non-significant.
3. **The fix is a bigger output reserve, which the 8k window can't afford.** Reserving 5,000+ tokens leaves too little prompt for the diff and context. At 16k, the reserve fits without shrinking the prompt. The catch: Ollama must run with flash attention and a q8_0 KV cache (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`). D66 measured that as free in VRAM and speed on this GPU. It's a server setting on the user's machine (Q15).

---

## Phase 4 step 2: diff rendering (Q12) and context use (Q13), both rejected

**Why these two:** the B0 prompts showed two problems.
- **Diff misreading.** The model read a moved parameter's `-` and `+` lines as "duplicate parameter". It did the same with `- force_readable: bool = False` in a signature next to `+ force_readable = False` in the body.
- **Unused context.** It never used the callers in its prompt. It also couldn't tell which context blocks *were* callers: in one counterfactual, 5 of 8 blocks were test functions.

**What was tried** (all toggles default off; configs `default_split`, `default_callers`, `default_split_callers`):
- **`packing.diff_style: split`:** each hunk is shown twice. Before is unnumbered, so it can't be cited, with removed lines marked. After is numbered, with `+` on added lines.
- **`retrieval.tag_relations`:** one query on the graph's `calls` edges marks each context block `relation="calls the changed code"` or `relation="called by the changed code"`.
- **`generation.check_callers`:** adds rule 6 to the system prompt. Before reporting an API break, read the tagged callers; if every caller shown works with the new code, don't report it.

### Dev (41 cases, paired against the current default, `b0-anchor-dev`)

| metric | default | + split | + callers | + both |
|---|---|---|---|---|
| F1 | 0.814 | 0.807 (−0.006) | 0.842 (+0.029 [0.000, +0.072]) | 0.814 (0.000) |
| precision | 0.686 | 0.697 | 0.727 | 0.686 |
| recall | 1.000 | 0.958 | 1.000 | 1.000 |
| noise per clean PR | 0.75 | **1.25** | 0.625 | 1.00 |
| false alarms per counterfactual | 1.00 | 1.11 | 1.00 | 1.00 |

### Test (83 cases): caller-aware context, the only dev candidate

| metric | default (B0 + A1) | + callers | Δ, paired 95% CI |
|---|---|---|---|
| F1 | 0.767 | 0.773 | +0.006 [−0.056, +0.069] |
| precision | 0.648 | 0.657 | +0.009 |
| recall | 0.939 | 0.939 | 0.000 |
| noise per clean PR | 0.438 | 0.438 | 0.000 |
| false alarms per counterfactual | 1.000 | 1.056 | +0.056 |

**Rejected.** The test result doesn't pass the gate; the dev gain was noise.

### What this says

1. **The split diff backfires.** Showing old and new side by side invites comments on the difference: rename noise rose from 4 to 7 findings on 4 clean cases. The "duplicate parameter" titles were a symptom, not the cause of the false alarms.
2. **The 8B model does not use callers, even when told to.** With the callers tagged and an explicit rule, it still flagged every counterfactual (9 of 9 on dev, 19 posts on 18 cases on test). Typical titles: *"Parameter order changed in `_analyze` function"*, *"Removed parameter breaks API contract"*. It reacts to the diff's shape and ignores the evidence that the change is safe. Prompting alone can't fix this at qwen3:8b.
3. **Retrieval-side changes can't help until the model can reason with what it gets.** That points at the model, not the context:
   - thinking mode (U6), which is already a config toggle;
   - a stronger model (Q2);
   - a targeted verification pass for API-contract findings (Q14).
   Planned retrieval upgrades such as graph expansion (U2) would put the callers in the prompt more often; on today's evidence, that wouldn't change the verdicts.

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
