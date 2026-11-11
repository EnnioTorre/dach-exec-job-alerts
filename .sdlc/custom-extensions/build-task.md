# build-task — Team Overrides

Applies to `build-task` (and any fix/commit flow). Takes precedence over the skill's defaults.

## Staging discipline

- **Stage explicitly.** Commit only the files you changed for this task — name each path in
  `git add <path> …`. Never run `git add .` / `git add -A`, and never add pre-existing untracked
  files (docs, configs, unrelated artifacts) into a feature commit.
  *Why: a fix commit once swept untracked `copilot-instructions.md` + `docs/infrastructure.md` into
  a geocoding PR and they had to be un-bundled — see PR #110.*
- Before committing, run `git status` and confirm every staged path belongs to this task. If an
  untracked file appears that you did not create for this task, leave it untracked.

## Tests track code

- **Commit tests only for code committed in the same PR.** Do not add tests for code that lives in a
  different (uncommitted) branch/PR. If a test needs code from another slice, that test belongs in
  that slice's PR.

## Testing rate-limited code

- **Neutralise `time.sleep` in the test fixture when unit-testing rate-limited code.** Code that
  enforces a request cadence (e.g. the ≥1 req/s Nominatim spacing in `locations`/`rank_jobs`) will
  incur real multi-second sleeps in tests that make several sequential calls. Patch it in the autouse
  fixture: `monkeypatch.setattr(<module>.time, "sleep", lambda *a, **k: None)`.
  *Why: `test_geocode_failure_degrades_to_none` made three sequential calls and slept ~2s for real
  until the fixture no-opped sleep — flagged by the test-reviewer on PR #120.*

## Graceful degradation in the pipeline

- **When consuming a field from a semi-trusted or AI-mutated artifact (e.g. `jobs_raw_ai.json`),
  validate its shape before subscripting and degrade gracefully — one malformed row must never abort
  the stage.** The `parsers never raise` convention extends to every stage that reads scraped/AI data:
  check type/length (e.g. `coords` is a 2-length numeric sequence) and fall back, don't crash.
  *Why: `_location_distance_score` subscripted `coords[0]/coords[1]` after only a truthiness check; a
  malformed `coords` from the AI-influenceable `jobs_raw_ai.json` would raise in the unwrapped scoring
  loop and abort the whole rank — flagged by the security-reviewer + gap-critic on PR #128.*

- **Place a new filter/drop before a target-count backfill, not after.** When a pipeline tops up a
  shortlist to a target count (e.g. the relaxed fallback to `RANK_TARGET_COUNT`), a drop added after it
  leaves the shortlist short with no backfill — and deterministically short on validation-loop retries.
  Run the drop before the backfill so it tops up from the already-filtered pool.
  *Why: the out-of-geo drop was wired after the relaxed-fallback top-up; dropping foreign rows left the
  shortlist below target — flagged by the gap-critic on PR #128.*
