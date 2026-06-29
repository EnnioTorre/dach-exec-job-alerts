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
  github:
    mode: gh-proxy
    toolsets: [default]
  web-fetch: {}
  bash: ["*"]
network:
  allowed:
    - defaults
    - www.linkedin.com
    - www.stepstone.de
    - www.stepstone.at
    - www.xing.com
    - www.indeed.com
    - at.indeed.com
    - de.indeed.com
    - www.glassdoor.com
    - www.glassdoor.de
    - jobs.lever.co
    - boards.greenhouse.io
    - apply.workable.com
    - careers.smartrecruiters.com
safe-outputs:
  create-issue:
    max: 1
    labels: [job-digest]
    title-prefix: "[DACH Jobs] "
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

Before creating an issue, search for an existing open issue whose title starts with `[DACH Jobs]` and contains today's UTC date (format: `YYYY-MM-DD`). If one already exists, call `noop` — do not create a duplicate.

When no issue exists for today, use `create-issue` with:

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

## Safe Outputs

- Use `create-issue` for the daily digest.
- Use `noop` when a digest issue for today already exists, or when no credible job listings are found (explain briefly).
