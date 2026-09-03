# ADR-013 — Local-first before any remote infrastructure

**Status:** Accepted (2026-09-02)

## Context

Benchmarks accrete infrastructure: a results server, a dashboard, a queue, a scheduler, shared
storage. Each is defensible in isolation and each moves the project further from the thing that
matters — whether the numbers are trustworthy — while adding operational surface that must work
before anyone can measure anything.

## Decision

LeanBench runs on one laptop. No Docker, no Kubernetes, no message queues, no web dashboards, no
remote result store, no network dependency in the core loop.

Outputs are a terminal report and a JSON document. The JSON is shaped for consumption by an
optimization agent, which is the actual "dashboard" this project needs:

```json
{"score": 81.9, "noise_profile": "np_7f3a", "dimensions": {}, "weak_categories": [],
 "largest_token_costs": [], "regressions": [], "improvements": [], "inconclusive": [],
 "suite_health": {"informative_task_rate": 0.72, "infra_failure_rate": 0.01}}
```

The gate on that JSON is behavioural: an autonomous agent must be able to run
modify → evaluate → read JSON → hypothesize → modify, using **only** the JSON, without reading
the terminal report.

**HTML reports re-enter** when terminal + JSON are in daily use and someone asks. **Docker
sandboxing re-enters** when a candidate needs isolation the local process runtime cannot give.

## Consequences

- CI runs the full end-to-end path with no network and no API key, in under three minutes.
- Contributors need `git clone` and a Python environment. Nothing else.
- Multi-machine result aggregation is not possible today. Nobody has needed it; when someone
  does, immutable run directories (ADR-008) are already a clean unit to sync.
