# Scoring — normative and public

No hidden scoring behaviour. Every published number must be recomputable from a run's raw
artifacts (`events.jsonl` + the suite's `scoring` block) by the formulas on this page. If a
number in a report cannot be derived this way, it does not ship.

The aggregate is **versioned and lives in suite config, not in code**, so that
`leanbench rescore RUN_ID --scoring scoring-v2.toml` can apply a new formula to an old run
without re-running anything.

---

## 1. Report dimensions independently first

The aggregate is a convenience, not the result. These seven are always reported separately,
because collapsing them hides the trade-offs that are the entire point of the measurement:

| Dimension | Meaning |
|---|---|
| Correctness | Did the agent reach the right answer? |
| Context Efficiency | How many repository tokens did that cost? |
| Retrieval Quality | Recall/precision/MRR/nDCG against structured gold |
| Navigation Efficiency | Tool calls, files opened, time to first relevant file |
| Index Performance | Cold index duration and throughput |
| Incremental Performance | Update latency **and equivalence rate, never separated** |
| Resource Footprint | Peak RSS, CPU time, index size |

---

## 2. Normalization

All dimension scores normalize to `[0, 1]`, higher is better.

### 2.1 Correctness
Already in `[0,1]` from the structural grader (`TASKS.md` §4.2). No transformation.

### 2.2 Context efficiency

Repository tokens are lower-is-better and unbounded above, so they are normalized against a
**reference baseline** rather than against an arbitrary constant:

```
context_efficiency(c) = clamp01( log(1 + T_ref / max(T_c, 1)) / log(1 + R_max) )
```

where
- `T_c` = mean `repository_context_tokens` for candidate `c`, **over correctly-solved tasks only**
- `T_ref` = the same quantity for the `RawRepository` baseline on the same task set
- `R_max` = the configured saturation ratio (`scoring.context_saturation_ratio`, default `20.0`),
  meaning "a 20× reduction against raw file reading saturates this dimension"

The log makes the dimension read sensibly across orders of magnitude: 2× reduction ≈ 0.36,
5× ≈ 0.60, 10× ≈ 0.80, 20× ≈ 1.00. Linear normalization would let a single extreme candidate
compress everyone else into indistinguishable noise near zero.

**Restricting to correctly-solved tasks is essential and is also a trap**, which is why §3
exists.

### 2.3 Retrieval quality
`mean(nDCG@10)` across probes, reported alongside `worst_case(nDCG@10)` across paraphrases.
The aggregate uses the **mean of mean and worst-case**, so brittleness under paraphrase costs
score rather than being averaged away.

### 2.4 Navigation efficiency
`clamp01(1 - tool_calls_c / tool_calls_ref)` against the same reference baseline, floored at 0.

### 2.5 Index / incremental performance
Measured against the configured latency budgets:
`clamp01(budget_p95 / max(measured_p95, ε))`, capped at 1. Beating the budget by 10× is not
worth ten times the score — the budget is a floor to clear, not a race.

### 2.6 Incremental — equivalence gates latency
```
incremental_score = latency_score × incremental_equivalence_rate
```
This multiplication is deliberate and non-negotiable. **A candidate with fast updates and poor
equivalence is worse than one with slow correct updates**, and the score must say so. Update
latency is never reported without the equivalence rate beside it.

### 2.7 Resource footprint
`clamp01(budget / max(measured, ε))` over peak RSS and index-size ratio, averaged.

---

## 3. The signature metric, and the failing-fast trap

**Repository Tokens to Correct Solution** — always reported prominently, counting only tasks
the candidate got correct:

```
Raw repository     41,210
ripgrep            29,440
CTags              18,905
Minimal AST        11,332
Candidate           5,812
```

Counting only correct tasks is the right choice — tokens spent on a wrong answer are not
"efficiency" — but it creates an obvious perverse incentive: **a candidate that answers almost
nothing, and answers only the two trivially easy tasks, posts a beautiful token number.**

Three defences, all mandatory:

1. **The correctness rate is always printed directly beside the token figure.** Never one
   without the other.
2. The published headline is correctness-weighted:
   ```
   effective_context_efficiency = correctness × context_efficiency
   ```
   A candidate that returns nothing scores **zero**, not infinity.
3. Token comparisons across candidates are computed over the **intersection** of tasks both
   candidates solved correctly, and the report states the size of that intersection. Comparing
   a 12-task mean against a 47-task mean is not a comparison.

---

## 4. Aggregate

```toml
[scoring]
version = "v1"
correctness           = 0.45
context_efficiency    = 0.25
retrieval_navigation  = 0.10
incremental           = 0.10
cold_index            = 0.05
footprint             = 0.05
context_saturation_ratio = 20.0
```

```
score = 100 × Σ (weight_d × normalized_score_d)
```

Weights sum to 1.0; the loader asserts this. Correctness carries the largest weight because a
token-efficient wrong answer is worthless — the ordering of these weights encodes the product
thesis and should be argued with, not silently edited.

**A dimension with no data is excluded and the remaining weights are renormalized**, with the
report stating which dimensions were excluded. Scoring a missing dimension as zero would
punish a candidate for not being asked.

---

## 5. Traceability requirement

`leanbench report RUN_ID --explain` prints, for every dimension, the raw inputs, the
normalization applied, and the arithmetic — so any number can be checked by hand against
`events.jsonl`. A test reconstructs the full score from raw events alone and asserts equality
with `summary.json`.
