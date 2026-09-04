# Results

Every number here was produced by the code in this repository and can be recomputed from
the run artifacts. Where a result is unflattering, it is reported anyway — that is the
only reason to trust the ones that are not.

**Corpus:** `encode/httpx` pinned at `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`
(125 indexed files, 3.3 MB). **Suite:** 50 authored tasks, ≥3 paraphrased probes each.
**Tokenizer:** approximate (`~`), identical for every candidate in a run.

---

## 1. The headline: repository tokens to correct solution

Agent track, `candidate-guided` policy, 50 tasks. Correctness is printed beside the token
figure because a candidate that fails fast otherwise posts a beautiful number.

| Candidate | Correctness | Tokens / correct | Mean tokens | Tool calls | Effective efficiency |
|---|---:|---:|---:|---:|---:|
| RawRepository | 0.009 | 5,052 | 6,612 | 4.0 | 0.008 |
| Ripgrep | 0.092 | 1,488 | 1,426 | 3.9 | 0.087 |
| CTags | 0.000 | — | 423 | 3.5 | 0.000 |
| MinimalAST | 0.005 | 564 | 631 | 3.6 | 0.005 |
| **LeanVFS** | **0.369** | **435** | 430 | 4.0 | **0.363** |

LeanVFS reaches **4× the correctness of the next best candidate on a third of the
tokens**, and **11.6× fewer tokens than raw file reading**.

**Read this correctly.** Absolute correctness is low for everyone because the agent track
here is driven by *deterministic policies*, not a language model. The policies are two
contrasting strategies for spending a token budget — read everything, or ask the index
first and read only what it points at. The numbers are comparable **to each other** and
are **not** a claim about what an LLM would score. Running the track against a real model
is implemented but unrun; see §6.

## 2. Retrieval track (deterministic, free, zero variance)

| Candidate | nDCG@10 | Worst-case | R@5 | R@10 | MRR | Tokens | Tokens/task |
|---|---:|---:|---:|---:|---:|---:|---:|
| RawRepository | 0.274 | 0.110 | 0.197 | 0.266 | 0.490 | 681,120 | 13,622 |
| Ripgrep | 0.322 | **0.162** | 0.284 | 0.370 | 0.611 | 193,269 | 3,865 |
| CTags | 0.224 | 0.086 | 0.180 | 0.234 | 0.492 | 71,673 | 1,433 |
| MinimalAST | 0.297 | 0.123 | 0.263 | 0.322 | 0.610 | 90,482 | 1,809 |
| **LeanVFS** | **0.335** | 0.112 | **0.295** | **0.375** | 0.606 | **50,661** | **1,013** |

**Worst-case is the honest column.** It is the minimum across the three paraphrases of
each task, and ripgrep wins it. LeanVFS is the strongest on average and among the more
brittle under rephrasing — a real weakness, not a rounding error.

## 3. Validity gates

**Noise floor.** 10 repetitions of an identical configuration:
`stdev = 0.000000`, `CV = 0.0000%`, all tasks `stable`, minimum detectable effect `0.0` at
every sample size. The retrieval track is bit-for-bit deterministic, as required.

**Discriminative power.** Pairwise Cliff's delta on repository tokens, the dimension the
gate is defined on:

|  | CTags | LeanVFS | MinAST | Raw | ripgrep |
|---|---:|---:|---:|---:|---:|
| CTags | — | −0.990 | 0.524 | 1.000 | 1.000 |
| LeanVFS | 0.990 | — | 0.914 | 1.000 | 1.000 |
| MinimalAST | −0.524 | −0.914 | — | 1.000 | 0.999 |
| RawRepository | −1.000 | −1.000 | −1.000 | — | −1.000 |
| Ripgrep | −1.000 | −1.000 | −0.999 | 1.000 | — |

`|δ(Raw, MinimalAST)| = 1.000` (large) against a 0.474 threshold — **gate PASSED**, and the
token ladder is perfectly monotonic: Raw < ripgrep < MinAST < CTags < LeanVFS.

**The same gate on retrieval quality FAILS.** `|δ(Raw, MinimalAST)| = 0.064` — negligible.
A minimal AST index is *not distinguishable from raw file reading* on nDCG@10 in this
suite. Either the tasks under-reward structure or the AST baseline is too thin. This is a
genuine Type II finding and is not hidden behind the dimension that passes.

**Suite health.** 100% of the 50 tasks are informative (gate: 60%); no ceiling, floor,
unstable or stale-gold flags.

## 4. LeanVFS index characteristics (httpx)

| | Value | Target | |
|---|---:|---:|---|
| Cold index | 1.02 s | — | 3,235 KB/s (floor 400) ✅ |
| Files / symbols | 125 / 1,991 | — | |
| Facts / relationships | 10,824 / 5,934 | — | |
| Index size | 3.96× source | ≤1.5× | ❌ |
| Index size before WAL checkpoint | 4.18× source | — | fixed |
| Resolution R0+R1 (CALLS) | 20.5% | ≥60% | ❌ |
| Single-file incremental edit | 69.7 ms | 60 p50 / 150 p95 | ✅ |
| Incremental equivalence | 0 divergences | 0 | ✅ |

**Incremental.** A one-line edit to `httpx/_urls.py` reparses **1 file and skips 124**,
end to end in 69.7 ms against a full sync of 1,105 ms. `leanvfs verify` then rebuilds into
a scratch database at the pinned `idf_generation` and reports **zero divergences** in
symbols, facts and relationships.

The check is only worth quoting because it can fail: the test suite includes a
deliberately broken implementation (a rename applied to disk but never synced) and asserts
that `verify` catches the stale symbol. It found two real bugs during development — symbol
ids being reallocated on re-parse, dangling inbound edges across the repository, and file
deletion leaving referrers unresolved.

## 5. Findings that changed the design

- **The grader was wrong.** nDCG@10 could exceed 1.0 (measured 1.12) because gain was
  awarded per relevant *result* while the ideal ranking was built from distinct gold
  *items*. It inflated exactly the symbol-heavy candidates. Fixing it reordered the
  ladder.
- **A config knob was silently dead.** The adapter policy was built from one config
  section, so every `calls.*` key fell back to an inline default and builtin suppression
  had never run. Every lookup carried a default, so nothing failed — it just quietly
  behaved differently. This is the failure mode "config over constants" is meant to
  prevent.
- **A negative result, kept.** Query-side stoplisting plus IDF query weighting — the
  textbook fix for the 18/50 intent probes scoring zero — measured *worse*
  (0.335 → 0.317; stoplist alone 0.322, IDF weighting alone 0.321 — every variant lost)
  and was reverted. It survives as default-off config so the experiment
  can be re-run elsewhere.
- **A prior overturned by measurement.** The design assumed trivial builtin calls were
  noise. With suppression finally working, retrieval got worse; the terms are worth +1.3%
  nDCG@10 as lexical surface at no cost in agent-visible tokens. Default flipped.
- **An un-checkpointed write-ahead log more than doubled the apparent index.** SQLite in WAL
  mode retains every page written during a session until the log is folded back. Checkpointing
  after commit took the index from **4.18× to 1.79× of source bytes** on httpx — the largest
  single size reduction in the project, from a change with no algorithmic content at all. (The
  3.96× in the table above is the current figure, after later extraction changes added facts
  back.) Worth recording because it was invisible: nothing was slow, nothing errored, the
  number was simply wrong by a factor of two.
- **Result diversification is real but small.** Symbol indexes fill the top-10 with
  same-file hits (measured: ripgrep 10.0 distinct files, LeanVFS 5.1, MinAST 3.9).
  Capping hits per file is worth +1% nDCG@10 at cap 3.

## 6. What is not measured here

- **No LLM has been run.** The agent-track harnesses and grader are implemented and
  deterministic; the model-backed harness and the replay cache are not built. Every
  agent-track number above is a deterministic-policy number.
- **One repository, one language.** Nothing here supports a claim about C, Java,
  monorepos, or codebases orders of magnitude larger.
- **Self-graded.** The tasks, the baselines, the harness and the candidate were all
  produced by the same agent. `docs/limitations.md` states what that costs and what the
  structural defences are.
