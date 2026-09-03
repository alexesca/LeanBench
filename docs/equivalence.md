# Incremental equivalence — a concrete, testable definition

"Semantically equivalent after incremental update" is not testable as prose. This page makes it
mechanical, because incremental correctness is where repository-intelligence systems quietly
break: the index answers confidently and the answer is stale.

---

## 1. Comparison is through protocol outputs only

Equivalence is compared through **candidate protocol responses**, never through the candidate's
internal files, database rows, or logs.

This is deliberate. Comparing internal state would test that two code paths produce the same
bytes; comparing protocol output tests **the only thing an agent can actually observe**. A
candidate is free to store incremental state differently from rebuilt state, so long as no
query can tell.

## 2. The procedure

```
1. Index the repository from clean          → state A
2. Apply a seeded mutation                  (§12 mutation classes)
3. Notify the candidate (update_repository) → state B_incremental
4. Measure update latency
5. Issue the probe battery                  → responses B_inc
6. Wipe candidate state; index from clean   → state B_rebuilt
7. Issue the identical probe battery        → responses B_clean
8. Compare B_inc against B_clean per §3
```

**Global statistics are pinned across the comparison.** If a candidate derives any
repository-wide statistic (IDF, corpus term frequency, symbol popularity, path priors), the
rebuild is performed at the *same frozen generation* as the incremental state. Otherwise the
comparison is rigged: repository-wide statistics change on every edit, so incremental and
rebuilt state legitimately differ, and the test would fail for a reason that has nothing to do
with correctness. This is why the candidate protocol exposes `idf_generation` in `meta`, and
why `leanvfs verify --pin-idf` exists.

## 3. Comparison rules by result shape

### 3.1 Set-valued results — symbols, files, references, tests, dependencies
**Exact set equality on normalized identifiers.** Any difference is a failure, reported with the
diff (added / removed, with identifiers).

Normalization: repo-relative POSIX paths; symbols by their stable qualified form. Ordering is
ignored for set-valued results; **membership is not negotiable.** A stale symbol surviving a
rename, or a new symbol missing after an add, is precisely the bug class this catches.

### 3.2 Ranked results — search
Two conditions, both required:
- **rank-biased overlap ≥ 0.95 at depth 10** (`rbo_p = 0.9`)
- **exact set equality on the top-1 result**

Ranking may drift slightly — scores derived from float arithmetic over changed corpora will not
be bit-identical, and demanding that would be a false alarm generator. **Membership may not
drift**, and the single best answer may not change.

### 3.3 Scalar stats
Within a configured relative tolerance (`equivalence.scalar_tolerance`, default `0.01`).
Counters that are legitimately path-dependent (`parse_full`, `parse_incremental`, `cache_hit`)
are **excluded by name** — they are *expected* to differ, that is the whole point of an
incremental path — and the exclusion list is explicit config, never an implicit skip.

### 3.4 Metadata
`index_state` must be `ok` in both. An incremental update that leaves the index `stale` or
`partial` while the rebuild is `ok` is a failure even if every payload matches.

---

## 4. Reporting

`incremental_equivalence_rate` = (mutations whose comparison passed) / (mutations applied),
reported across all mutation classes and **broken down by class**, since a system may handle
comment edits perfectly and renames not at all — an average would hide that.

**The rate is always reported beside update latency, never separately.** Per `docs/scoring.md`
§2.6 the incremental score is `latency_score × equivalence_rate`, so a fast-and-wrong candidate
scores below a slow-and-right one. Presenting update latency alone would reward exactly the
wrong behaviour, and is the single easiest way for a benchmark in this space to mislead.

---

## 5. Mutation classes

Initial: whitespace, comment edit, function-body edit, signature edit, symbol rename, file
rename, new test, documentation edit.
Later: function move, config rename, route change, schema change.

Each mutation is **seeded and reproducible**: the same seed against the same commit produces
byte-identical repository changes, so a failure can be re-run and debugged rather than merely
observed once.

Expected sensitivity, useful as a sanity check on the harness itself:

| Mutation | Should change | Should NOT change |
|---|---|---|
| whitespace | line ranges only | symbols, facts, relationships, search membership |
| comment edit | doc facts | signatures, calls, relationships |
| function-body edit | calls, effects, keywords for that symbol | that symbol's signature, inbound edges |
| signature edit | signature, inbound edge resolution | unrelated files |
| symbol rename | that symbol, inbound edges → unresolved then re-resolved | unrelated symbols |
| file rename | paths for that file's symbols | symbol identities within the file |
| new test | test symbols, TESTS edges to targets | the targets' own definitions |

---

## 6. The negative test that proves the check works

A test harness must include a **deliberately broken incremental implementation** — one that
leaves a stale symbol behind after a rename — and assert the equivalence check catches it.

Without this, a passing equivalence rate proves nothing: a check that never fails and a check
that cannot fail are indistinguishable from the outside. This is the same reasoning that makes
mutation testing worthwhile, applied to the benchmark's own instrumentation.
