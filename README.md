# LeanBench + LeanVFS

Two systems built together, in one repository, for one reason:

> **Make AI coding agents consume fewer tokens when they search a repository — and prove it.**

**LeanVFS** is the thing that tries to make agents cheaper: a local, offline, deterministic
semantic projection of a repository, so an agent can find what it needs without reading whole
files.

**LeanBench** is the thing that decides whether LeanVFS actually works: an independent benchmark
that measures whether a repository-intelligence system helps an agent understand and modify code
*correctly* while consuming *less* repository context.

Neither is useful alone. An indexer with no measurement is a guess; a benchmark with nothing to
measure is a spreadsheet. They are built as a loop — the benchmark tells the indexer where it is
losing tokens, the indexer's failures tell the benchmark which tasks were badly authored.

---

## The one guard rail that makes the loop honest

A benchmark co-designed with one candidate will quietly award points for that candidate's
assumptions. So LeanBench treats LeanVFS as **just another candidate**, indistinguishable from a
third party:

- no import crosses the boundary (enforced by test)
- the only channel is the JSONL wire protocol in [`PROTOCOL.md`](PROTOCOL.md)
- the four reference baselines speak the **same** protocol over the **same** subprocess path —
  no privileged internal route
- no task may be authored by looking at what LeanVFS happens to answer well

See [ADR-001](docs/adr/ADR-001-independence-from-leanvfs.md). If LeanVFS loses to `ctags`, the
benchmark reports that.

---

## Layout

```
PROTOCOL.md          # normative: the candidate wire protocol. Both sides build to this.
TASKS.md             # normative: task/gold schema and grading semantics.
DECISIONS-NEEDED.md  # judgment calls, defaults taken, reasoning preserved.

leanbench/           # the benchmark: kernel, candidate runner, tracks, graders, metrics, CLI
leanvfs/             # the candidate: indexer, resolver, budget engine, protocol server
baselines/           # the calibration standards: Raw, ripgrep, CTags, MinimalAST
suites/              # tasks + structured gold
corpus/              # working copies, pinned by exact commit SHA (gitignored)
docs/                # scoring, equivalence, limitations, ADRs
```

`PROTOCOL.md` and `TASKS.md` are the integration keystones. They exist as written contracts so
the four parts can be built in parallel against one source of truth rather than discovering
mismatches at integration time.

---

## Two tracks, because they answer different questions

| | Retrieval Track | Agent Track |
|---|---|---|
| LLM | none | full agent loop |
| Cost | free | real API spend |
| Speed | milliseconds | minutes |
| Determinism | **exactly zero variance** (asserted) | stochastic, variance reported |
| Answers | *is the index good?* | *does a good index help?* |

The retrieval track is the inner optimization loop — a candidate developer gets a full answer in
under 10 seconds, free. The agent track validates that retrieval gains actually convert into an
agent reaching the right answer with less context.

**Their correlation is itself reported.** If retrieval gains stop predicting agent gains, we are
overfitting to a proxy and the report says so. See
[ADR-004](docs/adr/ADR-004-two-evaluation-tracks.md).

---

## The two failure modes this is designed against

**Type I — noise reported as signal.** A change scores +2.1, you believe it, you re-run and get
−1.4. The optimization loop becomes a confident random walk. Defence: the noise floor is measured
*before* any signal, and `compare` **refuses** to call a sub-noise delta an improvement — it
prints `NO CONCLUSION`, with no green arrow. ([ADR-005](docs/adr/ADR-005-noise-floor-before-comparison.md))

**Type II — no discrimination.** Raw file reading, ripgrep, ctags and an AST index all land
within a few points, so the benchmark is measuring the agent's stubbornness rather than the
candidate. Defence: pairwise effect size between baselines is a **reported, gated property** —
the suite is not usable until `|δ(Raw, MinAST)| ≥ 0.47` on repository-tokens-to-correct-solution.

Both are build gates, not hopes. You do not pass them by adding features, only by measuring.

---

## The signature metric

**Repository Tokens to Correct Solution**, counted only over tasks answered correctly — and
always printed beside the correctness rate, because otherwise a candidate that fails fast posts a
beautiful number. The published headline is correctness-weighted, so a candidate that returns
nothing scores zero rather than infinity.

Full definition, including the anti-gaming rules (envelopes are counted; pointer-shaped answers
pay for the dereference; one tokenizer per run):
[ADR-009](docs/adr/ADR-009-repository-tokens-signature-metric.md) and [`docs/scoring.md`](docs/scoring.md).

---

## Honesty commitments

Every number in a report is recomputable from `events.jsonl` + the suite's scoring config. If it
cannot be, it does not ship. Approximate token counts are labelled `~`. Infrastructure failures
are never scored as candidate failures. Update latency is never reported without the
[equivalence rate](docs/equivalence.md) beside it. Cross-model and cross-tokenizer comparisons
are marked or rejected, not quietly averaged.

The ways this benchmark can be wrong — contamination, probe overfitting, single-repository
validity, self-graded baselines, and the fact that one agent authored both the tasks and the
system under test — are written down in [`docs/limitations.md`](docs/limitations.md) rather than
left for a reader to discover.

---

## What it measured

Full numbers, including the unflattering ones, are in [`RESULTS.md`](RESULTS.md).
The short version, on 50 authored tasks over `httpx` at a pinned commit:

| Candidate | Correctness | Tokens / correct solution |
|---|---:|---:|
| Raw file reading | 0.009 | 5,052 |
| ripgrep | 0.092 | 1,488 |
| CTags | 0.000 | — |
| Minimal AST | 0.005 | 564 |
| **LeanVFS** | **0.369** | **435** |

LeanVFS reaches 4× the correctness of the next best candidate on a third of the tokens,
and 11.6× fewer tokens than raw file reading.

Validity, checked rather than asserted: retrieval-track variance is **exactly zero** over
10 repetitions; the discrimination gate passes at `|δ(Raw, MinAST)| = 1.000` on the
signature metric; 100% of tasks are informative against a 60% gate.

Three things the same measurements say that are *not* flattering, reported because a
benchmark that only publishes its wins is an advertisement:

- On retrieval **quality** (nDCG@10) rather than tokens, a minimal AST index is
  statistically indistinguishable from raw file reading — `|δ| = 0.064`. The suite may
  under-reward structure.
- LeanVFS is among the **most brittle** candidates under paraphrase; ripgrep has the
  better worst case.
- Two LeanVFS targets are missed: call-resolution confidence (20.5% vs 60%) and index
  size (3.96× vs 1.5×).

## Running it

```bash
leanbench doctor                                    # environment check
leanbench evaluate --candidate leanvfs/leanvfs-candidate.toml --suite suites/httpx
leanbench noise --candidate ... --suite ... --repetitions 10   # noise floor first
leanbench separation runs/*/                        # discriminative power
leanbench tasks triage suites/httpx --run runs/...  # suite health
```

## Status

The deterministic half is complete and gated: both tracks run, artifacts are immutable and
fully traceable, 166 tests pass, and every phase gate is an executable assertion rather
than a claim. Not built: a model-backed agent harness with its replay cache, and genuine
incremental sync (`update_repository` currently does a full re-sync, which is correct but
does not exercise the invalidation matrix). Both are scoped in `RESULTS.md` §6.

Nothing here is preserved merely because a spec said it. **Once LeanBench provides evidence,
measurement wins.**
