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

## Enforce the full declared invariant, not just the BAC-named case

- **When a config/validator's declared intent is "fail loud on invalid", enforce the FULL stated
  invariant on every field it applies to — not only the one the BAC happens to name.** If a comment or
  spec says "each list must be non-empty", the code must check every list, not just the first; a
  partial check leaves the other fields able to break the pipeline silently.
  *Why: `position_filter.load_position_filter` enforced non-empty on only `in_focus_roles` though the
  comment/spec said "each role-rule list"; an empty `it_signal_terms` would empty the digest and an
  empty `excluded_areas` would un-filter it — flagged by the code-reviewer + gap-critic on PR #132.*

## Rules added 2026-11-11

## Reconcile docs when a symbol moves or the architecture changes

- **When a symbol moves between modules, grep `docs/` for the old module and any `module.py:<line>`
  references and repoint them all in the same PR.** Line-number anchors rot on refactor; prefer symbol
  names. Check `building-blocks.md`, `data-flows.md`, `scenarios.md`, `INDEX.md`, and any ADR that
  names the symbol's location.
  *Why: `score_job` and the component scorers moved from `rank_jobs.py` to `scoring.py` on PR #140/#142
  but stale `rank_jobs.py:NNN` references lingered in building-blocks/data-flows/scenarios/INDEX and
  were re-flagged by the gap-critic across three review passes.*
- **When a change alters the architecture, update the affected docs (`patterns.md`, `data-flows.md`,
  `architecture.md`) AND the relevant ADR — its content and its `Status` (mark superseded/amended/
  accepted) — in the same PR.** A behavioural change that contradicts an ADR's Consequences or a
  documented invariant must reconcile both the prose and the ADR status, not just add a new ADR.
  *Why: PR #140's fail-loud rework made ADR-0001's "degrade to country constants / always ships a
  digest" Consequences false; the header was marked superseded but the Consequences paragraph and
  `patterns.md`/`architecture.md` stances were left stale and re-flagged on pass 3.*

## Test a new stage's main() and orchestration, not just the helpers

- **When adding a new stage / entry-point script, test `main()` and the orchestration — file I/O, the
  fail-loud `try/finally`, and any index-keyed stats — not only the pure helpers.** Pure-helper tests
  stay green while a broken `main()` / envelope / indexing path ships silently.
  *Why: PR #145's `pool_jobs.py` had the pool-building helpers tested but `main()` / `_build_envelope` /
  `_pool_stats` uncovered — the gap-critic flagged the orchestration + `jobs[i]` index path as unverified.*
- **When text-asserting a workflow/config file for the ABSENCE of a key (e.g. `continue-on-error`),
  strip comment lines first.** A comment that merely mentions the key is a false match.
  *Why: PR #146's workflow guard test for "no continue-on-error" first tripped on a comment that named
  it; fixed by filtering `#` lines before the assertion.*

## Rules added 2026-11-11 (Story 9 / PR #152)

## Aggregates over multiple key-sources

- **When a per-key aggregate/funnel mixes counts from two key-sources, compute totals AND rows over the
  UNION of their key-sets — never one set's keys only.** Iterating a single key-set silently drops the
  other set's members and desynchronises the totals, breaking a monotonic funnel (e.g. `passed <
  shortlisted`). Fall back to a neutral value for the missing dimension (`stats.get(src, 0)`).
  *Why: `create_issue._source_perf_funnel_section` summed `total_reached`/`total_passed` and iterated
  rows over `source_stats` keys only, while `shortlisted`/`Top 15` counted all sources; a pool/shortlist
  source absent from scrape stats (MCP/second-pass merge) was dropped and undercounted — a non-monotonic
  "honest" funnel flagged by the gap-critic on PR #152.*

## Legacy fallbacks behind a replacement

- **When you add a replacement path with a legacy fallback, isolate the legacy path in its own
  self-contained, clearly-labelled (`LEGACY` docstring) function — never inline it as a branch.** Keep
  its helpers local to it so a cleanup can delete the function and its single dispatch branch in one
  move once the replacement fully supersedes it.
  *Why: Story 9 first inlined the legacy deduped-unique table to minimise the diff; the human asked to
  separate it for a clean future removal — reworked into `_source_perf_legacy_section` on PR #152.*

## Test every new conditional branch

- **Every new conditional rendering/output branch ships with a test that exercises it.** A new `if`
  that emits a different shape (extra columns, alternate format) is a distinct output contract and must
  have its own fixture + assertion.
  *Why: the `with_ai` variant of the funnel table (extra AI Quality/Recommendation columns) shipped
  untested and could have broken undetected — flagged by the test-reviewer on PR #152.*
