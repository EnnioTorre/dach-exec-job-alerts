## Source of truth

- **Ticket:** none — spec is the source of truth
- **Spec:** `.sdlc/tasks/scrape-normalize-pass/spec.md`
- **Plan:** `.sdlc/prds/standardized-job-location/plan.md` (Story 4, revised R1 — geocode-only)

## Changes

- `scrape_jobs.py`: add `_normalize_locations(jobs)` and wire it into `main()` after
  `_enrich_locations` — rewrites each written row's `location` to the standard `display` and attaches
  `place/region/country/coords/display`; never raises. Loads/saves the location cache around the pass.
- `.github/workflows/dach-exec-job-alerts.yml`: the existing geocode `actions/cache` step now also
  persists `/tmp/jobs/location_cache.json`, so the geocode budget warms across daily runs.
- `test_scrape_jobs.py`: 4 tests (`TestNormalizeLocations`).

## Verification

**Evidence pack:** `.sdlc/tasks/scrape-normalize-pass/evidence.md` (revision `162f4d5`)

| Check | Result |
|---|---|
| Lint | pass (ruff, changed files) |
| Build | pass (pytest imports `scrape_jobs`/`locations` cleanly) |
| Tests | `TestNormalizeLocations` 4 passed; full suite green |
| Coverage | no threshold enforced |
| Dependency CVE scan | not wired |
| Secret scan | gitleaks runs blocking in CI (`quality.yml`); no secrets added |

**Not verified:** SAST (not enabled), dependency CVE (not wired), coverage threshold (none),
secret scan locally (CI-only), the end-to-end scrape / `TEST_MIN_*` loop (CI validation stage; stats
are pre-pass so unaffected), and the workflow YAML change (runs only in GitHub Actions).

## Deviations from the spec

None. (Cache wiring + the explicit `display` field were specified and confirmed before build.)

## Review notes

- **First runtime-affecting story:** this rewrites `location` in `jobs_raw.json`. Additive/
  backward-compatible — `rank_jobs`/`create_issue` keep reading `location` (now cleaner); new fields
  wait for Stories 5–6.
- Normalizes the written slice (`all_jobs[:200]`) to not spend geocode budget on dropped rows; cold
  runs placeholder past the budget and warm via the `actions/cache`.
- **String-level only** (stakeholder decision): digest #121 role #4 → `—, —, —`; element-scoped
  real-location recovery is a later story.
- **Two-pass design — Story 5 is authoritative (gap-critic review note).** Pipeline is
  scrape → AI → rank → scrape_extra → rank. The AI stage (`GITHUB_TOKEN` set) may overwrite `location`
  and `scrape_extra` appends un-normalized rows after this pass; Story 5's rank-time re-normalization
  (BAC-8) standardizes both. Story 4's visible benefit holds on the AI-off path (digest #121). No code
  change here — these are carried to Story 5 (re-normalize all rows; skip-geocode the placeholder;
  consume `coords`).
