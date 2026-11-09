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
