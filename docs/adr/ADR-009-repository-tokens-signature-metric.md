# ADR-009 — Repository tokens as the signature metric, correctness-weighted

**Status:** Accepted (2026-09-02)

## Context

The project exists to answer one question: does a repository-intelligence system let an AI
coding agent understand and modify software correctly **while consuming less repository
context**? A benchmark needs one number that most directly expresses its thesis, or every report
becomes a wall of dimensions in which the reader picks a favourite.

## Decision

The signature metric is **Repository Tokens to Correct Solution**, counted only over tasks the
candidate got correct, and always printed beside the correctness rate.

Because it is the headline, it is specified adversarially (build spec §8.2):

- **Counted:** candidate output, raw source, test source, docs, config, search results, tool
  result JSON **envelopes and field names**, error strings, truncation notices.
- **Not counted** (system metrics instead): candidate-internal file reads, parser reads, hashing,
  index database reads.
- **Reported separately, excluded:** system prompt, task prompt, harness scaffolding.
- One tokenizer per run, identical for all candidates; cross-tokenizer comparison **rejected**.
- Count the **exact serialized string handed to the model**, envelope included — a candidate
  cannot hide payload in structure that escapes counting.
- **Cumulative per task, not per call.** A candidate answering "see `_client.py` lines 40–110"
  pays for the dereference the agent then performs. Pointer-shaped answers get no free lunch.
- Approximate counts are labelled `~` and never presented as exact.

## The perverse incentive, and the defence

Counting only correct tasks is right — tokens spent on a wrong answer are not efficiency — but
it rewards **failing fast**: a candidate that solves only two trivial tasks posts a beautiful
token number.

Three mandatory defences, detailed in `docs/scoring.md` §3:

1. The correctness rate is always printed beside the token figure. Never one without the other.
2. The published headline is `effective_context_efficiency = correctness × context_efficiency`,
   so a candidate that returns nothing scores **zero, not infinity**.
3. Cross-candidate token comparisons are computed over the **intersection** of tasks both solved
   correctly, and the report states that intersection's size.

## Consequences

- The metric is defensible against gaming through payload structure, pointer answers, and
  selective failure — the three obvious attacks.
- It is tokenizer-dependent in absolute terms; ratios within a run are the trustworthy reading
  (`docs/limitations.md` §7).
