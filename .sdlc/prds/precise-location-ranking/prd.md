---
id: prd-precise-location-ranking
status: refined
created: 2026-11-06
updated: 2026-11-06
phase: start
author: "EnnioTorre"
---

# PRD: Precise Job Location for Distance-Based Ranking

## Problem / Opportunity

The digest ranks postings by distance from Vienna, but many jobs carry only a generic
country-level location. Site parsers hardcode or default the location (e.g.
`parse_karriere_at` → `"Austria"`, `parse_jobs_ch` → `"Switzerland"` in
[.github/scripts/scrape_jobs.py](../../.github/scripts/scrape_jobs.py)), and the ranker's
`_distance_from_vienna_km` ([.github/scripts/rank_jobs.py](../../.github/scripts/rank_jobs.py))
only computes a precise Haversine distance when the location string contains a **known city token**
(`_DACH_LOCATION_POINTS`). Otherwise it falls back to a flat country distance (AT→280 km,
DE→560 km, CH→700 km).

Consequences experienced today by the maintainer/reader:
- A Vienna role and a Bregenz role both labelled "Austria" receive the **same** country-fallback
  distance score (3.5), so the ranking cannot reflect true proximity.
- The digest's Location column is uninformative ("Austria") — the reader cannot visually judge
  relevance.

## Value Propositions

### User Value
The digest's Location column shows a specific place (city/region), so the reader can immediately
judge how relevant a posting is by where it actually is.

### Technical Value
Distance-based scoring operates on real coordinates for far more postings instead of a coarse
per-country constant, making the `location` component of the ranking formula meaningfully
discriminating rather than near-constant within a country.

## Goals

- For postings where a city is recoverable, the stored `location` is city-level (not a bare country)
  and the ranker computes distance from actual coordinates.
- Cities outside the current built-in coordinate table are resolved to coordinates via a geocoding
  service, so precision is not limited to a hand-maintained list.
- When a posting genuinely exposes no city, a bounded job-page enrichment attempt is made to recover
  one before falling back to the existing country-level distance.
- No ranking regression: the pipeline stays green, and postings that already had precise city
  locations keep their current scores — only postings that become newly precise change position.

## Constraints

- **No ranking regression** (as defined above): existing precise-location scores must be preserved;
  the full pipeline (`scrape → rank → test_scraper_loop`) must still pass its thresholds.
- Must honour existing project conventions: parsers never raise (return `[]`/degrade on error);
  all outbound HTTP goes through the pre-emptive per-domain rate limiter; SSL uses the configurable
  CA bundle (`SCRAPER_CA_BUNDLE`) for corporate/Zscaler proxies.
- Tests must not hit live sites or live geocoding — network must be mocked (per
  [pytest.ini](../../pytest.ini) / fixture conventions).
- Enrichment must be bounded with a per-run budget (mirroring the existing language-enrichment caps
  such as `SCRAPER_PAGE_LANG_ENRICH_MAX`) to protect runtime and provider quotas.
- Introduces a new external dependency (geocoding) — it must be optional/degrading so the
  deterministic pipeline still produces a digest if geocoding is unavailable.

## Anti-Goals

- Not redesigning the scoring formula weights (`0.35·location + 0.35·language + 0.30·relevance`) —
  only the accuracy of the `location` input changes.
- Not adding a persistent datastore; any geocoding cache stays within the existing file/`/tmp`
  model.

## Solution

**Selected approach — Parser city-extraction + bounded page-enrichment + online geocoding (Nominatim/OSM).**

Three layers, each degrading to the next:

1. **Parse-time city extraction.** Make each site parser capture the city the card actually shows
   instead of a hardcoded country. `parse_karriere_at` (which today hardcodes `"Austria"` and never
   inspects the card) gains the same location-element lookup that `parse_stepstone` and
   `parse_jobs_ch` already perform; all parsers pass the city string through unchanged.
2. **Bounded job-page enrichment.** When a row still has only a country, fetch its job page to
   recover a city — reusing the existing bounded/cached enrichment pattern (`_fetch_job_page_language`,
   per-URL cache, per-run cap env var) rather than inventing a new mechanism.
3. **Online geocoding of the resolved string.** Resolve the city/region string to coordinates via
   the public **OpenStreetMap Nominatim** REST API, then feed those coordinates into the existing
   Haversine distance-from-Vienna calculation. `_DACH_LOCATION_POINTS` becomes a fast-path cache
   checked first; Nominatim is called only on a miss, results are cached, and calls are rate-limited
   and bounded. Precision target is **city, with region/state accepted** as an intermediate before
   the country-level fallback. On any geocoding failure, timeout, or no-match, the ranker degrades to
   today's `_COUNTRY_DISTANCE_FALLBACK`.

**Scope:**
- Reuses: `_DACH_LOCATION_POINTS` / `_haversine_km` / `vienna_distance_score` (rank_jobs.py); the
  bounded-fetch enrichment pattern, per-domain `TokenBucket` rate limiter, and `SCRAPER_CA_BUNDLE`
  SSL handling (scrape_jobs.py); existing `requests` dependency (no new Python library).
- Requires new: city-extraction in `parse_karriere_at` (and pass-through audit of other parsers);
  a bounded location-enrichment fetch; a Nominatim geocoding client (timeout + cache + rate limit +
  graceful degradation); wiring geocoded coordinates into `_distance_from_vienna_km`.
- Integration points: new outbound HTTPS to `nominatim.openstreetmap.org`; the scrape and rank
  stages; the digest Location column in `create_issue.py`.

**External schemas/contracts:** Nominatim Search API (JSON) — https://nominatim.org/release-docs/latest/api/Search/ ·
usage policy https://operations.osmfoundation.org/policies/nominatim/

**Rationale:**
- Matches the user's start-prd choice (geocoding) and gives precision for *any* DACH city without a
  hand-maintained list.
- Reuses an already-proven bounded-fetch/caching pattern, keeping blast radius small and the
  pipeline's "optional stages never block" guarantee intact.
- Nominatim needs no API key/secret and is callable with the existing `requests` dependency, so no
  new library and no new credential is introduced.

**Estimated complexity:** M

## Business Acceptance Criteria

**BAC-1 — City shown instead of bare country:**
Given a job posting whose listing exposes a specific city (e.g. a Karriere.at role in Graz),
When the daily digest is produced,
Then that posting's Location reads the specific city/region, not a generic country name.

**BAC-2 — Proximity reflected in ranking for arbitrary cities:**
Given two postings in the same country but at clearly different distances from Vienna (e.g. one in
Vienna, one in Bregenz), each with a recoverable city,
When they are ranked,
Then the nearer posting receives a higher location contribution to its score than the farther one
(they are no longer scored identically).

**BAC-3 — Missing city is recovered within budget:**
Given a posting that initially exposes only a country, and the per-run recovery budget is not
exhausted,
When the pipeline processes it,
Then the system attempts to determine the posting's city before resorting to a country-level
estimate.

**BAC-4 — Region accepted when no city is available:**
Given a posting for which only a region/state can be determined (no city),
When it is ranked and shown,
Then the region is used for both the displayed Location and the distance estimate, in preference to
the country-level fallback.

**BAC-5 — Graceful degradation, run always completes:**
Given the geocoding service is unreachable, times out, or returns no match for a posting,
When the daily run executes,
Then that posting falls back to a country-level location estimate and the digest is still produced
successfully.

**BAC-6 — No regression for already-precise postings:**
Given postings whose city was already recognised before this change (e.g. Vienna, Berlin, Zurich),
When the new logic runs,
Then their ranking position relative to each other is unchanged from today's behaviour.

**BAC-7 — Digest remains reproducible:**
Given the same input postings on two runs,
When geocoding results are served from cache,
Then the produced ranking and displayed locations are identical across the two runs.

## Open Decisions

- [x] **Geocoding provider** — **Resolved: OpenStreetMap Nominatim** (free/OSS, no API key, callable
  via the existing `requests` dependency, fits the per-domain rate limiter). Google Geocoding and an
  offline GeoNames dataset were considered and rejected (paid/secret vs repo-size/staleness).
- [x] **Precision target** — **Resolved: city, with region/state accepted** as an intermediate before
  the country-level fallback.
- [ ] **Geocoding cache lifetime** — in-run only vs a `/tmp`-persisted cache per run vs a committed
  static cache seeded from past runs. *Assumption to confirm in `specify-task`:* start with an in-run
  cache (matching today's language-enrichment caches); revisit if Nominatim's 1 req/s policy proves
  too slow for a run. Technical detail — does not change any BAC.
- [ ] **Which stage geocodes** — scrape stage (coordinates written into `jobs_raw.json`) vs rank time
  inside `_distance_from_vienna_km`. *Assumption to confirm in `specify-task`:* geocode at rank time
  to keep the new dependency off the scrape hot-path and localised to the scorer. Technical detail.
- [ ] **Enrichment/geocoding budget & env-var names** — default per-run caps and `SCRAPER_*` names
  for the city-recovery fetch and the geocoding calls. Deferred to `specify-task` (technical).

## Dependencies

**Upstream:**
- **OpenStreetMap Nominatim public API** (`nominatim.openstreetmap.org`) — external, community-run,
  governed by the [OSMF Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
  (max ~1 req/s, required descriptive `User-Agent`, caching expected). No account/owner inside RBI.
- Must be reachable from the GitHub Actions runner over HTTPS (public internet — runners have no
  corporate proxy, confirmed by the SearXNG stage in the workflow).

**Downstream:**
- `create_issue.py` digest Location column — now shows city/region; no contract change, display only.
- `jobs_raw.json` / `jobs_ranked.json` row shape — `location` becomes more specific; if coordinates
  are persisted (stage decision above), a new optional field is added. No external consumer.

**Cross-repo / deployment:**
- **Project ADR required (CIG).** The CIG check returned `NO_COVERAGE` for Nominatim — not forbidden,
  but the team must author a project ADR documenting: rationale for the public endpoint vs a
  self-hosted instance, data classification of inputs (city/region strings only — no PII), resilience
  config (connect/read timeouts, graceful degradation), and caching strategy. Owner: this repo's team.
  Reference CIG ADRs #20 (Open-Source First), #1 (REST/HTTP), #412/#414 (resilience).
- No other repos involved — this is a single-repo pipeline; tests run in-repo via `pytest`
  ([tests.yml](../../.github/workflows/tests.yml)). No new secret (Nominatim needs no key).

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Nominatim rate limit (1 req/s) slows the run or returns 429 | M | M | Fast-path `_DACH_LOCATION_POINTS` cache first; cache all lookups; route through the per-domain `TokenBucket`; bounded per-run budget |
| Geocoding returns wrong/ambiguous coordinates (e.g. duplicate place names) | M | M | Bias queries to DACH (countrycodes=at,de,ch,it); accept region when city ambiguous; cap distance influence via existing score tiers |
| Nominatim unreachable from runner / outage | L | L | Graceful degradation to country fallback (BAC-5); run still completes |
| Ranking order shifts in ways the maintainer finds surprising | M | M | BAC-6 freezes already-precise postings' relative order; add regression tests over a fixed fixture set |
| OSM usage-policy breach (missing User-Agent / excessive calls) | L | M | Set descriptive `User-Agent`; enforce caching + rate limit; document in the project ADR |
| New outbound call in tests makes them flaky | L | H | Mock all geocoding/HTTP in tests (existing fixture convention); never hit live Nominatim |

## References

_No external links, tickets, or specs were provided with the request. Discovered during refinement:_

- [OpenStreetMap Nominatim — Search API docs](https://nominatim.org/release-docs/latest/api/Search/)
- [OSMF Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)

_Relevant in-repo anchors:_

- [docs/scenarios.md — Scenario 2 & ranking rules](../../docs/scenarios.md)
- [docs/building-blocks.md — Ranking capability](../../docs/building-blocks.md)
- [.github/scripts/rank_jobs.py — `_distance_from_vienna_km`, `vienna_distance_score`](../../.github/scripts/rank_jobs.py)
- [.github/scripts/scrape_jobs.py — `parse_karriere_at`, `parse_jobs_ch`, `_fetch_job_page_language` enrichment pattern](../../.github/scripts/scrape_jobs.py)

## Consistency Notes

- **Confirmed (code, rank_jobs.py):** `_distance_from_vienna_km` uses `_DACH_LOCATION_POINTS` for
  precise distance and `_COUNTRY_DISTANCE_FALLBACK` (AT 280 / DE 560 / CH 700 / IT 400) otherwise —
  this is the regression source the PRD targets.
- **Confirmed (code, scrape_jobs.py):** `parse_karriere_at` hardcodes `"Austria"` and `parse_jobs_ch`
  defaults `"Switzerland"`, so many rows never reach the city fast-path.
- **Confirmed (docs/scenarios.md):** ranking by Vienna distance is a core designed capability; this
  change strengthens it rather than conflicting with it. No overlapping/duplicate feature exists.
- **Confirmed (code):** a bounded page-fetch enrichment pattern already exists for language
  (`SCRAPER_PAGE_LANG_ENRICH_MAX`, `_fetch_job_page_language`) — the chosen "enrich from job page"
  approach reuses this established pattern.
- **Confirmed (code, scrape_jobs.py):** `parse_karriere_at` hardcodes `"Austria"` and does **not**
  read a location element from the card, whereas `parse_stepstone` and `parse_jobs_ch` both already
  read a `location_el`. The fix for Karriere.at is to add the lookup the siblings already have.
- **Confirmed (CIG check_compliance, 2026-11-06):** OpenStreetMap Nominatim returned `NO_COVERAGE`
  — not forbidden, but **a project ADR is required** (per the full explanation, not `action_required`).
  Surrounding stack is CIG-aligned: Python #225, GitHub Actions #153, REST/HTTP #1, resilience
  #412/#414, Open-Source First #20. No new secret or library introduced.
- **Assumed:** the cache-lifetime and geocoding-stage choices (in-run cache, geocode at rank time)
  are starting positions to confirm in `specify-task`; they are technical and change no BAC.
