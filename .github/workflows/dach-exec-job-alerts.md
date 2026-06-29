---
emoji: "📬"
description: Daily AI-ranked DACH executive job search digest posted as a GitHub Issue
engine:
  id: copilot
  model: gpt-4o-mini
max-turns: 1
max-ai-credits: 120
on:
  schedule:
    - cron: "15 6 * * *"
  workflow_dispatch:
permissions:
  contents: read
  metadata: read
  issues: read
  pull-requests: read
  copilot-requests: write
tools:
  bash: ["*"]
  edit: false
network:
  allowed:
    - defaults
    - "*.de"
    - "*.at"
    - "*.ch"
    - "*.com"
safe-outputs:
  create-issue:
    max: 1
    labels: [job-digest]
    title-prefix: "[DACH Jobs] "
  noop:
    report-as-issue: false
---

# DACH Executive Job Alert Digest

## Objective

Produce one daily issue with top DACH openings for:

- Engineering Manager
- CTO
- Head of Engineering
- Director of Engineering
- Head of Platform or Cloud

## Required Pipeline

1. Use bash only to run Python scripts.
2. Deterministic Python fetch + parse + dedupe.
3. Deterministic Python scoring.
4. Optional AI only for one short justification per role.
5. If AI errors (including 429), continue and publish deterministic output.

Never use `edit`, patch/diff output, `gh issue list`, `report_incomplete`, `missing_tool`, `missing_data`, or `create_report_incomplete_issue`.

## Sources

Use these URLs and continue on per-source failure:

1. https://www.stepstone.de/jobs/head-of-engineering/in-oesterreich
2. https://www.stepstone.de/jobs/head-of-engineering/in-deutschland
3. https://www.stepstone.at/jobs/cto
4. https://at.indeed.com/jobs?q=head+of+engineering+OR+CTO+OR+engineering+manager+OR+director+of+engineering+OR+head+of+platform&l=Austria+OR+Germany+OR+Switzerland
5. https://de.indeed.com/jobs?q=head+of+engineering+OR+CTO+OR+engineering+manager&l=Deutschland

For HTTP fetch use retries and browser UA.

## Data Files

- `/tmp/gh-aw/jobs_raw.json`
- `/tmp/gh-aw/jobs_deduped.json`
- `/tmp/gh-aw/jobs_ranked.json`

Normalized fields:
`title, company, location, source_url, application_url, publish_date, salary_text, language_hint`

Drop records without `title` or `company`.
Cap raw records to 40 to limit token usage.

## Scoring

Deterministic Python scores (1-5):

- location_score (Vienna=5; >1000km=1)
- company_size_score
- salary_score
- language_score (English-friendly high, German-required lower)

`final_score = clamp(1,5, round(0.35*location + 0.20*company + 0.30*salary + 0.15*language, 1))`

## Output Contract

Emit exactly one safe output item every run:

- preferred: `create_issue`
- fallback: `noop` only for unrecoverable internal failures

If listings exist: create issue title `Top 10 Exec Roles — YYYY-MM-DD`.
If zero listings: create issue title `No Listings Retrieved — YYYY-MM-DD` and include source diagnostics.

`create_issue` body must be markdown and include ranked table + brief details.
