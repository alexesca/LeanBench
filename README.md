# LeanVFS + LeanBench

**LeanVFS** is a local, offline, deterministic semantic index that lets a coding agent
search your repository without reading whole files. On a real codebase it answers a
question in **~130 tokens** where grep-and-read costs **~26,000** — the same answer,
197× cheaper.

**LeanBench** is the benchmark that decides whether that claim is true. It treats LeanVFS
as just another candidate and measures it against raw file reading, ripgrep, CTags and a
minimal AST index. Both live here, because an indexer with no measurement is a guess.

---

## Install

```bash
pip install "git+https://github.com/alexesca/LeanBench.git"
```

That gives you a `leanvfs` command. There is no config file, no daemon, no setup step:

```bash
cd ~/your/repo
leanvfs search "why do uploads fail for large files" --limit 8
```

The first query indexes the repository; every later query brings the index up to date
before answering, so it is never stale. An unchanged file costs one hash (~50 ms across
125 files), so keeping it correct is nearly free.

### Point your coding agent at it

Give your agent [**`INSTALL-PROMPT.md`**](INSTALL-PROMPT.md) — a paste-ready prompt that
installs LeanVFS and rewrites its search habit. The durable part is an `AGENTS.md` entry:

```markdown
## Code search
This repo has a LeanVFS semantic index. Use it INSTEAD of grepping or reading
whole files.

  leanvfs --repo . search "<question in plain English>" --limit 8
  leanvfs --repo . context "<Symbol.name>"

Workflow: search -> pick the 1-3 relevant hits -> read ONLY those line ranges
with Read(offset/limit). Do not read whole files first.
```

### Commands

| | |
|---|---|
| `leanvfs search "<question>"` | ranked file + symbol + line-range hits |
| `leanvfs context "<Symbol>"` | signature, exceptions, effects, tests, callers — **never the body** |
| `leanvfs stats` | index size, symbol counts, resolution quality |
| `leanvfs status` | generation, staleness, IDF drift |
| `leanvfs verify` | assert the incremental index equals a clean rebuild |
| `leanvfs config show` | every tunable, with the layer each value came from |

Python is fully supported (tree-sitter). Other languages fall back to a generic
symbol extractor, plus dedicated Markdown and config-file extractors.

### See it on your own code

```bash
./try-it.sh ~/your/repo "a question you know the answer to"
```

Prints the token cost of grep-and-read versus LeanVFS, side by side. It measures the cost
of *locating* an answer, not whether the answer is right — judge the hits yourself.

---

## Does it actually work?

Full numbers, including the unflattering ones, in [`RESULTS.md`](RESULTS.md). On 50
hand-authored tasks over `encode/httpx` at a pinned commit:

| Candidate | Correctness | Tokens / correct solution |
|---|---:|---:|
| Raw file reading | 0.009 | 5,052 |
| ripgrep | 0.092 | 1,488 |
| CTags | 0.000 | — |
| Minimal AST | 0.005 | 564 |
| **LeanVFS** | **0.369** | **435** |

Validity, checked rather than asserted: retrieval-track variance is **exactly zero** over
10 repetitions; the discrimination gate passes at `|δ(Raw, MinAST)| = 1.000`; 100% of
tasks are informative against a 60% gate.

Three things the same measurements say that are **not** flattering:

- On retrieval *quality* (nDCG@10) rather than tokens, a minimal AST index is
  statistically indistinguishable from raw file reading — `|δ| = 0.064`.
- LeanVFS is among the **most brittle** candidates under paraphrase; ripgrep has the
  better worst case.
- Call resolution reaches 20.5% high-confidence against a 60% target.

Correctness figures come from deterministic policies, not a language model.
**No LLM has been run**, and that is stated wherever a number appears.

**How to check this work yourself:** the strongest test is the one I cannot run — point
`try-it.sh` at a repository I have never seen and judge the hits. See
[the architecture guide](docs/guide/leanvfs-architecture.html) for how the system works
and how it changed under measurement.

---

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
fully traceable, 171 tests pass, and every phase gate is an executable assertion rather
than a claim. Incremental sync is hash-gated and verified: a one-line edit reparses 1 file
of 125 in 69.7 ms, and `leanvfs verify` confirms zero divergence from a clean rebuild.

Not built: a model-backed agent harness with its replay cache. Every agent-track number
here is a deterministic-policy number, which is stated wherever one is quoted.

Nothing here is preserved merely because a spec said it. **Once LeanBench provides evidence,
measurement wins.**
