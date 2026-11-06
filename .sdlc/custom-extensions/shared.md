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

- **Smallest PR possible.** Prefer `S`-sized PRs; split aggressively. A PR should do one
  reviewable thing and be reviewable in well under 30 minutes. When a change grows, break it
  into stacked/sequential PRs rather than one large PR.
- Keep each PR independently deployable and green on its own.
- PR **title** follows Conventional Commits (see below).

## Commits

- **Conventional Commits** for every commit and PR title:
  `type(scope): description` — e.g. `feat(scrape): capture specific city/region`.
- Common types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`, `build`.
- Keep the subject line imperative and ≤ ~72 chars; put rationale and ticket refs in the body.
