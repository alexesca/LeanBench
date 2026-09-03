# Limitations

A benchmark that does not publish its own weaknesses is marketing. These are the ways
LeanBench can be wrong, stated plainly, with what we do about each and what we do not.

---

## 1. Training contamination

Public gold data ends up in future model training corpora. That is not a risk we can eliminate
while remaining auditable — see `DECISIONS-NEEDED.md` LB-5 — so we make it **detectable**
instead.

- Every task records `authored_at`.
- A large jump in agent-track performance on **old** tasks, not reproduced on newly authored
  ones, is a **contamination signal, not a candidate win**, and must be reported as such.
- The retrieval track is structurally more resistant: it measures what an *index* returns for a
  fixed probe, so a model having memorized httpx does not help a candidate score.

**What we cannot do:** prove a given model has not seen a given task. Treat any single-model
agent-track number on a public suite as an upper bound on true performance.

## 2. Probe phrasing overfitting

The retrieval track issues fixed probes. A candidate can be tuned — deliberately or by accident
through iterated benchmarking — to the exact phrasing.

Mitigations: ≥3 paraphrases per task in three deliberately different registers (literal /
intent / symptom), with intent and symptom probes written to avoid the gold identifiers; both
**mean and worst-case** across paraphrases reported, so brittleness costs score.

**Residual weakness, stated honestly:** in this build the paraphrases were written by the same
author as the gold. That is a known bias (LB-3). Independent paraphrase authorship is the fix
and has not been done.

## 3. Single-repository, single-language validity

The initial suite is HTTPX — one Python library, ~570 KB, one architectural style. Results
demonstrate *nothing* about C, Java, monorepos, or 10-million-line codebases.

The build order forbids expanding corpus in place of passing a validity gate, and every new
repository must **re-run every gate** — a new corpus does not inherit the previous suite's
validity. Until the corpus broadens, read every number as "on httpx" and not as a general claim.

## 4. Baselines are our own construction

The ladder (Raw / ripgrep / CTags / MinimalAST) is the measuring instrument. If a baseline is
weak, the candidate looks better than it is, and the discrimination gate becomes meaningless.

We build baselines to be *the best honest version of their category*, with capability ceilings
strictly respected (no AST parsing inside the ripgrep baseline; no relationship resolution
inside MinimalAST). Where a baseline reimplements a tool rather than invoking the real binary,
the README says so explicitly.

**This remains a self-graded exam.** An external implementation of any baseline that beats ours
is a bug report we want.

## 5. The agent track is stochastic and expensive

LLM run-to-run variance can exceed the effect being measured. This is the reason for the noise
floor, and it is enforced rather than advised: `compare` **refuses** to call a sub-noise delta
an improvement, and refuses to run at all on stochastic dimensions without a noise profile for
the current `(suite, harness, model)` key.

Consequence to accept: many real but small improvements will be reported as `NO CONCLUSION`.
That is the correct outcome. **An optimization loop that reports noise as signal is worse than
no loop — it actively misleads.**

## 6. Correctness grading is structural, not semantic

The grader checks whether the agent identified the gold elements. It cannot tell a genuinely
insightful answer from one that name-drops the right symbols, and it will mark correct a
well-reasoned answer that used different-but-valid terminology.

An LLM-judge grader is deliberately **not** implemented. Its re-entry criterion is strict: a
category must be provably ungradeable structurally *and* judge/human agreement must be measured
first. Adding an unvalidated judge would replace a known, boring bias with an unknown one.

## 7. Token counting is tokenizer-dependent

Absolute token figures depend on the tokenizer. One tokenizer is pinned per run and identical
across candidates; cross-tokenizer comparisons are **rejected**, not warned about. Approximate
counts are labelled `~` and never presented as exact.

Ratios between candidates within a run are trustworthy. Absolute figures across runs with
different tokenizers are not.

## 8. Python-implemented latency numbers

LeanVFS is implemented in Python (ADR-002). The latency budgets are met or missed *as a Python
implementation*. A published latency figure is therefore a property of this implementation, not
a floor for the design, and the report must label it so rather than implying the architecture
cannot do better.

## 9. Gold is authored by the same agent that built the system

The most uncomfortable one. In this build, tasks, baselines, harness, and candidate were all
produced by the same agent. The structural defences are: task quality gates that cull
uninformative tasks automatically, the separation-matrix gate that fails if baselines are
indistinguishable, an independent review pass filling `reviewed_by`, and the requirement that
every task carry a written justification.

None of that fully substitutes for independent authorship. **Weight external replication
accordingly.**

---

## How to read a LeanBench report given all of the above

- Trust **relative** ordering within a run far more than absolute numbers.
- Trust the **retrieval track** more than the agent track for reproducibility; trust the agent
  track more for whether retrieval gains actually matter.
- If the two tracks disagree about which candidate is better, **believe neither yet** — one of
  them is measuring the wrong thing, and finding out which is more valuable than the ranking.
- Check `suite_health` before reading any score. A `DEGRADED` run, or an informative-task rate
  under the threshold, means the score is not evidence.
