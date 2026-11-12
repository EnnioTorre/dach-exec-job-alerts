# specify-task — Team Overrides

Applies to `specify-task`. Takes precedence over the skill's defaults. Also see
`.sdlc/custom-extensions/shared.md`.

## Security Surface verification

- **Every Security Surface "Verified by" entry must name a test that actually exercises the stated
  path.** A degrade / never-raise / error-path guarantee must be verified by a test that hits that
  path (resolver returns `None`, input rejected, exception raised) — never by a happy-path test that
  leaves the claim unexercised. If no such test exists in the plan, add one.
  *Why: the normalize_location spec cited a happy-path test (`test_normalize_uses_placeholder_for_unknown_parts`,
  which mocked a resolved record) as verifying the never-raise degrade; the actual all-`None`/filter-list
  path was never exercised — a false verification claim the gap-critic caught on PR #123.*

## Blast radius for shared data fields

- **When a story rewrites a field in a shared artifact (e.g. `location` in `jobs_raw.json`), enumerate
  every downstream producer and consumer of that field in the blast radius — including optional AI
  stages and second-pass/merge scripts — before claiming the end-to-end benefit.** Trace the real
  pipeline order from the workflow, not just the obvious reader.
  *Why: the scrape-normalize-pass spec's blast radius omitted `ai_pre_rank_enrich.py` (overwrites
  `location` when `GITHUB_TOKEN` is set) and `scrape_extra.py` (merges un-normalized rows into
  `jobs_raw.json` after the pass); the gap-critic caught that the digest benefit is only guaranteed on
  the AI-off path and that a later story must re-normalize — PR #125.*

## Externalised list content vs the PRD focus

- **When a spec moves a filter/role list into config and the PRD narrows that list's scope, reconcile
  the list CONTENT against the PRD's stated focus — do not mandate a blind "verbatim copy" of the old,
  broader list.** A verbatim copy carries entries the PRD now excludes, and the narrowing is easily
  left owned by no story. Spell out which entries stay, which go, and which story owns the narrowing.
  *Why: the position-filter-config spec told build-task to copy `ROLE_KEYWORDS` verbatim into
  `in_focus_roles`; the copy carried IC/engineer titles the PRD lists as "not in focus" (leadership
  only), the human reviewer rejected it, and the gap-critic found no story owned the narrowing —
  PR #132 (plan R2).*

## Rules added 2026-11-11

- **Before writing an ADR number or an env-var / knob name into a spec (or accepting one from the
  plan), verify it is not already used in the repo** — grep `docs/adrs/` for the ADR number and the
  codebase for the env var — and reconcile a collision (renumber / rename) before building.
  *Why: Story 4's plan assigned the pipeline ADR to ADR-0002 (already the fail-loud ADR) and the budget
  knob to LOCATION_GEOCODE_MAX (already used by `locations.py`); caught at specify-task and fixed via
  revise-plan R4 to ADR-0003 / POOL_GEOCODE_MAX before building.*

## Rules added 2026-11-12

## Blast radius must enumerate every inline construction of a changed config

- **When a change makes a config field REQUIRED (or adds required fields to a validated config
  loader), the spec's blast radius must enumerate EVERY inline construction of that config across the
  test suite — not just the loader's own fixture.** Grep the tests for the config's required keys
  (e.g. `individual_contributor_markers`, `domain_terms`) and list each inline dict that must gain the
  new field, or the build will break on a fixture the spec never named.
  *Why: Story 1's spec blast radius named only `test_position_filter_config._valid_config`; the
  second inline config in `test_rank_jobs.test_filter_reads_role_terms_from_config` also had to gain
  the 7 new required fields, which only surfaced as a failing test at build time (PR #161) and forced
  the recorded 4-file deviation.*

## One falsifiable test per widened validation branch

- **When a change widens N validation branches (e.g. adds a list to BOTH an overlap check AND a
  search-set check), the verification criteria must name one falsifiable test PER branch — not one
  test that happens to cover the first branch.** A single test over one branch leaves the other
  branch deletable with the suite still green.
  *Why: Story 1 added `off_focus_roles` to both the overlap cross-check and the search-set cross-check
  in `position_filter.load_position_filter`, but the plan's tests covered only the overlap branch; the
  search-set `excluded_lc` union was untested and could be deleted without failing any test — caught
  by the test-reviewer on PR #161 and fixed in `fa256a1`.*
