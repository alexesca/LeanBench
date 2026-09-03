# LeanBench Candidate Protocol — v1

**Normative contract.** This file is the single source of truth for the boundary between
LeanBench (the benchmark) and any candidate repository-intelligence system (LeanVFS,
RawRepository, Ripgrep, CTags, MinimalAST, or a third-party system).

Both sides are built against this document. Where this document and any prose spec
disagree, this document wins. Changes here require bumping `protocol_version` and
updating the conformance suite in `leanbench/tests/test_protocol_conformance.py`.

---

## 1. Transport

The candidate is a **subprocess** launched by LeanBench. Communication is
**JSONL over stdin/stdout**: exactly one JSON object per line, UTF-8, `\n`-terminated,
no embedded raw newlines.

- **stdout carries protocol traffic only.** No banners, no progress bars, no ANSI codes.
- **stderr is free-form diagnostics.** LeanBench captures it separately and attaches it
  to failure reports. Writing to stderr never affects grading.
- Responses MAY be returned out of order. Correlation is by `id`.
- The candidate MUST NOT exit on its own before receiving `shutdown`.

A candidate that writes a non-JSON line to stdout is classified `protocol_error`.
A candidate that writes valid JSON failing the response schema is `invalid_response`.
These are distinct classifications and MUST NOT be conflated.

---

## 2. Request envelope

```json
{"id": "7", "op": "search", "args": {"query": "redirect handling", "limit": 10},
 "token_budget": 800, "format": "compact"}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Opaque correlation id. Echoed verbatim in the response. |
| `op` | string | yes | One of §4. |
| `args` | object | yes | Op-specific. May be `{}`. |
| `token_budget` | int \| null | no | Server-side output budget in tokens. `null` = candidate default. |
| `format` | `"compact"` \| `"json"` | no | Default `"compact"`. |

`token_budget` is **advisory in units but binding in behaviour**: the candidate must
attempt to keep its serialized `result` at or under the budget using its own approximate
counter, and must set `meta.truncated` honestly when it drops content. LeanBench's own
tokenizer is authoritative for scoring; the candidate's count is never trusted for the metric.

---

## 3. Response envelope

### Success

```json
{"id": "7", "status": "ok",
 "result": { ... op-specific ... },
 "meta": {"tokens_approx": 742, "truncated": true,
          "dropped": {"keyword": 11, "call": 4},
          "index_state": "ok", "generation": 184, "idf_generation": 12,
          "elapsed_ms": 6.1}}
```

| `meta` field | Type | Required | Notes |
|---|---|---|---|
| `tokens_approx` | int | yes | Candidate's own approximate count of `result`. Advisory. |
| `truncated` | bool | yes | True iff content was dropped for budget. Lying here is a correctness bug. |
| `dropped` | object\<string,int\> | no | Fact-kind → count dropped. Optimization signal for LeanBench. |
| `index_state` | `ok`\|`stale`\|`partial`\|`failed` | yes | Anything not `ok` propagates to the report. |
| `generation` | int | no | Index generation the answer was served from. |
| `idf_generation` | int | no | Frozen global-statistics generation. |
| `elapsed_ms` | float | no | Candidate-measured latency. LeanBench also measures externally. |

### Progress (long `prepare_repository` only)

```json
{"id": "1", "status": "indexing", "progress": 0.42}
```

Zero or more may precede the terminal response for the same `id`. Any op may emit them,
but in practice only `prepare_repository` should. They reset the op timeout.

### Error

```json
{"id": "7", "status": "error", "code": "not_found", "message": "...", "retryable": false}
```

Codes: `unsupported_op`, `invalid_args`, `not_found`, `not_prepared`, `index_error`,
`timeout`, `internal`.

An error response is a **well-behaved** outcome — the candidate answered. It is classified
`candidate_protocol_error` only when the code indicates the candidate could not honour a
capability it declared. `not_found` on a genuinely absent symbol is a normal empty answer,
scored as a miss, not as an infrastructure failure.

---

## 4. Operations

All `result` shapes below are the **`format: "json"`** canonical form. Under
`format: "compact"`, `result` is `{"text": "<compact rendering>"}` and LeanBench counts
tokens of that text. Candidates SHOULD support both; `compact` is the default because it
is the token-efficient path and is what the headline metric measures.

### 4.1 `prepare_repository`

```json
{"op": "prepare_repository", "args": {"path": "/abs/path/to/repo", "commit": "a1b2c3d"}}
```
Result:
```json
{"indexed": true, "files": 412, "symbols": 3118, "cold_index_ms": 4120.5, "index_bytes": 8123456}
```
Must be idempotent for the same `path`. `commit` is informational (LeanBench has already
checked the working copy out); the candidate MUST NOT run git.

### 4.2 `search`

```json
{"op": "search", "args": {"query": "redirect handling", "limit": 10}}
```
Result:
```json
{"hits": [
  {"path": "httpx/_client.py", "symbol": "Client._send_handling_redirects",
   "kind": "method", "line_start": 940, "line_end": 1002, "score": 0.94,
   "snippet": "..."}
]}
```
`path` MUST be repository-relative, POSIX separators. `symbol` may be null for file-level
hits. Ordering is by descending relevance and MUST be deterministic (ties broken by a
total order, never by rowid or hash iteration).

### 4.3 `get_symbol`

```json
{"op": "get_symbol", "args": {"name": "Client.send"}}
```
Result:
```json
{"symbols": [
  {"path": "httpx/_client.py", "symbol": "Client.send", "kind": "method",
   "signature": "send(request, *, stream=False, auth=..., follow_redirects=...)",
   "return_type": "Response", "line_start": 890, "line_end": 938,
   "visibility": "public", "doc": "Send a request."}
]}
```
Name matching accepts bare (`send`) and qualified (`Client.send`) forms.

### 4.4 `get_context`

The money operation. Budget-assembled: identity and signature always; then, by priority,
exceptions → side effects → invariants/security notes → test expectations → resolved
calls → doc refs → keywords. **Never the full implementation body.**

```json
{"op": "get_context", "args": {"symbol": "Client.send", "token_budget": 400}}
```
Result:
```json
{"symbol": "Client.send", "path": "httpx/_client.py", "line_start": 890, "line_end": 938,
 "signature": "...", "return_type": "Response",
 "raises": ["TooManyRedirects"], "effects": ["http", "db:r"],
 "calls": ["Client._send_handling_auth", "Client._build_request"],
 "notes": [{"class": "security", "text": "..."}],
 "tests": ["tests/client/test_redirects.py::test_too_many_redirects"],
 "keywords": ["send", "request", "redirect", "auth"],
 "budget_report": {"admitted": 31, "dropped": {"keyword": 4}, "tokens_approx": 388}}
```

### 4.5 `get_dependencies`

```json
{"op": "get_dependencies", "args": {"path": "httpx/_client.py"}}
```
Result: `{"imports_local": [...], "imports_external": [...], "imported_by": [...]}`
Paths repository-relative; module names for external.

### 4.6 `get_references`

```json
{"op": "get_references", "args": {"symbol": "URL", "limit": 50}}
```
Result:
```json
{"references": [{"path": "...", "symbol": "...", "line": 42, "kind": "USES_TYPE",
                 "confidence": 0.9}]}
```
`kind` ∈ the relationship vocabulary. `confidence` ∈ [0,1] per the resolution tiers.

### 4.7 `get_tests`

```json
{"op": "get_tests", "args": {"symbol": "Client.send"}}
```
Result:
```json
{"tests": [{"path": "tests/client/test_client.py", "symbol": "test_get",
            "line_start": 12, "scenario": "...", "expects": "..."}]}
```

### 4.8 `get_docs`

```json
{"op": "get_docs", "args": {"query": "redirects", "limit": 5}}
```
Result: `{"docs": [{"path": "docs/quickstart.md", "heading": "Redirects", "line_start": 88, "excerpt": "..."}]}`

### 4.9 `update_repository`

```json
{"op": "update_repository", "args": {"changed": ["a.py"], "added": [], "removed": []}}
```
Result: `{"updated": true, "update_ms": 41.2, "files_reparsed": 1, "generation": 185}`
The candidate MAY re-scan to discover changes itself; the hint list is an optimization.

### 4.10 `get_stats`

Result: free-form object. Reserved keys LeanBench reads:
`files`, `symbols`, `facts`, `relationships`, `index_bytes`, `source_bytes`,
`cold_index_ms`, `resolution_rate` (object, tier → fraction), `counters` (object).

### 4.11 `shutdown`

Result: `{"ok": true}`. The candidate must then exit with code 0 within
`shutdown_timeout_s`. LeanBench hard-kills the process group after that.

---

## 5. Manifest — `leanbench-candidate.toml`

```toml
protocol_version = 1

[candidate]
name = "LeanVFS"
version = "0.1.0"

[runtime]
command = "python"
args = ["-m", "leanvfs", "benchmark-server"]
cwd = "."                    # optional, relative to manifest
env = { LEANVFS_PROFILE = "balanced" }   # optional

[timeouts]
startup_s = 10.0
prepare_s = 600.0
op_s = 30.0
shutdown_s = 5.0

[capabilities]
search = true
symbols = true
context = true
dependencies = true
references = true
tests = true
docs = true
incremental = true
```

Capability keys map to ops. A suite declares `required_capabilities`; LeanBench asserts
`required - declared == ∅` at suite start and raises `IncompatibleCandidate` otherwise.
Declaring a capability and then returning `unsupported_op` for it is a
`candidate_protocol_error`, and is deliberately harsher than not declaring it.

---

## 6. Digests

Every run records three digests, and any comparison across differing digests must say so:

- `binary_digest` — BLAKE2b-256 of the resolved `runtime.command` executable if it is a
  file on disk; for interpreted candidates, of the sorted concatenation of the source
  tree's file hashes under the candidate root.
- `manifest_digest` — BLAKE2b-256 of the manifest bytes.
- `config_digest` — BLAKE2b-256 of the candidate's fully resolved config, canonically
  serialized, if the candidate exposes one via `get_stats().config_resolved`.

---

## 7. Failure classification (LeanBench side, normative)

| Situation | Classification |
|---|---|
| Process exits non-zero, or dies, before `shutdown` | `candidate_crash` |
| No terminal response within the op/startup timeout | `candidate_timeout` |
| Non-JSON line on stdout, or unknown `protocol_version` | `protocol_error` |
| JSON that fails the response schema, or unknown `status` | `invalid_response` |
| `unsupported_op` for a **declared** capability | `candidate_protocol_error` |
| Op used that the candidate never declared | `unsupported_capability` |
| LeanBench's own fault (disk, bug, missing fixture) | `benchmark_infrastructure_error` |

Infrastructure errors are **never** scored as candidate failures. A run whose
infrastructure failure rate exceeds 5% is marked `DEGRADED` and `compare` refuses to draw
conclusions from it.
