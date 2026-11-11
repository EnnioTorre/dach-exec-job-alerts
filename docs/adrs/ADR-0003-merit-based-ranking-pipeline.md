# ADR-0003: Merit-based multi-stage ranking pipeline (score → pool → rank)

**Status:** Accepted
**Date:** 2026-11-11
**PR:** Story 4 `score-and-pool-stage`
**Relates to:** [ADR-0001](ADR-0001-nominatim-geocoding.md) (geocoding), [ADR-0002](ADR-0002-no-synthetic-fallback-external-resources.md) (fail-loud)

## Context

The daily digest should surface IT-leadership roles drawn from the **largest** pools of such postings —
LinkedIn and Google — yet they added almost nothing to the shortlist. Root cause: `scrape_jobs.py`
passed only the **first 200 deduped rows in source order** (`all_jobs[:200]`) to the downstream stages.
When board sources (Karriere, StepStone, Arbeitnow) returned many rows, LinkedIn and Google rows were
cut **before** the ranker ever scored them. Whether a row was considered depended on source order and
other sources' volume, not on its quality. The cut also doubled as the (accidental) bound on the AI
stage's token use.

## Decision

Replace the order-based cap with a **merit-based multi-stage ranking pipeline**:

```
scrape (ALL deduped rows) → score + pool → AI enrich → final rank → publish
```

- **Remove the `[:200]` scrape cap.** Every deduped row is written to `jobs_raw.json`.
- **Add a score-and-pool stage** (`pool_jobs.py`): score *every* row with the shared deterministic
  scorer (`scoring.score_job`, extracted in Story 3), then build a bounded **candidate pool**:
  - **Per-family minimum (fairness).** Each under-represented family — `linkedin`, `google` — is
    guaranteed a small minimum (`POOL_FAMILY_MIN`) drawn from **its own top-scored rows**. `board` is
    exempt (it is never starved). The minimum **never alters a score** — it only decides representation.
  - **Merit fill.** The remainder of the pool (up to `POOL_TARGET_SIZE`) is filled strictly by global
    score, with a deterministic stable tie-break.
- The **pool is the bounded input** that keeps the AI stage's token use in check now that the cap is
  gone (Story 5 points AI at it).
- **Per-stage external-resource budget.** The score-all pass caps its Nominatim use via its own
  `POOL_GEOCODE_MAX` knob (falling back to `RANK_GEOCODE_MAX`), so it never doubles the ranker's volume.
- **Fail-loud ([ADR-0002](ADR-0002-no-synthetic-fallback-external-resources.md)).** The pool stage runs
  `score_job` on every row, so a Nominatim outage raises `GeocodeUnavailable` and fails the run rather
  than shipping a poisoned pool; the geocode cache is persisted first (`finally`) so a retry reuses
  coordinates. The workflow pool step is **not** `continue-on-error`.

## Consequences

**Positive:** a row's chance of reaching the shortlist depends on its **merit** (and a small fairness
floor for under-represented families), not on source order or other sources' volume. LinkedIn and
Google can finally contribute. One scoring formula governs the pool and the final rank (Story 3), so
there is no source bias. The AI input stays bounded by the pool.

**Negative / trade-offs:** an extra pipeline stage and file (`jobs_pool.json`); the score-all pass
geocodes more rows than the old capped set (bounded by `POOL_GEOCODE_MAX` + the shared persistent
cache, which the pool warms for the rank). Lifting the cap can shift the final shortlist size, so the
validation-loop thresholds (`TEST_MIN_RANKED`, …) may need re-tuning against a real run. The
per-family minimum sizes (`POOL_FAMILY_MIN`, `POOL_TARGET_SIZE`) are tuning parameters.

## References

- Spec: `.sdlc/tasks/score-and-pool-stage/spec.md` · PRD: `.sdlc/prds/it-leadership-search-yield/prd.md`
- Scorer: `.github/scripts/scoring.py` (Story 3) · Pool: `.github/scripts/pool_jobs.py`
- Supersedes the order-based `all_jobs[:200]` cap in `scrape_jobs.py`.
