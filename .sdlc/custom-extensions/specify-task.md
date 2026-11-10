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
