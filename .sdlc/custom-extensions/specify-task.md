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
