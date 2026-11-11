# Shared Team Conventions

Applies to all `rbi-sdlc-fundamentals` skills. These are team-wide overrides and take
precedence over a skill's default behaviour.

## Delivery model

- **Trunk-based development.** `main` is the single long-lived branch and is always
  releasable. Branch off `main`, target `main`. No long-lived develop/release branches.
- **Continuous deployment.** Every merge to `main` can ship to production. Treat `main` as
  deployable at all times — never merge work that isn't releasable behind a flag or
  backward-compatible.

## Pull requests

- **Small by definition: ≤ 200 changed lines AND ≤ 3 files touched.** A PR over either threshold is
  too big — split it. (Generated/lock files and the `.sdlc/` task trail don't count toward the code
  thresholds, but the implementation + its tests do.)
- **One reviewable thing**, reviewable in well under 30 minutes, independently deployable, green on
  its own.
- **Tests track code.** A PR's committed tests cover only the code committed in that same PR — never
  tests for code that will land in another PR.
- PR **title** follows Conventional Commits (see below).

### Short-lived branches (branch < 1 day; decouple branch lifetime from feature size)

A feature bigger than one small PR ships as several short-lived branches, not one long branch. Pick
the pattern that fits:

- **Stacked PRs** — chain dependent branches (PR #2 builds on PR #1's branch); bottom merges first,
  the rest rebase onto `main` and cascade. Use for a change with a natural order. **After the base PR
  merges to `main`, retarget each dependent PR's base to `main` and rebase it** (`gh pr edit <n>
  --base main` + rebase) BEFORE merging it — otherwise merging it into the now-merged base branch lands
  the change on a dead branch, not `main`, and it is silently orphaned.
  *Why: PR #135 (relaxed-fill, BAC-5) stayed based on #134's branch; #134 merged to `main` first, then
  #135 merged into #134's branch — so the change never reached `main` and had to be recovered by a
  fresh cherry-pick PR (#137). Always confirm the change is on `origin/main` after merging a stack.*
- **Branch by abstraction** — for refactors/migrations a flag can't cover: add an interface → add the
  new impl behind it → switch callers (flag) → delete the old impl. Four small sequential PRs.
- **Ship behind a feature flag** — new features land on `main` behind an off-by-default flag; the
  branch closes as soon as the slice is reviewable, even if the feature is weeks from user-facing.
- **Split by execution path** — scaffolding (types/interfaces/empty bodies, behind a flag) → primary
  happy path → edge cases, each its own small PR.

**Anti-patterns (do not do):**
- Force-merging incomplete work into `main` with no flag/abstraction "to keep the branch short".
- "WIP" branches kept open for days — measure branch life from first commit, not from PR-open.
- **Merging a stacked PR into its base branch after that base has already merged to `main`** — the
  change lands on a dead branch, not `main`, and is silently lost (retarget to `main` first).


## Commits

- **Conventional Commits** for every commit and PR title:
  `type(scope): description` — e.g. `feat(scrape): capture specific city/region`.
- Common types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`, `build`.
- Keep the subject line imperative and ≤ ~72 chars; put rationale and ticket refs in the body.

## Documentation invariants

- **Reconcile invariants in-PR.** When a change contradicts a documented project invariant (e.g. the
  "Ranking is deterministic (no AI)" note), update that invariant's documentation in the *same* PR —
  never defer it to a later docs PR.
  *Why: a geocoding change made the ranker network-dependent; the "deterministic" claim was left
  unreconciled and the gap-critic flagged the doc/code mismatch across two review iterations — PR #110.*

## External-resource budgets

- **Each module/stage that calls a bounded external resource gets its own budget/rate-limit knob.**
  When a new consumer reuses another module's env var (e.g. `RANK_GEOCODE_MAX` for Nominatim), a single
  operator setting silently authorises N× the intended external-call volume. Give the new consumer its
  own env knob (e.g. `LOCATION_GEOCODE_MAX`), falling back to the shared one, so each stage is capped
  independently.
  *Why: `locations._geocode_address` first reused `RANK_GEOCODE_MAX`, so `=50` authorised up to 100
  Nominatim requests/run (50 rank + 50 locations) — flagged by the gap-critic on PR #120.*

## Rules added 2026-11-11

- **When a mid-review change turns a refactor into a behavioural redesign, split it into a new story
  rather than pivoting in-place.** A PR scoped as behaviour-preserving that acquires a behavioural
  change (new logic, removed fallback, changed output) should be rebuilt/split, not grown — it voids
  the prior review and the "one reviewable thing" rule.
  *Why: PR #140 started as a byte-identical scorer extraction, then absorbed a geolocation redesign +
  a repo-wide fail-loud policy in-place; it was flagged for scope every pass and finally had to be
  rebuilt with clean history (#142) and a follow-up story (S12).*
- **When adopting a cross-cutting policy (e.g. fail-loud / no-synthetic-fallback), trace it through the
  orchestration layers — workflow YAML, validation loop, `main()` — not just the unit that implements
  it.** A policy enforced in one function is not delivered if an outer `continue-on-error`, retry
  loop, or save-before-raise ordering swallows or undermines it.
  *Why: ADR-0002 fail-loud was implemented in `scoring.py`, but the Stage-3c `continue-on-error`
  workflow step and `test_scraper_loop.py`'s retry swallowed/amplified it end-to-end — caught by the
  gap-critic on PR #140 pass 3 and carved into Story 12.*
