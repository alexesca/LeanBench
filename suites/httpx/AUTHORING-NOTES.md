# httpx suite — authoring notes

Repository: `corpus/httpx` @ `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` (encode/httpx,
60 Python files, plus `docs/` and `tests/`). 50 tasks, authored 2026-09-02, unreviewed
(`reviewed_by = ""` on every task — a separate pass must fill it before release).

Counts by category: `symbol_location` 10, `architecture` 8, `api_contract` 7, `tests` 7,
`documentation` 3 + `comments` 2, `change_impact` 5, `incremental` 5, and the three
reserve slots spent on `behavior` 2 + `configuration` 1.
Counts by difficulty: L1 4, L2 19, L3 14, L4 10, L5 3.

A separate 9-task `suites/dev` suite over the same checkout exists purely for the
inner development loop (all L1, 5 tool calls / 4k tokens / 8s each). It is *not* a
measurement suite and its `informative_task_rate_threshold` is 0.

---

## The design rule I applied

For every candidate task I asked: **can the answer be reached by searching for the words
in the prompt?** If yes, the task was either rewritten so the gold sits one relationship
hop away from anything lexically findable, or dropped (see the rejection list).

Three concrete devices recur:

1. **Split gold across a definition site and an enforcement/consumption site.** The
   answer is only complete when both are named, so a system that returns "the file that
   contains the word" scores at most partial credit under the §4.2 weighting.
2. **Deliberate lexical distractors.** Several tasks have a near-identical name in the
   wrong place: `_port_or_default` in `_client.py` vs `normalize_port` in `_urlparse.py`
   (sym-001); `Response.is_redirect` vs `Response.has_redirect_location` (sym-010);
   three different `_build_auth_header` methods (sym-002, test-005);
   `Request(content=)` vs `Request(stream=)` (api-003).
3. **Probes that share no identifiers with the gold.** The `intent` and `symptom` probes
   were written to avoid the answer's symbol names entirely wherever the concept has a
   plain-English name. Measured on a crude lexical (bag-of-tokens, whole-file) baseline:
   the `literal` probe puts a gold path in the top 3 for 47/50 tasks, the `symptom` probe
   for 24/50, and the `intent` probe for only 16/50. That spread is the point — the
   worst-case-across-paraphrase aggregate in TASKS.md §4.1 is where a lexical system
   should visibly collapse and a structure-aware one should not.

---

## What each category targets, and why it should discriminate

### `symbol_location` (10)
Not "where is the class named X". Every one of these asks *which symbol has this
property*, so the name is the output, not the input.

- **sym-001** port normalization: needs the parse pipeline, not the client-side helper of
  a similar name.
- **sym-002** the duplicated Basic-auth header builder: three methods share the name; the
  answer is which *two* are copies.
- **sym-003** `Accept-Encoding` is *derived* from the decoder registry at import time and
  pruned by optional dependencies — a pure cross-module data-flow question.
- **sym-005** credential stripping is one method plus two module-level predicates whose
  correctness depends on default-port logic.
- **sym-006** / **sym-007** / **sym-009**: an effect (`.request` attached, exception
  translated, `.elapsed` populated) traced back to the code that produces it. In every
  case the producing symbol is in a different file from where the user observes it.
- **sym-008** ordering of mounted patterns: the sort happens once in `Client.__init__`;
  a grep for "priority" lands in `_utils.py` and stops there.
- **sym-010** the strict redirect predicate, with `is_redirect` sitting right next to it
  as the wrong answer.

Grep-favourable of this group: **sym-004** (the constant is literally
`MAX_URL_LENGTH = 65536` and the symptom probe quotes the raised message). Kept
deliberately — see "where grep should win" below.

### `architecture` (8)
Layering and design-decision questions where no single file holds the answer.

- **arch-001** what httpx *does not* implement (pooling lives in httpcore; `Limits` is a
  pure configuration carrier across the seam).
- **arch-002** the transport contract — reading a base class's docstring and
  `NotImplementedError` bodies, plus the one call site.
- **arch-003** why two 700-line near-duplicate classes exist and exactly which slice is
  shared (`BaseClient` holds everything I/O-free).
- **arch-004** the nesting order of the auth loop and the redirect loop. This is the
  single best task in the suite: the answer is a control-flow containment fact that
  appears in no comment and no doc page.
- **arch-005** why `_urlparse.py` exists at all (rationale is in a module docstring, which
  most indexers drop).
- **arch-006** end-to-end propagation of a timeout through four layers into a documented
  extension key.
- **arch-007** the two byte-stream protocols and the classes that implement both — plain
  classes, not ABCs, so an inheritance-aware system wins and a name search does not.
- **arch-008** how the public surface is defined and enforced (module `__all__` →
  package `__all__` → `__module__` rewrite → test).

### `api_contract` (7)
"What must I do / what am I allowed to pass" questions answered by reading a contract,
including its failure modes. api-001 (custom `Auth` subclass), api-002 (which `Timeout`
argument combination raises), api-003 (`content=` vs `stream=` header auto-population),
api-004 (the deprecated `data=<bytes>` path), api-005 (why a sentinel and not `None`),
api-006 (immutability of `QueryParams`), api-007 (accepted upload tuple shapes).
These discriminate mostly on the *agent* track: several of them are one file, so
retrieval is easy, but the correctness grader wants the enumeration.

### `tests` (7)
Test-to-target linkage in both directions.

- **test-001** and **test-006** are reverse queries: given a source behaviour, which tests
  break. test-001 in particular has coverage split across two files that attack the same
  code from different levels (end-to-end vs. calling `_redirect_headers` directly).
- **test-002** is an invariant test that fails on a *dependency upgrade*, and the fix is
  in the source, not the test.
- **test-004** links a test technique back to the package class that enables it.
- **test-003**, **test-005**, **test-007** are ordinary coverage-location tasks and are
  the easiest of this group (test-003 is L1).

### `documentation` (3) + `comments` (2)
- **doc-001** and **doc-005** pair a doc page with the source that implements/duplicates
  it; doc-005 exploits the fact that the exception taxonomy is written twice.
- **doc-004** asks the reader to notice that "timeout" is an instance of the general
  request-extensions mechanism.
- **doc-002** and **doc-003** are true `comments` tasks: the gold content is a rationale
  that exists *only* in a code comment (`requests` issue 1704 for the 302→GET behaviour;
  issue #2491 for the `dict` exclusion). Systems that index only signatures will lose
  the content even when they find the file.

### `change_impact` (5)
Reverse relationship traversal.
- **chg-001** rename a base-class method: five implementations/call sites plus docs.
- **chg-002** the trap: adding a decoder silently changes an *outgoing request header*,
  because `ACCEPT_ENCODING` is derived from the same registry.
- **chg-003** the full checklist for exporting a new public name.
- **chg-004** the non-obvious link is that `default_encoding` is not a `Response`
  constructor argument — it is assigned in `_send_single_request`.
- **chg-005** (L5) five consumers of `URL.raw_path` in four modules, including one that
  splits the query back off again and one that signs it.

### `incremental` (5)
Phrased as invalidation questions so they are meaningful even before the harness's
edit-and-requery loop lands: which modules import the changed one, which subclasses gain
a member, which test links must be refreshed. inc-001/003/004 have exactly checkable
answers (I verified the importer sets by grep); inc-002 has a subtlety (`MockTransport`
inherits from *both* bases, `ASGITransport` from only the async one).

### Reserve (3)
- **beh-001** what a 303 does to method, body and headers — three separate helpers.
- **beh-002** the three streaming-misuse exceptions and the fact that they sit outside
  the `HTTPError` tree (they are `RuntimeError`s).
- **cfg-001** every environment variable that changes behaviour, in two unrelated
  modules, both gated on `trust_env`.

---

## Where I expect grep to win (and that is fine)

A suite where a lexical baseline never wins is as unrealistic as one where it always
does. These are the tasks I expect to be `ceiling`-flagged or close to it:

| Task | Why |
|---|---|
| `httpx-sym-004` | the constant name is the answer and the symptom probe quotes the raised message verbatim |
| `httpx-api-002` | the symptom probe is the exact `ValueError` string, which occurs once in the tree |
| `httpx-api-006` | the symptom probe is the exact `RuntimeError` string |
| `httpx-doc-003` | one function, one file; the comment is adjacent to the match |
| `httpx-test-003` | `conftest.py` is the only plausible location and the literal terms are all present |
| `httpx-test-007` | `zstd` occurs in exactly two files |
| all of `suites/dev` | by construction |

That is roughly 6/50 in the measurement suite (12%), which leaves ample headroom over the
0.60 informative-rate gate even if I have misjudged a few more.

## Tasks I am least confident about

Flagging these honestly for the review pass:

1. **`httpx-inc-005`** — "which symbols' test links should be refreshed" is a judgement
   call; a reasonable system could name a longer or shorter list. The gold is the set the
   redirect tests actually exercise, but partial-credit scoring here will be noisy.
2. **`httpx-arch-008`** and **`httpx-chg-003`** — the only meaningful symbol is `__all__`,
   which makes the symbol sub-metric coarse (symbols are weighted 0.5 in §4.2). The real
   content is the *procedure*, which the structural grader may under-credit.
3. **`httpx-inc-002`/`httpx-inc-003`** — the answer is essentially a file list, so the
   symbol weighting is a poor fit; they may look easier than they are.
4. **`httpx-arch-003`** — asks for a design rationale. A verbose answer that names all
   three classes will score well even if the reasoning is wrong; a terse correct answer
   may score badly. Possible `unstable` flag.
5. **`httpx-test-006`** — I verified that `tests/test_config.py` is the only module
   constructing `Timeout` from a tuple, but "which tests would have to change" invites
   listing individual test functions, which the path-level gold cannot credit.
6. **`httpx-chg-002`** — the interesting half of the answer (the outgoing header changes)
   may be missed by an otherwise-correct answer, giving a bimodal score distribution.

If triage shows any of these `unstable`, the right fix is to tighten the prompt to ask
for an enumeration rather than an explanation, not to change the gold.

---

## Considered and rejected (ceiling / floor / unmeasurable)

Recorded because the rejection reasons are reusable signal for the next suite.

| Rejected task | Reason |
|---|---|
| "Where is the class `Timeout` defined?" | pure ceiling: one grep, one hit |
| "What is the value of `DEFAULT_MAX_REDIRECTS`?" | ceiling; demoted to the dev suite (`httpx-dev-002`) |
| "List every exception class in httpx" | `rg '^class .*Error'` wins outright, and the huge gold set turns partial credit into noise |
| "Which HTTP verb methods does `Client` expose?" | ceiling; trivially enumerable from any symbol index |
| "What does `_main.py` (the CLI) do?" | unanchored: the whole file is click decorators, the gold would be a file path, no candidate is distinguished |
| "Explain the digest authentication algorithm" | answerable from pretraining without reading the repository; measures contamination, not retrieval |
| "Where is HTTP/2 implemented?" | the correct answer is "not in this repository" — negative gold is not expressible in the schema |
| "Where is the reason-phrase table for status codes?" | `_status_codes.py` is a flat enum in an obviously-named file: ceiling |
| "Where is IDNA encoding performed?" | one grep on `idna` lands `encode_host`; folded into `httpx-arch-005` as a sub-part instead |
| "Which line raises `NotImplementedError` in `SyncByteStream`?" | range-only gold, trivially greppable |
| "Which test file has the most assertions?" | not a comprehension question; unstable and gameable |
| "Rename `X` to `Y` and update all callers" | the `coding` category is deferred by build spec §17 |
| "What is the httpx version?" | ceiling |
| "Which module defines `URLPattern`?" (as its own task) | ceiling; reframed as `httpx-sym-008`, which asks what makes the *ordering* work |

---

## Verification method

Every gold reference was read at the pinned commit before it was written down, and
`suites/validate_gold.py` re-checks mechanically that:

- the task file parses, its `id` matches its filename, and the fields required by
  TASKS.md §1 are present and well-typed;
- there are ≥3 probes with ≥3 distinct `paraphrase_id` values, each using a real
  protocol op;
- the task's `commit` matches the corpus checkout's `HEAD`;
- every `gold.files` / `gold.tests` / `gold.docs` path exists and is a clean
  repo-relative POSIX path;
- every `gold.symbols` entry is defined (`def` / `class` / module-level assignment) in
  one of the listed gold files, *and* its owning class is too;
- every range lies inside its file, is listed in the gold paths, and brackets a plausible
  construct;
- `gold.justification` is non-empty and relationship triples are well formed.

The validator has a matching negative test: injecting a deliberately broken task produces
23 distinct violations and exit code 1.
