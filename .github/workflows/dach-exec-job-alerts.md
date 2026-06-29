---
emoji: "📬"
description: Daily AI-ranked DACH executive job search digest posted as a GitHub Issue
engine:
  id: copilot
  model: gpt-4o
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

## Task

Run once per day and gather currently open roles in the DACH region for:

- Engineering Manager
- CTO
- Head of Engineering
- Director of Engineering
- Head of Platform
- Head of Cloud

Only include roles that are open at run time and have a valid source URL.

Use only shell commands in `bash` for data collection. Prefer `python3` scripts or `curl` for fetching pages and parsing results.

For every HTTP request, use a realistic browser user-agent and retries (for example `curl -L --retry 3 --retry-delay 2 --compressed -A "Mozilla/5.0 ..."`).

Fetch listings directly from these job board search URLs (fetch each one and extract relevant postings):

1. `https://www.stepstone.de/jobs/head-of-engineering/in-oesterreich` 
2. `https://www.stepstone.de/jobs/head-of-engineering/in-deutschland`
3. `https://www.stepstone.at/jobs/cto`
4. `https://at.indeed.com/jobs?q=head+of+engineering+OR+CTO+OR+engineering+manager+OR+director+of+engineering+OR+head+of+platform&l=Austria+OR+Germany+OR+Switzerland`
5. `https://de.indeed.com/jobs?q=head+of+engineering+OR+CTO+OR+engineering+manager&l=Deutschland`
6. `https://www.xing.com/jobs/search?keywords=head+of+engineering&location=Austria`
7. `https://www.xing.com/jobs/search?keywords=CTO+OR+engineering+manager&location=Germany`
8. `https://www.glassdoor.de/Job/osterreich-head-of-engineering-jobs-SRCH_IL.0,10_IN15_KO11,30.htm`

Fetch each URL, parse the HTML for job listings, and deduplicate by company+title.

If a source is blocked, rate-limited, or returns unusable HTML, skip it and continue with the remaining sources.

Reliability rules:

- Use `set -euo pipefail` in bash snippets.
- Do not use `gh issue list` or any `gh` search query.
- Do not try to detect existing digest issues. Always produce the current run digest.
- If fewer than 3 sources are reachable, still produce output from available sources instead of reporting incomplete.
- If zero listings are found, still create a digest issue that contains:
  - source health table (source URL, status, reason)
  - what was tried
  - suggested next source adjustments

## Ranking Method

For each posting, calculate four criterion scores from 1 to 5:

- `location_score`: 5 if in Vienna. Decrease with distance from Vienna. Score 1 for locations over 1000 km from Vienna.
- `company_size_score`: larger company gets higher score.
- `salary_score`: higher total compensation gets higher score. If salary is not published, estimate only when there is credible evidence and mark as estimated.
- `language_score`: English-friendly roles score higher. Roles requiring German score lower.

Calculate `final_score` in range 1 to 5 using weighted average:

- `0.35 * location_score`
- `0.20 * company_size_score`
- `0.30 * salary_score`
- `0.15 * language_score`

Round to one decimal and clamp to [1.0, 5.0].

## Output Requirements

Return the top 10 roles sorted by `final_score` descending.

Each role entry must include:

- title
- company
- location
- source_url
- application_url
- publish_date if known
- location_score
- company_size_score
- salary_score
- language_score
- final_score
- short justification

If fewer than 10 strong matches exist, send the best available and explicitly say how many were found.

## Delivery

Create exactly one digest issue per run using `create-issue` with:

- **title**: `[DACH Jobs] Top 10 Exec Roles — YYYY-MM-DD` (today's UTC date)
- **body**: a GitHub-flavored markdown report containing the ranked table and per-role details

Format the issue body as:

```
## DACH Executive Job Digest — YYYY-MM-DD

> Roles searched: Engineering Manager · CTO · Head of Engineering · Director of Engineering · Head of Platform · Head of Cloud
> Region: DACH (Germany, Austria, Switzerland)

### Top 10 Ranked Openings

| Rank | Role | Company | Location | Score | Salary | Language |
|------|------|---------|----------|-------|--------|----------|
| 1 | ... | ... | ... | 4.8 | ... | EN |
...

---

### Details

#### 1. [Role Title](application_url) — Company
- **Location**: city, country · location_score/5
- **Company size**: ... · company_size_score/5
- **Salary**: ... · salary_score/5
- **Language**: ... · language_score/5
- **Final score**: X.X / 5
- **Source**: [link](source_url) · Published: YYYY-MM-DD
- **Why**: short justification
```

If fewer than 10 credible listings are found, include all found and note the count at the top.

If no listings are found, create the issue with title:

- `[DACH Jobs] No Listings Retrieved — YYYY-MM-DD`

and include diagnostics as described above.

## Safe Outputs

- Use `create-issue` for the daily digest.
- Use `noop` only for unrecoverable internal tool failure (not for source-blocking cases).
