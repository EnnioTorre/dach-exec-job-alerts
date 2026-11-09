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
