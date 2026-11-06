# ADR-0001: OpenStreetMap Nominatim for job-location geocoding

**Status:** Accepted
**Date:** 2026-11-06
**PR:** #110

## Context

The daily digest ranks postings by distance from Vienna. Previously a posting that carried only a
country (e.g. "Austria") scored on a coarse per-country constant, so proximity within a country was
invisible. Story 2 of PRD `precise-location-ranking` resolves specific city/region strings to
coordinates so the location term of the ranking formula reflects true distance.

A geocoding capability is required. The CIG compliance check returned **`NO_COVERAGE`** — no CIG ADR
governs geocoding services, so the choice is not forbidden but must be documented in a project ADR
(the CIG tool's explanation requires this even though `action_required` read `none`). The surrounding
stack is CIG-aligned: Python scripting (ADR #225), GitHub Actions (ADR #153), REST/HTTPS integration
(ADR #1), backend resilience (ADR #412/#414), Open-Source First (ADR #20).

## Decision

Use the **public OpenStreetMap Nominatim** REST API (`nominatim.openstreetmap.org/search`) for
server-side geocoding in the ranking stage, called with the project's existing `requests` dependency
(no new library, no API key or secret).

- **Public endpoint vs self-hosted:** we use the public endpoint. The pipeline runs once daily on a
  GitHub runner and issues only a small, bounded number of lookups per run; a self-hosted Nominatim
  instance would add disproportionate operational cost for this volume. If usage grows or the public
  policy becomes a constraint, revisit self-hosting.
- **Data classification:** only city/region strings scraped from public job postings are sent — no
  PII, credentials, or financial data. No data-residency concern arises (cf. ADR #359, ADR #21),
  confirmed for this use case.
- **Resilience (ADR #412/#414):** explicit connect/read timeout `(3.05, 10)`; any non-200, empty, or
  malformed response and any request exception degrade to `None`, and the ranker falls back to the
  existing deterministic country constant. A geocoding outage can never fail the run.
- **Usage policy & caching:** a descriptive `User-Agent` (with a contact URL) is sent per the OSMF
  Nominatim usage policy; requests are spaced ≥1 req/s; results (including misses) are cached in
  `/tmp/jobs/geocode_cache.json` and reused across the run's rank/validation/re-rank passes to bound
  the aggregate request rate and keep scores reproducible within a run.
- **Rollout control:** geocoding is on by default and fully disableable via `RANK_GEOCODE=false`
  (instant rollback to pre-geocoding scoring); the per-run budget is `RANK_GEOCODE_MAX`.

## Consequences

**Positive:** precise, discriminating location scores for arbitrary DACH cities without a
hand-maintained coordinate list; no new dependency or secret; graceful degradation preserves the
pipeline's always-ships-a-digest guarantee.

**Negative / trade-offs:** the ranking stage now has a network dependency, so cross-day
reproducibility depends on the geocode cache and the external service; the project's "deterministic
ranking" guideline is reconciled accordingly in `copilot-instructions.md` (deterministic *given the
cache*, degrading to deterministic constants). The public Nominatim service has no SLA; if it is
unreachable the digest silently reverts to coarser country-level scoring for affected rows.

## References

- [OpenStreetMap Nominatim Search API](https://nominatim.org/release-docs/latest/api/Search/)
- [OSMF Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
- CIG ADRs: #20 (Open-Source First), #1 (REST/HTTP), #412/#414 (resilience), #225 (Python), #153 (GitHub Actions)
- PRD: `.sdlc/prds/precise-location-ranking/prd.md` · Spec: `.sdlc/tasks/geocode-vienna-distance/spec.md`
