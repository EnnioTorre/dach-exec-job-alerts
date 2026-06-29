---
emoji: "📬"
description: Daily AI-ranked DACH executive job search and email digest
engine: codex
on:
  schedule:
    - cron: "15 6 * * *"
  workflow_dispatch:
permissions:
  contents: read
  metadata: read
  issues: read
  pull-requests: read
tools:
  github:
    mode: gh-proxy
    toolsets: [default]
  web-search: {}
  web-fetch: {}
  bash: ["*"]
safe-outputs:
  jobs:
    send-email-report:
      description: Send the ranked job digest by email
      runs-on: ubuntu-latest
      output: "Email digest sent"
      inputs:
        to:
          description: Recipient email
          required: true
          type: string
        subject:
          description: Email subject
          required: true
          type: string
        body:
          description: Plain text body
          required: true
          type: string
        html_body:
          description: HTML body
          required: false
          type: string
      env:
        SMTP_SERVER: ${{ secrets.SMTP_SERVER }}
        SMTP_PORT: ${{ secrets.SMTP_PORT }}
        SMTP_USERNAME: ${{ secrets.SMTP_USERNAME }}
        SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
        SMTP_SECURE: ${{ secrets.SMTP_SECURE }}
      steps:
        - name: Extract send_email_report payload
          shell: bash
          run: |
            set -euo pipefail
            item=$(jq -cr '.items[] | select(.type == "send_email_report")' "$GH_AW_AGENT_OUTPUT" | tail -n 1)
            if [ -z "$item" ]; then
              echo "Missing send_email_report output from agent"
              exit 1
            fi
            echo "TO=$(jq -r '.to' <<<"$item")" >> "$GITHUB_ENV"
            echo "SUBJECT<<EOF" >> "$GITHUB_ENV"
            jq -r '.subject' <<<"$item" >> "$GITHUB_ENV"
            echo "EOF" >> "$GITHUB_ENV"
            echo "BODY<<EOF" >> "$GITHUB_ENV"
            jq -r '.body' <<<"$item" >> "$GITHUB_ENV"
            echo "EOF" >> "$GITHUB_ENV"
            echo "HTML_BODY<<EOF" >> "$GITHUB_ENV"
            jq -r '.html_body // ""' <<<"$item" >> "$GITHUB_ENV"
            echo "EOF" >> "$GITHUB_ENV"
        - name: Validate SMTP config
          shell: bash
          run: |
            set -euo pipefail
            test -n "${SMTP_SERVER:-}"
            test -n "${SMTP_PORT:-}"
            test -n "${SMTP_USERNAME:-}"
            test -n "${SMTP_PASSWORD:-}"
        - name: Send email
          uses: dawidd6/action-send-mail@v3
          with:
            server_address: ${{ env.SMTP_SERVER }}
            server_port: ${{ env.SMTP_PORT }}
            username: ${{ env.SMTP_USERNAME }}
            password: ${{ env.SMTP_PASSWORD }}
            secure: ${{ env.SMTP_SECURE || 'true' }}
            from: GitHub Agentic Workflow <${{ env.SMTP_USERNAME }}>
            to: ${{ env.TO }}
            subject: ${{ env.SUBJECT }}
            body: ${{ env.BODY }}
            html_body: ${{ env.HTML_BODY }}
---

# DACH Executive Job Alert Digest

## Task

Run once per day and search the public internet for currently open roles in the DACH region for:

- Engineering Manager
- CTO
- Head of Engineering
- Director of Engineering
- Head of Platform
- Head of Cloud

Only include roles that are open at run time and have a valid source URL.

Use web search and web fetch to gather listings from company career pages and reputable job boards.

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

Determine recipient email using this fallback order:

1. Repository variable `JOB_ALERT_RECIPIENT_EMAIL`
2. Secret `JOB_ALERT_RECIPIENT_EMAIL`
3. Public profile email of `${{ github.repository_owner }}` from GitHub API (only if non-empty)

If no recipient email is available, use `noop` with a short explanation.

When recipient is available, emit `send_email_report` exactly once with:

- `to`: resolved recipient
- `subject`: concise daily digest subject including current UTC date
- `body`: plain text digest with top 10
- `html_body`: optional HTML table version

## Safe Outputs

- Use `send_email_report` for email delivery.
- Use `noop` when no recipient can be resolved or no credible job listings are found.
