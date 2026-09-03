# ADR-005 — No comparison ships without a noise floor

**Status:** Accepted (2026-09-02)

## Context

With an LLM in the loop, run-to-run variance can exceed the effect being measured. A candidate
change scores +2.1 and the developer believes it; run again and it is −1.4. The optimization
loop becomes a random walk with extra steps, and — worse — a *confident* one, because every
step came with a number.

This is the benchmark's Type I failure mode, and it is not hypothetical: it is the default
outcome of building the obvious thing.

## Decision

The noise floor is measured **before** any signal is reported, and the tooling enforces it.

`leanbench noise --candidate X --suite S --repetitions 10` runs the identical candidate, repo,
commit, suite, seed, and config N times and reports, per dimension and per task: mean, median,
standard deviation, IQR, coefficient of variation, and the **minimum detectable effect** at
n = 1, 3, 5, 10 at 80% power, α = 0.05. Tasks are classified `stable` (CV < 5%), `noisy`
(5–20%), or `unusable` (> 20%).

Profiles persist as `noise-profile.json` keyed by `(suite_version, harness, model,
model_settings)` and are invalidated when any key changes.

### Rules enforced in code, not in documentation

- A delta inside the noise band prints as **`NO CONCLUSION`**. Never as an improvement, never as
  a regression, never with a green arrow.
- Stochastic dimensions get a paired test across matched tasks (Wilcoxon signed-rank), with
  effect size as **Cliff's delta**, not p alone.
- Multiple comparisons across dimensions get Benjamini–Hochberg correction, and the report says
  so out loud.
- With no noise profile for the current key, `compare` **refuses to run** on stochastic
  dimensions and tells the user to run `leanbench noise` first. Deterministic dimensions still
  report.
- `--repetitions` below the MDE requirement for the requested effect size emits a warning naming
  the repetitions actually needed.
- Retrieval-track noise must be **exactly zero**, asserted in a test.

## Consequences

- Many real but small improvements will be reported as `NO CONCLUSION`. This is the intended
  behaviour and the most likely source of friction with an impatient optimizer, including an
  automated one.
- The noise profile costs 10× a single run, per key. Paid once per key, not per experiment.
- The honest failure mode is now "we could not tell", which is a true statement, rather than a
  confident number that reverses next week.
