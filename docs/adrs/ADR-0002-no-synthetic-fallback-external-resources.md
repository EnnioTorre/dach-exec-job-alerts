# ADR-0002: Fail loud on external-resource failures — no synthetic fallback values

**Status:** Accepted
**Date:** 2026-11-11
**PR:** #140
**Amends:** [ADR-0001](ADR-0001-nominatim-geocoding.md) (resilience stance)

## Context

The pipeline enriches and ranks postings using **external resources that return values the
pipeline then treats as data**: geolocation (OpenStreetMap Nominatim), AI/LLM APIs (GitHub Models),
and MCP servers. Historically these followed the repo's "degrade gracefully / never fail the run"
convention: on any failure, substitute a stand-in value and carry on — e.g. a failed geocode fell
back to a coarse per-country coordinate constant (ADR-0001), and a would-be-geocoded location that
could not be resolved scored on a neutral distance as if it had been placed.

The problem: **a fabricated stand-in value silently poisons downstream data.** A country-constant
coordinate, a neutral "we'll pretend this is average" score, or a guessed AI enrichment is
indistinguishable, downstream, from a real resolved value — so a transient outage of an external
service quietly corrupts the ranking/digest instead of surfacing the failure. The digest looks
authoritative while being wrong.

## Decision

For any call to an external resource whose **response is used as data** (geolocation, AI/LLM, MCP,
and similar), when the resource **fails to provide a response**:

1. **Retry** transient failures (timeout, connection error, HTTP 5xx, 429) with a small bounded
   number of attempts and spacing that honours the resource's rate policy.
2. **If it still fails after retries, raise and let the workflow fail.** Do **not** substitute a
   fabricated / guessed / neutral stand-in value that flows on as if it were a real answer. A failed
   run is recoverable (re-run, alert); poisoned data in a shipped digest is not.

Three explicit boundaries keep this from over-reaching:

- **A successful "no result" is a real answer, not a failure.** A resource that responds correctly
  with an empty/negative result (e.g. Nominatim `200` + `[]` for a place it does not know) has
  provided a response. Handle it honestly — record it as *unknown* (and treat "unknown" as its own
  value, never as a fabricated concrete one). Do not retry or fail the run for a legitimate miss.
- **Operator-disabled is a configured mode, not a failure.** A capability switched off on purpose
  (e.g. `RANK_GEOCODE=false`) is a chosen configuration; its absence degrades the affected dimension
  to *unknown*, and that must be documented behaviour, not a silent surprise.
- **Using an alternative *real* source is not a prohibited fallback.** Retrying the same need against
  a different live provider or source (e.g. the `google_proxy` provider chain, or backfilling from a
  second real job API) yields *real* data, not a fabricated value. Skipping a single dead scrape
  source and continuing with the other real sources is likewise fine — nothing synthetic is injected.

So the rule is specifically: **never invent a value to paper over a failed external call.** Retry,
use another real source, honestly mark unknown, or fail — in that order of preference.

## Consequences

**Positive:** the digest is trustworthy or absent — never silently poisoned by stand-in values. A
hard external-resource outage surfaces loudly (failed run / alert) instead of shipping corrupted
rankings. "Unknown" is explicit and auditable rather than disguised as an average.

**Negative / trade-offs:** the pipeline loses the unconditional "always ships a digest" guarantee for
hard external-resource outages — a sustained Nominatim/AI outage can now fail a run after retries.
This is the intended trade (correctness over availability for data-bearing calls). Retries add
latency and consume the per-resource budget; bound them.

**Supersedes in [ADR-0001](ADR-0001-nominatim-geocoding.md):** the bullet "any ... response ...
degrade to `None`, and the ranker falls back to the existing deterministic country constant. A
geocoding outage can never fail the run," and the matching Consequences text. Under this ADR a
geocoding **outage** (transport failure after retries) fails the run; a legitimate **miss**
(`200` + no match) or **operator-disabled** geocoding degrades the distance dimension to *unknown*
(neutral), with no fabricated coordinate. The static per-country coordinate constants are removed.

## Scope of application

- **Now (PR #140):** geolocation in `scoring.py` — retry the Nominatim call, raise on persistent
  failure, keep "miss"/"disabled" as honest *unknown*; the per-country coordinate fallback is gone.
- **Follow-up:** align the AI stages (`github_models.py` / `ai_*`) and any MCP callers to the same
  policy — they currently return `None` and skip; distinguishing a transient failure (retry → fail)
  from a legitimate empty answer should be applied there too. Tracked separately.

## References

- [ADR-0001](ADR-0001-nominatim-geocoding.md) — Nominatim geocoding (resilience stance amended here)
- Repo convention "parsers never raise" ([patterns.md](../patterns.md#error-handling-pattern--parsers-never-raise)) — unchanged for *scrape sources* (skipping a dead source and using the other real sources injects nothing synthetic); this ADR governs *data-bearing external-resource calls*.
- OSMF Nominatim usage policy — retry spacing must still honour ≥1 req/s.
