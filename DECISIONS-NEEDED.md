# Decisions Needed

Per the ground rules of both build specs: when blocked on a judgment call, record it here with
a recommendation, **take the stated default, and continue**. Nothing in this file blocks the build.

The repository owner pre-approved all defaults ahead of the initial build, so every decision
below is recorded as *taken*, with the reasoning preserved so it can be revisited when
measurement contradicts it. That last part matters more than the decision: **once LeanBench
provides evidence, measurement wins over anything written here.**

---

## LeanBench

### LB-1 — Model for the agent track
**Taken:** one pinned mid-tier model for all published runs; `--model` override available for
local experimentation, with results marked `CROSS-MODEL — INTERPRET WITH CAUTION`.
**Why:** cost and reproducibility both hang on this. A published number produced by a model
nobody else can pin is not a benchmark result. The replay cache (LB-6) means a pinned model's
runs stay re-analyzable even after the provider retires the snapshot.
**Revisit when:** the pinned model is deprecated, or a cheaper model is shown to rank
candidates identically (Spearman ≥ 0.9 against the pinned model across the baseline ladder) —
at which point the cheaper model becomes the default and the expensive one the audit.

### LB-2 — Noise floor repetitions
**Taken:** 10 repetitions for the initial profile per `(suite, harness, model, model_settings)`
key, refreshed only when a key changes.
**Why:** 10 is defensible and it is also 10× the spend, but it is paid once per key rather than
per experiment. The alternative — guessing the noise band — is exactly the failure this whole
section exists to prevent.
**Revisit when:** the measured coefficient of variation is small enough that the minimum
detectable effect at n=5 is already below the effect sizes we care about.

### LB-3 — Probe paraphrase authorship
**Taken:** 3 per task — one literal, one intent-phrased, one symptom-phrased — flagged for
review, with the explicit authoring rule that intent and symptom probes must avoid reusing the
gold identifiers.
**Why:** the retrieval track's known weakness is overfitting to probe phrasing. Paraphrases are
the mitigation, and the `symptom` paraphrase (written as if by someone who has never read the
source) is the one that actually separates structural understanding from lexical matching.
**Known imperfection:** ideally paraphrases are not written by whoever wrote the gold. In this
build they are, which is a real bias and is recorded in `docs/limitations.md` rather than
papered over. Reporting mean *and worst-case* across paraphrases is the partial defence.

### LB-4 — Informative-task-rate threshold
**Taken:** 60%, configurable, reported in every run summary as a headline health number.
**Why:** it is a judgment call and admitting so is better than pretending it is derived. What
is *not* a judgment call is that the figure must be measured and surfaced — a suite quietly
full of ceiling tasks produces confident, meaningless numbers.

### LB-5 — Whether to publish gold data at all
**Taken:** publish, document the risk, record `authored_at` per task so contamination is at
least detectable.
**Why:** a benchmark whose gold is secret cannot be audited, and an unauditable benchmark is an
assertion rather than a measurement. The cost is real: public gold enters future training
corpora, and later models may score well on old tasks for reasons that have nothing to do with
the candidate. Provenance dating means a suspicious jump on old tasks can be *identified* as a
contamination signal rather than celebrated as a candidate win.

### LB-6 — (Added during build) LLM replay cache as a first-class artifact
**Taken:** build the replay cache early rather than as a later optimization, stored inside the
run directory.
**Why:** it is the single highest-leverage piece of infrastructure in the project. It makes
grader changes, scoring changes, and newly-invented metrics testable against historical runs at
zero cost, and it makes agent runs reproducible *after the fact* even though the provider is
not deterministic. Deferring it means every scoring iteration costs a full re-run.

---

## LeanVFS

### LV-1 — State location
**Taken:** OS cache dir keyed by repo fingerprint, not in-repo. `--state-dir .leanvfs`
reproduces in-repo behaviour for anyone who wants it, and when used, the path is appended to
`.git/info/exclude` if absent.
**Why:** in-repo state pollutes the working tree and gets accidentally committed.

### LV-2 — On-disk mirror default
**Taken:** off.
**Why:** the mirror is a second consistency domain — its own staleness bugs, atomic-rename
choreography, write amplification, and golden tests — serving a consumer that does not yet
exist. Grep-over-projection is a legitimate hypothesis, so the mirror stays behind a flag for
LeanBench to test, rather than being assumed correct on day one. It should earn its way in via
an experiment.

### LV-3 — IDF refresh policy
**Taken:** `sync`. `drift` is implemented and available but off.
**Why:** this is the resolution of the single most expensive latent contradiction in the
original spec — repository-wide IDF cannot coexist with both "incremental converges to clean"
and "one edit never triggers repo-wide work". Freezing IDF per generation resolves it. `drift`
reintroduces nondeterminism, so LeanBench must opt into it knowingly.
**General rule this generalizes to:** no global statistic may be read during incremental update
unless it is generation-frozen. Enforced by an architectural test.

### LV-4 — Second language adapter
**Taken:** none until Python clears the handoff gate.
**Why:** LeanBench's discrimination gate is on HTTPX, which is Python. A second rich adapter
before the first one is proven is unmeasured work. The `LanguageAdapter` seam exists from day
one so adding one is purely additive.

### LV-5 — Ambiguous-reference emission
**Taken:** emit up to 3 R3 candidates, configurable.
**Why:** emitting candidates costs tokens and adds noise; emitting none loses real edges. There
is no way to reason this to an answer from first principles — it is an early LeanBench
experiment, and `max_ambiguous` is exposed as a tunable precisely so the experiment is a config
sweep rather than a code change.

### LV-6 — (Added during build) Implementation language
**Taken:** Python, with a measured re-entry criterion for a Rust port.
**Why:** see `docs/adr/ADR-002-implementation-languages.md`. Summarized: token efficiency is
language-independent, and building the Rust version first means paying the toolchain cost
before learning whether the design saves any tokens at all.
