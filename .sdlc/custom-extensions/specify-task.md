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
