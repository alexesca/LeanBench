# Benchmarking Engineering

*How you find out whether a thing actually works — and why most people never do.*

---

## In one page

Suppose you make something better. A faster engine, a smarter search tool, a new drug, a robot
that grips more reliably. How do you know you actually improved it?

The intuitive answer is: you try it, and you can tell. This answer is wrong more often than
almost anyone believes. Not because people are careless, but because of three things that are
true of every field at once:

1. **Changes that feel significant often aren't**, and changes that feel trivial sometimes are.
   Intuition is trained on the mechanism, not the outcome.
2. **Measurements wobble.** Run the same test twice and you get different numbers. If the wobble
   is bigger than the improvement, you are reading noise and calling it progress.
3. **Once a number matters, it starts to be gamed** — usually by accident, by the honest process
   of optimising toward it.

**Benchmarking engineering** is the discipline of building a measuring instrument careful enough
to survive all three. Not "running some tests" — designing the measurement itself as a system,
with its own requirements, its own failure modes, and its own proof that it works.

The core insight is uncomfortable: **before you can trust what your measurement says about the
thing, you must prove the measurement can tell things apart at all.** A scale that reads 70 kg
for everyone is perfectly repeatable and completely useless. Most homegrown benchmarks are that
scale, and nobody checks.

This article is about that discipline in general, and then in detail. The worked example is a
code-search tool I built and a benchmark I built to judge it. The benchmark's most valuable
output was not a leaderboard. It was **repeatedly proving me wrong** — including twice about
changes I was confident would help, and which measurably made things worse.

The last section argues this generalises: the same structure, and the same failure modes, appear
in drug discovery, robotics, and empirical research. Everywhere the question is "did that help?",
the same trap is waiting.

---

## Part I — What benchmarking engineering is trying to accomplish

### The real goal is not measurement. It is a trustworthy feedback loop.

It is tempting to think the point of a benchmark is to produce a number. It isn't. A number is
a means. The point is to make a **loop** — change something, learn whether it helped, change
again — that converges on better rather than wandering.

Whether the loop converges depends entirely on the quality of one signal: *did that help?* If
the signal is good, mediocre ideas plus many iterations beats brilliant ideas plus none. If the
signal is bad, the loop is a random walk that feels like progress, and more iterations make it
worse, because each step compounds a mistake with confidence.

That reframing has a consequence worth stating plainly: **improving the measurement is often
higher-leverage than improving the thing.** A team that can reliably detect a 1% improvement
will, over a year, beat a smarter team that can only detect 10% improvements. The second team
throws away every good idea that wasn't dramatic.

### The two ways a measurement betrays you

Nearly every benchmark failure reduces to one of two errors. They need different defences, so
they need different names.

**Type I — you report noise as signal.** Your measurement varies more than the effect you are
looking for. You measure a change, it looks positive, you keep it. Repeat the measurement and
the sign flips.

This is *worse than not measuring*. Someone with no data knows they are guessing and stays
appropriately humble. Someone with noisy data believes they are informed, and will defend the
belief — with a number, which is very hard to argue with.

The defence: **measure the noise before you measure anything else.** Run the identical setup
repeatedly with nothing changed. Compute how much the result varies by itself. From that,
compute the *minimum detectable effect* — the smallest real difference your setup can
distinguish from luck. Then refuse, mechanically, to report anything smaller as a finding.

**Type II — your measurement cannot tell things apart.** Everything scores about the same. The
numbers are stable, reproducible and meaningless: you are measuring something, but not the thing
you named.

This one is treacherous because it looks healthy. Low variance reads as rigour. You can iterate
for months against a benchmark that was never able to see the difference between your work and
doing nothing.

The defence: **prove discrimination before trusting anything.** Include reference points you
already know differ, and require the instrument to separate them. If it can't distinguish a
system you *know* is bad from one you *know* is good, no result it produces means anything.

### The four parts, and why weakness in one destroys the whole

| Part | The question | If it's weak |
|---|---|---|
| **Workload** | What are we measuring on? | Results don't transfer to reality |
| **Metric** | What counts as better? | You optimise the wrong thing, efficiently |
| **Baseline** | Better than *what*? | No frame of reference; any number looks fine |
| **Method** | How do we know it's real? | You measure noise and believe it |

Most in-house benchmarks have a workload, a metric, a weak baseline and no method. That specific
combination reliably manufactures confident, wrong conclusions — which is a worse outcome than
having no benchmark at all.

### Baselines are the instrument, not the opposition

The most common way a benchmark misleads is not fraud. It is a weak baseline.

If you compare your system to a straw man, it wins, and you learn nothing. The strongest single
indicator that a benchmark is honest is that its baselines are *good* — that real effort went
into making the alternatives as strong as they can be.

There is a second, subtler requirement: each baseline must **respect its own capability
ceiling**. If your "simple text search" baseline quietly uses a parser, the ladder no longer
measures what it claims. Each rung must be the best honest version of its category, and no
better.

### Metrics get gamed, so specify them adversarially

Anything measured will eventually be optimised against — usually not cynically, just by the
normal process of trying to improve the number. So write the definition as though someone were
trying to cheat it, because eventually someone will, and it will probably be you by accident.

Concretely, for a metric like "how much context does this tool consume":

- **Count the packaging, not just the payload.** Otherwise a system hides content in structure.
- **Charge for deferred work.** If the answer is "look at lines 40–110," bill the reader for
  going and looking. Otherwise the cheapest system is the least useful one.
- **Fix the unit.** Two measurements taken with different rulers cannot be compared, and should
  be rejected rather than warned about.
- **Weight by success.** A system that answers nothing consumes nothing. Without this, doing
  nothing is optimal.

That last one has a sharp edge. Measuring cost only over successes is correct — cost spent on a
wrong answer isn't efficiency — but it rewards **failing fast**: answer only the easy cases,
post a beautiful number. The defences must be structural: always print the success rate beside
the cost, publish the product of the two as the headline, and compare systems only on cases both
handled.

### Separate the fast loop from the trustworthy loop

There is usually a tension: the measurement you most trust is slow and expensive; the one you
can run constantly is cheap and partial. The instinct is to compromise into a single
middle-ground measurement. This is a mistake — it produces one loop that is too slow to iterate
against *and* too crude to trust.

Better: build both, keep them separate, and **track whether they still agree**. The cheap loop
drives daily work. The expensive one runs occasionally and validates that the cheap one is still
predicting reality. When they diverge, the cheap loop has become a proxy you are overfitting to,
and that divergence is itself one of the most valuable signals available.

---

## Part II — The loop in practice

This is the part that is usually omitted, because it is unflattering. What follows is the actual
sequence of experiments on a real system: what I tried, what I measured, what I kept, and what
I had to throw away.

**The system:** LeanVFS, a semantic index over a codebase. An AI coding assistant asking "where
does this code decide to follow a redirect?" can either read files until it finds out — accurate
and enormously expensive — or consult an index. LeanVFS is the index. The claim is that it
answers with far less material.

**The benchmark:** LeanBench, which treats LeanVFS as one anonymous candidate among five,
alongside raw file reading, `ripgrep`, `ctags`, and a minimal syntax-tree index. It runs 50
hand-written questions over a pinned snapshot of a real open-source library, each with three
differently-phrased variants.

### Step 0 — Build the instrument, then check the instrument

The first real measurement produced this, on retrieval quality (higher is better):

| | Score |
|---|---|
| Raw file reading | 0.274 |
| ripgrep | 0.322 |
| CTags | 0.464 |
| Minimal AST | 0.587 |
| **LeanVFS** | **0.598** |

A clean, monotonic ladder, with my system on top. Exactly the result I wanted — which is
precisely when you should get suspicious.

Checking the per-variant breakdown, one number was impossible: a score of **1.12** on a metric
mathematically bounded at 1.0.

The cause was a subtle bug in my grader. Credit was awarded per *matching result*, while the
ideal-case denominator was computed from *distinct correct answers*. So ten hits inside one
correct file earned ten units of credit against a maximum of one. It inflated exactly those
systems that return many symbols from the same file — the syntax-tree index and mine.

Corrected, the ladder was no longer a ladder:

| | Before (broken) | After (correct) |
|---|---|---|
| Raw file reading | 0.274 | 0.274 |
| ripgrep | 0.322 | **0.322** |
| CTags | 0.464 | 0.224 |
| Minimal AST | 0.587 | 0.297 |
| LeanVFS | 0.598 | 0.335 |

Look at which rows moved. Raw file reading and `ripgrep` are **unchanged to four decimal
places** — they return file-level results, so there was never duplicate credit to award them.
Only the three symbol-oriented systems fell, and they fell in proportion to how heavily they
cluster results within a file. The bug was not random error; it was a systematic thumb on the
scale for exactly one architectural style, which happened to be mine.

`ripgrep` — plain text search — now beat both structural indexes. My margin shrank from
comfortable to slim.

**This is the single most important event in the project.** For a period, the instrument built
to judge the tool was quietly flattering it, in the exact direction its author would have
preferred. Had I not checked a number that was impossible on its face, every subsequent decision
would have been optimised against a broken signal.

The lesson generalises past software: *validate the instrument against known-impossible values
before you trust it on unknown ones.* Bounds checks on your own measurement are not pedantry.
They are the cheapest fraud detection available, including against yourself.

### Step 1 — Diagnose before hypothesising

Text search beating a structural index is surprising. The tempting move is to start improving
the ranking. The correct move is to find out *why*.

I measured something I had not thought to measure: how many **distinct files** each system
returns in its top ten.

| | Distinct files in top 10 |
|---|---|
| ripgrep | 10.0 |
| LeanVFS | 5.1 |
| Minimal AST | 3.9 |

There it was. Symbol-oriented indexes spend their limited slots on several symbols from the
*same* file. When the answer spans multiple files — which most non-trivial questions do — a
concentrated list covers less ground for the same cost. `ripgrep` wasn't smarter; it was more
*spread out*.

That diagnosis suggested a fix nobody would have guessed from intuition: cap how many results
any single file may occupy.

### Step 2 — Sweep, don't guess

Having a fix is not having a value. So I swept it:

| Cap per file | Score |
|---|---|
| off | 0.3314 |
| 1 | 0.3157 |
| 2 | 0.3307 |
| **3** | **0.3349** |
| 5 | 0.3310 |
| 8 | 0.3314 |

Three is best. Note that a cap of 1 — maximum diversity, the "obvious" extreme — is *worse than
no cap at all*. Intuition would have picked either 1 or 5. The measured answer was neither.

Gain: about +1%. Real, small, and honestly reported as small. Which raises the obvious question:
is +1% worth this effort? Only because the measurement was free and deterministic. That
economics matters — cheap measurement makes small improvements worth capturing; expensive
measurement means only large effects are worth chasing.

### Step 3 — The confident hypothesis that was wrong

The biggest weakness was clear from the data: of 50 questions phrased in plain English (no
codebase jargon), **18 scored exactly zero**. Not weak — zero.

Investigating specific failures made the cause look obvious. The query *"what happens if I pass
a bytes body in the argument meant for form fields"* returned nothing relevant, because words
like *what*, *happens*, *pass*, *meant* dominated the scoring. One result matched a documentation
theme colour, because both the question and the theme contained the word *media*.

The diagnosis: **query text was not being normalised the same way indexed text was.** The index
stripped filler words; the query did not. Asymmetric normalisation is a genuine, textbook defect,
and symmetric normalisation is the textbook fix. I was confident.

I implemented it — a query-side stopword list plus weighting rare terms more heavily than common
ones — and measured:

| Variant | Score |
|---|---|
| baseline (unchanged) | **0.335** |
| stopword list only | 0.322 |
| rare-term weighting only | 0.321 |
| both (the "correct" fix) | 0.317 |

**Every variant was worse.** The most theoretically correct one was the worst of all.

I reverted it. The code retains the machinery behind a default-off switch, with the measurements
written next to it, because "we tried this and it lost" is a result worth being able to re-run
on a different codebase.

I still do not have a satisfying explanation. The honest state of knowledge is: the obvious fix
for the biggest known weakness makes things worse, for reasons not yet understood. Without a
benchmark, this would have shipped as an obvious improvement and quietly degraded the system.

### Step 4 — A prior overturned, and a bug hiding behind it

Next I looked at what was being indexed. The system was recording calls to trivial built-in
functions — `len`, `str`, `isinstance` — as if they carried meaning. The design document called
these noise and specified suppressing them.

Investigating why suppression wasn't working exposed something else: **the suppression setting
had never taken effect at all.** The component read its configuration from a section that was
never passed in, so every lookup silently fell back to a built-in default. No error, no warning
— for the system's entire life it had behaved differently from its own documentation, and the
only reason anyone noticed was that a measurement disagreed with a config file.

I fixed it. Suppression started working. And the results got *worse*:

| | Score | Index size |
|---|---|---|
| record trivial calls | **0.3334** | larger |
| suppress them (the "correct" behaviour) | 0.3291 | 3.96× source |

The "noise" was useful. Those terms give a text index more surface to match against, and they
cost nothing in the metric that mattered, because they live in the index rather than in what
gets sent to the model.

So I flipped the default to contradict the design document, and wrote the measurement into the
config file beside the setting, so the next person sees the evidence rather than the intuition.

That is now two design beliefs — the query-normalisation fix in Step 3, and the noise-suppression
assumption here — held by the person best positioned to be right, tested, and both wrong.

### Step 5 — Check the direction, not just the magnitude

Later, demonstrating the system's update behaviour on a live repository, I deleted a file and
searched for its contents. The results still included it. Deleted files remained findable — the
index would confidently direct an agent to a path that no longer existed.

The benchmark never caught this, and could not have: every question in the suite was asked
against a static snapshot. The suite had no concept of deletion, so no score could ever move.

This is worth being precise about, because it cuts against the argument for heavyweight
measurement: the elaborate statistical apparatus missed a serious bug that a brief manual walkthrough
found immediately. **A benchmark measures what you pointed it at. It cannot tell you
that you pointed it in the wrong direction.**

The two techniques are complements, not substitutes, and the cheap one should come first.

### What the loop actually produced

| Experiment | Expected | Measured | Kept? |
|---|---|---|---|
| Fix impossible-value bug in grader | — | reordered the entire ranking | yes |
| Result diversification, cap 3 | small gain | +1% | yes |
| Query-side normalisation | large gain | **−5%** | **reverted** |
| Suppress trivial-call noise | small gain | **−1.3%** | **default inverted** |
| Fold write-ahead log after commit | none | index 4.18× → **1.79×** \* | yes |
| Fix symbol-identity reallocation | correctness | fixed silent corruption | yes |

\* *Later extraction work brought the index back up to 3.96× of source, so 1.79× is the
effect of that one change, not the figure the system ships at today.*

Two of six changes I expected to help, hurt. One I expected to be cosmetic more than halved the
index. That hit rate — roughly a third of confident predictions being wrong in *direction* — is
the entire argument for measuring, and it is not unusual. It is what the inside of an honest
optimisation loop looks like.

---

## Part III — The technical dive

How the system under test actually works, for readers who want the mechanism.

### The problem shape

A repository is too large to read. An index must answer questions about it using far less
material than the source itself. The design constraint is unusual: **the output is consumed by a
language model with a hard budget**, so every byte returned has a price, and the tool is judged
on usefulness *per byte*.

That inverts a normal search engine's incentives. A web search engine wants to show you more. A
context-budget search engine wants to show you the least it can get away with.

### The pipeline

```
discover → classify → hash → parse → canonical model
   → resolve relationships → priority/budget → SQLite + full-text index → queries
```

**Canonical model.** Parsers emit *facts*, never formatted text. A symbol carries a signature,
a return type, what it raises, what it calls, its side effects, and its docstring — as
structured data. Rendering happens later and separately, so changing the output format doesn't
require re-indexing. This decoupling exists specifically to make format experiments cheap,
because output format is one of the highest-leverage things to tune.

**Stable identity.** Every symbol has a key that is deliberately *not* line-dependent:

```
python:src/auth/service.py:method:AuthService.login(email,password)
```

Language, path, kind, qualified name, and a discriminator built from parameter *names only* — so
adding a type annotation doesn't reassign identity. This is load-bearing for incremental
updating, and I found out how load-bearing the hard way (below).

**The four-hash invalidation ladder.** Each symbol carries four hashes, and each gates a
specific amount of work:

| What changed | What is recomputed |
|---|---|
| nothing (same bytes) | nothing — skip the file entirely |
| formatting/comments only | line numbers; no re-analysis |
| the signature | callers must be re-checked; they may no longer match |
| the body | this symbol's outgoing references only |
| the docstring | documentation facts only |

The result: editing one file in a 125-file repository reparses **one file and skips 124**, in
about 70 ms against a full rebuild of 1,100 ms.

**Relationship resolution — the hard part.** Determining that `repo.find_by_email` refers to a
specific function, without running a type checker, is the accuracy problem in this system.

It runs in two phases: per-file extraction emits *unresolved references* with syntactic hints;
then a single sequential pass resolves them against the complete symbol table. Resolution is
graded by confidence, from "same file, exact match" down to "unresolved — we record the raw text
anyway", because *"calls something named `stripe.charge`"* is useful even when unresolved.

Honest status: only about 20% of call references resolve at high confidence, against a 60%
design target. This is the largest known weakness, and the benchmark says so in every report.

**The budget engine.** Given a token budget and a set of candidate facts:

```
1. sort by (priority, confidence, kind, value)   — a total order, so it's deterministic
2. admit greedily while the budget holds
3. respect per-category caps before the global budget
4. record every dropped fact and what it would have cost
5. return that drop record alongside the answer
```

Step 4 is not optional. "You spent 34% of the budget on keywords and dropped 12 test
expectations" is the signal needed to reallocate. And silent truncation is a correctness bug: a
consumer that can't tell it got a partial answer doesn't know to ask for more.

### The bugs worth knowing about

Three found by measurement, not review — all of which produced no error of any kind:

**Symbol identity reallocation.** Re-indexing a changed file deleted its symbol rows and
inserted fresh ones with new internal ids — while references *from other files* still pointed at
the old ids. One file edit silently corrupted relationships across the entire repository. The
fix relies on the line-independent identity above: update in place rather than delete-and-insert.

**Hashes computed two different ways.** A file larger than the size cap was hashed over N bytes
when written and N+1 bytes when checked, so it never compared equal and was reparsed on *every
single query, forever*. A second instance: unreadable files stored an empty hash, which never
matches anything. Neither errored. They just wasted work indefinitely, which is the failure mode
that survives longest because nothing ever complains.

**A secret leak.** The system has a strict rule that credentials never reach storage, enforced
by routing all fact values through a single redaction chokepoint. Writing the test that had been
specified but never written found that **docstrings bypassed it entirely** — they travel on the
symbol record, not as facts. A credential pasted into a module docstring was stored verbatim.
Exactly the "second path" the design claimed could not exist.

### Where it ended up

50 questions, one real library, versus four alternatives:

| Candidate | Success rate | Context per success |
|---|---:|---:|
| Raw file reading | 0.009 | 5,052 |
| ripgrep | 0.092 | 1,488 |
| CTags | 0.000 | — |
| Minimal AST | 0.005 | 564 |
| **LeanVFS** | **0.369** | **435** |

Four times the success rate of the next best at a third of the cost — and still wrong about two
thirds of the time. Both halves are the result. The relative ordering is what the benchmark
establishes; the absolute number is a reminder that "best of five" and "good" are different
claims.

---

## Part IV — This is not about software

The structure above is not specific to code. Strip out the domain and what remains is: *a loop
that needs a trustworthy signal, and an instrument that has to be validated before its outputs
mean anything.* That shape appears wherever anyone asks "did that help?"

### Drug discovery

The parallel is close enough to be uncomfortable. The workload is a patient population; the
metric is an endpoint; the baseline is placebo or standard of care; the method is randomisation
and blinding.

Every failure mode maps directly:

- **Type I** is a promising Phase II result that evaporates in Phase III — an effect smaller than
  the noise, reported as real. Clinical trials answer with pre-registration and power
  calculations: decide what you'll measure and how many subjects you need *before* looking.
- **Type II** is a trial that can't distinguish drug from placebo because the endpoint is too
  crude or the population too heterogeneous.
- **Metric gaming** is surrogate endpoints — measuring a biomarker because it's cheap and fast,
  then discovering the biomarker moved and the patients didn't. Torcetrapib is the canonical
  case: it raised HDL cholesterol substantially, exactly as the surrogate demanded, and its
  Phase III trial was halted in 2006 because mortality went *up*. The metric was not wrong about
  HDL. It was wrong that HDL was the thing that mattered.
- **Baseline quality** is why placebo control is non-negotiable, and why "compared to no
  treatment" is a much weaker claim than "compared to the best available treatment."

Medicine got here first, and paid for the knowledge. The software industry is still relearning
it, generally by rediscovering that its A/B test wasn't powered.

### Robotics

The workload is a task distribution; the metric is success rate under specified conditions; the
baseline is the previous controller or a scripted one.

The signature failure is Type II in disguise: a grasping policy evaluated on a fixed set of
objects, in one lighting condition, on one table. It reaches 95% and transfers terribly, because
the benchmark measured performance *on that table* and everyone read it as competence at
grasping. The defence is the same — deliberately include held-out conditions you expect to be
hard, and treat a benchmark that everything passes as broken rather than solved.

There is a well-documented instance of the same failure in autonomous driving: systems evaluated
against benchmark suites of predominantly daytime, clear-weather, well-marked-road footage, whose
performance degrades sharply on the conditions the suite under-represents. The benchmark was not
lying; it was answering a narrower question than the one everyone was reading it to answer.

Robotics also has an unusually honest advantage: reality provides an unforgeable evaluation.
The robot either picks up the object or it doesn't. Fields without that grounding — including
most of software — must construct their ground truth, and constructed ground truth can be wrong
in ways physics cannot.

### Empirical research

The replication crisis is a Type I failure at civilisation scale: thousands of published findings
that were noise, reported as signal, by researchers who followed the norms of their fields. When
the Open Science Collaboration attempted direct replications of 100 psychology studies in 2015,
roughly a third to 40% reproduced, depending on the criterion used — and the replicated effects
were on average around half the original size. Nothing in that required a single fraudulent act.

The mechanisms are the same ones described above, with the incentives inverted. Publication
favours positive results, so negative results — the highest-value output of an honest loop —
are systematically discarded. Flexible analysis lets a researcher find *a* significant result
without any single dishonest act. And the "baseline" is often no comparison at all.

The remedies invented in response are exactly the ones this article argues for: pre-registration
(commit to the metric before seeing data), reporting effect sizes rather than only significance
(how *much*, not just whether), and treating replication as first-class work.

### Machine learning

The field with the most benchmarks and, arguably, the most benchmark-driven self-deception.

Test-set contamination is a Type I failure with a distinctive cause: the answers leak into the
training data, so the measurement stops being independent of the thing measured. Leaderboard
overfitting is Goodhart's law running at the speed of publication — a whole field steering toward
one number until the number stops corresponding to the capability it was proxying for.

This is why the benchmark described here records an authoring date on every question. It cannot
prevent contamination. But if performance jumps on old questions and not new ones, that pattern
is *diagnosable* rather than celebrated as progress.

### The invariant

Across all of them:

1. You cannot improve what you cannot measure, **and you cannot measure what you have not
   validated.**
2. The instrument needs a correctness argument of its own, independent of what it measures.
3. Baselines determine what any result *means*. Weak baselines make strong-sounding, empty claims.
4. Effects smaller than your noise floor are not small findings. They are **non-findings**.
5. Every metric will be optimised against. Specify it as though someone is trying to cheat it.
6. **Negative results are the highest-value output** and the one every incentive structure
   discards.

---

## The costs, stated plainly

An article that skips this is advertising.

**It is genuinely expensive.** In this project the measurement apparatus is larger than the thing
it measures: roughly 8,700 lines for the tool, 17,200 for the benchmark, baselines and question
set. Writing questions with verified answers is slow, unglamorous work, and it — not the code —
is the bottleneck.

**Small suites have limited resolution.** Fifty questions is not many. The measurement here is
perfectly stable — zero variance across ten repetitions — but stability is not power. A real 2%
improvement is not reliably distinguishable from none, and slicing by category leaves too few
questions per slice to support any claim. That constraint is set by human authoring time, not by
statistics.

**It biases toward the measurable.** What's scored gets attention; what isn't gets neglected.
Goodhart's law is not a footnote here, it's the central occupational hazard.

**Ground truth rots, and leaks.** It goes stale as the target moves, and published answers end up
in future training data.

**Self-authorship is the deepest problem.** Here, the questions, the baselines, the grader and
the system under test were all built by the same author. Two of the celebrated "catches" above
are bugs in the benchmark's *own* code, found by its own author — a skeptic can fairly read that
as "he eventually noticed his own mistakes," not "the method works."

What survives that objection is narrower but real: **the two overturned priors.** Nobody sets out
to discover that the textbook fix loses. Those results were unwanted, arrived at against the
author's explicit expectation, and were not reachable by reasoning harder about the same code —
because reasoning about the same code is what produced the wrong expectation. The bug-catching
is weaker evidence and should be read as such.

The only real remedy is somebody else. **The strongest check on a benchmark is always the one its
author cannot run.**

---

## When not to build one

Benchmarking is a tool, not a virtue.

- **When the effect is obvious.** A 100× speedup does not need statistics.
- **When the thing you care about isn't quantifiable.** A bad proxy for code readability is worse
  than admitting it takes judgement.
- **When you'll decide once.** The apparatus pays off across iterations. For a one-time call, a
  careful experiment suffices.
- **When you can't build honest baselines.** Without a credible comparison, you'll get a number
  but not a meaning.
- **When an existing benchmark fits.** Build bespoke only when existing suites measure something
  materially different. Here they did — standard suites measure end-to-end task success, which
  entangles retrieval quality with model quality, and the question was specifically about
  retrieval. If your question is one an existing suite already answers, using it is almost always
  right.

A heuristic: build one when you expect to iterate **and** expect some of your intuitions to be
wrong. If you're confident all your intuitions are right, either you're in a well-understood
domain — skip it — or you are precisely the person who most needs one.

---

## A practical order of operations

1. **Build the crude demonstration first.** A sixty-line script showing the effect on one real
   example is more persuasive than an elaborate harness, and tells you early whether there's
   anything worth measuring. *(This project did it in the opposite order. The cheap demo ended up
   doing more convincing than the entire benchmark, and it found a bug the benchmark structurally
   could not.)*
2. **Establish baselines before the metric.** The comparisons constrain what a metric can mean.
3. **Validate the instrument.** Noise floor, then discrimination gate. Check for impossible
   values. Only then is a result interpretable.
4. **Then optimise.**

Step 3 is the one that gets skipped, and it decides whether anything after it means anything.

---

## The uncomfortable conclusion

The main return on benchmarking is not the leaderboard. It is the **negative results** — the
changes you were sure about that turned out to be wrong, and would otherwise have shipped.

That is an awkward thing to fund: infrastructure whose primary output is "the change you were
excited about doesn't work." But it is the one output unobtainable any other way, and its absence
is how organisations accumulate years of confident, unexamined, ineffective work — in codebases,
in labs, and in clinical pipelines.

Benchmarking engineering is best understood not as scorekeeping but as a device for **being wrong
faster, on purpose, in public.** Judged that way, it earns its cost.

---

*The systems described are real, the measurements reproducible, and both the tool and its
benchmark are open — including the results that are unflattering. Every number quoted was
produced by the code and can be recomputed from stored run artifacts.*
