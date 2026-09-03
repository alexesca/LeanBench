# LeanBench Task & Gold Schema — v1

**Normative contract.** Tasks are the benchmark's ground truth. This file defines their
on-disk shape so that task authoring and harness implementation can proceed in parallel.

Tasks live in `suites/<suite-name>/tasks/*.toml`, one task per file, filename `<task_id>.toml`.

---

## 1. Schema

```toml
id            = "httpx-sym-001"        # unique, stable, kebab/numeric; never reused
version       = 1                       # bump when prompt/gold/probes change materially
repository    = "httpx"                # repo id from the corpus manifest
commit        = "b5addb64f0161ff6bfe94c124ef76f6a1fba5254"
category      = "symbol_location"      # §2
difficulty    = "L2"                   # L1..L5, §3
authored_by   = "claude-opus-5"
reviewed_by   = ""                     # REQUIRED non-empty before a task enters a released suite
authored_at   = "2026-09-02"           # contamination provenance, §9.3 of the build spec
tags          = ["redirects", "client"]

# --- Agent Track ---------------------------------------------------------
prompt = """
Where is the logic that decides whether httpx follows a redirect, and what
exception is raised when the redirect limit is exceeded?
"""

# --- Retrieval Track: >= 3 paraphrased probes ---------------------------
# paraphrase_id MUST be one of: literal | intent | symptom
[[probes]]
paraphrase_id = "literal"
op            = "search"
args          = { query = "redirect handling follow_redirects max_redirects", limit = 10 }

[[probes]]
paraphrase_id = "intent"
op            = "search"
args          = { query = "how does the client decide to follow a redirect", limit = 10 }

[[probes]]
paraphrase_id = "symptom"
op            = "search"
args          = { query = "too many redirects error raised after several hops", limit = 10 }

# --- Structured gold -----------------------------------------------------
[gold]
files         = ["httpx/_client.py"]
symbols       = ["Client._send_handling_redirects", "Client._build_redirect_request"]
tests         = ["tests/client/test_redirects.py"]
docs          = ["docs/quickstart.md"]
# ranges are inclusive 1-based line spans; optional but preferred for L1/L2
ranges        = [{ path = "httpx/_client.py", start = 940, end = 1002 }]
# relationships are [source_symbol, KIND, target_symbol] triples
relationships = [["Client.send", "CALLS", "Client._send_handling_redirects"]]

# One-line written justification of WHY this gold is the right answer.
# If you cannot write it, the task is underspecified. REQUIRED.
justification = "The redirect loop and its TooManyRedirects raise both live in _send_handling_redirects; _build_redirect_request constructs each hop."

# --- Execution constraints ----------------------------------------------
required_capabilities = ["search"]
allowed_tools         = ["candidate.search", "candidate.context", "repo.read_range", "repo.search"]

[limits]
max_tool_calls        = 20
max_repository_tokens = 30000
wall_clock_s          = 180
```

### Field requirements

| Field | Required | Validation |
|---|---|---|
| `id` | yes | unique across suite; matches filename stem |
| `repository`, `commit` | yes | resolves in the corpus manifest |
| `category` | yes | member of §2 |
| `difficulty` | yes | `L1`..`L5` |
| `prompt` | yes (agent track) | non-empty |
| `probes` | yes | **≥ 3**, with **≥ 3 distinct `paraphrase_id` values** |
| `gold` | yes | at least one of `files`/`symbols` non-empty; every reference resolves at `commit` |
| `gold.justification` | yes | non-empty |
| `reviewed_by` | for release | non-empty for a task in a released suite; dev suites may leave blank |
| `required_capabilities` | yes | subset of the manifest capability keys |

---

## 2. Categories

`symbol_location`, `api_contract`, `architecture`, `behavior`, `tests`, `documentation`,
`comments`, `configuration`, `change_impact`, `incremental`.

(`coding` is deferred — re-entry criterion in build spec §17.)

## 3. Difficulty

| Level | Meaning |
|---|---|
| L1 | Local — answer lives in one symbol or one file |
| L2 | Cross-file — two or three files must be related |
| L3 | Cross-module — requires following relationships across a package boundary |
| L4 | Architecture — requires understanding a design decision or layering |
| L5 | Repository-wide — requires a global view (impact analysis, conventions) |

---

## 4. Grading semantics (normative — the grader implements exactly this)

### 4.1 Retrieval track

For each probe independently, the candidate's ranked results are reduced to a ranked list of
**gold-comparable identifiers**:

- a `search` hit contributes `symbol` if non-null, else `path`
- a `get_symbol`/`get_context` result contributes its `symbol`
- a `get_tests` result contributes its `path`
- a `get_references` result contributes its `symbol`

Identifiers are **normalized** before comparison:

- paths: POSIX separators, repo-relative, no leading `./`
- symbols: matched against gold with **suffix-qualified equality** — gold `Client.send`
  matches candidate `httpx._client.Client.send` and `Client.send`, but not `send` alone
  unless gold itself is bare `send`. This prevents both false negatives from module
  prefixes and false positives from bare-name collisions.

A result is **relevant** iff it normalizes to a member of
`gold.symbols ∪ gold.files ∪ gold.tests ∪ gold.docs`. A file-level hit on a file that
contains a gold symbol counts as relevant (credit for finding the right file), but a symbol
hit is required to score `Recall@K` on the `symbols` sub-metric.

Computed per probe: `Recall@{1,5,10}`, `Precision@{1,5,10}`, `MRR`, `nDCG@10`
(binary gain, log2 discount), `tokens_returned`, `latency_ms`.

Aggregated per task: **mean across paraphrases AND worst-case across paraphrases.** Both are
reported. A candidate whose worst-case collapses is brittle and the report must say so.

### 4.2 Agent track

`correctness` ∈ [0,1] from the structural grader: the fraction of required gold elements the
agent's final answer demonstrably identified, where "demonstrably" means the element appears
in the answer text under the normalization of §4.1, or the agent read the exact gold range.
Weighted: symbols 0.5, files 0.3, tests 0.1, relationships 0.1 — renormalized over the
non-empty gold categories only.

`repository_context_tokens` is the cumulative count defined in build spec §8.2.

---

## 5. Task quality metadata (computed, not authored)

`leanbench tasks triage` writes `suites/<suite>/triage.json`:

```json
{"httpx-sym-001": {"baseline_scores": {"raw": 0.31, "ripgrep": 0.55, "ctags": 0.62, "minast": 0.88},
                   "discrimination_index": 0.57, "flags": [], "stability_cv": 0.0}}
```

Flags: `ceiling` (all baselines ≥ 0.95), `floor` (all ≤ 0.05), `unstable` (agent-track
CV > 20%), `stale_gold` (a reference no longer resolves — **hard CI failure**).

`discrimination_index` = max(baseline scores) − min(baseline scores).
A task is **informative** iff it has none of `ceiling`, `floor`, `unstable`, `stale_gold`.
Suite gate: **≥ 60% informative**, reported in every run summary.
