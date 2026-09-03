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

## Status

Under active construction. See `docs/adr/` for decisions already taken and
`DECISIONS-NEEDED.md` for the judgment calls, their defaults, and what would change them.

Nothing here is preserved merely because a spec said it. **Once LeanBench provides evidence,
measurement wins.**
